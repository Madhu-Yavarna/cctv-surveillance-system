"""Geolocation and nearby police station lookup proxy."""
import json
import math
import urllib.parse
import urllib.request
from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..db import get_db
from ..models import PoliceStation
from ..security import current_user, optional_user

router = APIRouter(prefix="/api/geo", tags=["geo"])

USER_AGENT = "CCTV-Security-Platform/1.0 (admin@cctv.local)"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * R * math.asin(math.sqrt(a)), 2)


@router.get("/search")
def search_places(
    q: str = Query(..., min_length=2, description="Place, college, city, or address query"),
    limit: int = 8,
    user=Depends(optional_user)
):
    """Search for places, colleges, areas, landmarks via OpenStreetMap Nominatim."""
    encoded_q = urllib.parse.quote(q.strip())
    url = f"https://nominatim.openstreetmap.org/search?format=json&addressdetails=1&limit={limit}&q={encoded_q}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    results = []
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for item in data:
                display_name = item.get("display_name", "")
                name = item.get("name") or (display_name.split(",")[0] if display_name else "Location")
                clat = float(item["lat"])
                clon = float(item["lon"])
                results.append({
                    "display_name": display_name,
                    "name": name,
                    "lat": clat,
                    "lon": clon,
                    "latitude": clat,
                    "longitude": clon,
                    "type": item.get("type", "place"),
                    "category": item.get("class", "place")
                })
    except Exception:
        results = []
    return results


@router.get("/reverse")
def reverse_geocode(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    user=Depends(optional_user)
):
    """Reverse geocode latitude and longitude to a human-readable area name."""
    url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            addr = data.get("address", {})
            area = (
                addr.get("suburb") or addr.get("neighbourhood") or
                addr.get("city") or addr.get("town") or addr.get("county") or
                data.get("display_name", "").split(",")[0]
            )
            return {"area": area, "display_name": data.get("display_name", "")}
    except Exception:
        return {"area": f"{lat:.4f}, {lon:.4f}", "display_name": f"{lat:.4f}, {lon:.4f}"}


def _police_overpass(lat: float, lon: float, radius_m: int) -> List[dict]:
    query = f"""[out:json][timeout:5];
(
  node["amenity"="police"](around:{radius_m},{lat},{lon});
  way["amenity"="police"](around:{radius_m},{lat},{lon});
);
out center tags 15;"""
    endpoints = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter"
    ]
    for ep in endpoints:
        try:
            req = urllib.request.Request(ep, data=query.encode("utf-8"), headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=5) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                stations = []
                for el in res.get("elements", []):
                    t = el.get("tags", {})
                    clat = el.get("lat") or el.get("center", {}).get("lat")
                    clon = el.get("lon") or el.get("center", {}).get("lon")
                    if clat is None or clon is None:
                        continue
                    clat, clon = float(clat), float(clon)
                    name = t.get("name") or t.get("operator") or ""
                    addr_parts = [t.get("addr:street"), t.get("addr:suburb"), t.get("addr:city"), t.get("addr:district")]
                    addr = ", ".join([p for p in addr_parts if p]) or t.get("addr:full") or name or "Police Station"
                    if not name or name.strip().lower() in ["police station", "police", "police outpost"]:
                        town = t.get("addr:suburb") or t.get("addr:city") or t.get("addr:town") or t.get("addr:district")
                        if town:
                            name = f"{town} Police Station" if "police" not in town.lower() else town
                        else:
                            name = "Local Police Station"
                    stations.append({
                        "id": f"osm-{el.get('id')}",
                        "name": name,
                        "address": addr,
                        "lat": clat,
                        "lon": clon,
                        "latitude": clat,
                        "longitude": clon,
                        "phone": t.get("phone") or t.get("contact:phone") or "112 / 100",
                        "source": "osm"
                    })
                if stations:
                    return stations
        except Exception:
            continue
    return []


