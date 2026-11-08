"""Haversine distance, nearest-station lookup, GPS metadata from video."""
import math, re, shutil, subprocess, json


def haversine_km(lat1, lon1, lat2, lon2):
    """Straight-line (great-circle) distance in km - NOT road distance."""
    R = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def nearest_station(lat, lon, stations):
    """stations: iterable of objects with .lat .lon .active. Returns (station, km) or (None, None)."""
    best, bd = None, None
    for s in stations:
        if not getattr(s, "active", True) or s.lat is None or s.lon is None:
            continue
        d = haversine_km(lat, lon, s.lat, s.lon)
        if bd is None or d < bd:
            best, bd = s, d
    return best, (round(bd, 3) if bd is not None else None)


def valid_coords(lat, lon):
    return lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180


_ISO = re.compile(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)")


def parse_iso6709(s):
    m = _ISO.match(s or "")
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    return (lat, lon) if valid_coords(lat, lon) else None


def video_gps(path):
    """Genuine GPS tag embedded in the video container (needs ffprobe). None if absent."""
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                             capture_output=True, text=True, timeout=20).stdout
        j = json.loads(out)
        tags = dict(j.get("format", {}).get("tags", {}))
        for s in j.get("streams", []):
            tags.update(s.get("tags", {}))
        for k, v in tags.items():
            if k.lower() in ("location", "com.apple.quicktime.location.iso6709"):
                g = parse_iso6709(v)
                if g:
                    return g
    except Exception:
        pass
    return None