def _police_nominatim(lat: float, lon: float, radius_km: float) -> List[dict]:
    delta = max(0.08, min(0.35, radius_km / 111.0))
    left = lon - delta
    right = lon + delta
    top = lat + delta
    bottom = lat - delta
    url = f"https://nominatim.openstreetmap.org/search?format=json&addressdetails=1&limit=15&q=police+station&viewbox={left},{top},{right},{bottom}&bounded=1"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    stations = []
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for item in data:
                clat = float(item["lat"])
                clon = float(item["lon"])
                disp = item.get("display_name", "")
                name = item.get("name") or (disp.split(",")[0] if disp else "")
                if not name or name.strip().lower() in ["police station", "police", "police outpost"]:
                    parts = [p.strip() for p in disp.split(",") if p.strip()]
                    loc_name = ""
                    for p in parts[1:]:
                        if not any(char.isdigit() for char in p) and p.lower() not in ["india", "andhra pradesh", "telangana", "karnataka", "tamil nadu"]:
                            loc_name = p
                            break
                    name = f"{loc_name} Police Station" if loc_name and "police" not in loc_name.lower() else (loc_name or "Regional Police Station")
                addr = disp or f"{name}, Area Jurisdiction"
                stations.append({
                    "id": f"nom-{item.get('osm_id')}",
                    "name": name,
                    "address": addr,
                    "lat": clat,
                    "lon": clon,
                    "latitude": clat,
                    "longitude": clon,
                    "phone": "112 / 100",
                    "source": "nominatim"
                })
    except Exception:
        pass
    return stations


@router.get("/nearby-police")
def get_nearby_police(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    radius: float = Query(15, description="Radius in km (e.g. 15) or meters (e.g. 15000)"),
    db: Session = Depends(get_db),
    user=Depends(optional_user)
):
    """Find verified nearby police stations within the specified radius (default 15 km)."""
    # Normalize radius to meters and km
    if radius <= 100:
        radius_m = int(radius * 1000)
        radius_km = float(radius)
    else:
        radius_m = int(radius)
        radius_km = float(radius / 1000.0)

    raw_stations = []

    # 1. Query Overpass API
    overpass_stations = _police_overpass(lat, lon, radius_m)
    if overpass_stations:
        raw_stations.extend(overpass_stations)
    else:
        # 2. Fallback to Nominatim bounded search
        nom_stations = _police_nominatim(lat, lon, radius_km)
        raw_stations.extend(nom_stations)

    # 3. Include any registered active system police stations in database
    db_stations = db.scalars(select(PoliceStation).where(PoliceStation.active == True)).all()
    for ds in db_stations:
        if ds.lat is not None and ds.lon is not None:
            raw_stations.append({
                "id": f"db-{ds.id}",
                "name": ds.name if ds.name.lower().endswith("police") or "police" in ds.name.lower() else f"{ds.name} Police Station",
                "address": ds.address or f"Coordinates: {ds.lat}, {ds.lon}",
                "lat": ds.lat,
                "lon": ds.lon,
                "latitude": ds.lat,
                "longitude": ds.lon,
                "phone": ds.phone or "112 / 100",
                "source": "registered"
            })

    # Deduplicate by approximate coordinates (within 100 meters) and calculate actual Haversine distance
    deduped = []
    seen = []
    for s in raw_stations:
        clat, clon = s["lat"], s["lon"]
        dist = haversine_km(lat, lon, clat, clon)
        s["distance_km"] = dist

        is_dup = False
        for sl in seen:
            if haversine_km(clat, clon, sl["lat"], sl["lon"]) < 0.1:  # within 100m
                is_dup = True
                break
        if not is_dup:
            seen.append(s)
            deduped.append(s)

    deduped.sort(key=lambda x: x["distance_km"])

    # Filter within requested radius, but if fewer than 3 found, expand window up to 45km
    within_radius = [s for s in deduped if s["distance_km"] <= radius_km + 1.0]
    final_stations = within_radius if len(within_radius) >= 3 else deduped[:5]

    return {"stations": final_stations, "count": len(final_stations)}
