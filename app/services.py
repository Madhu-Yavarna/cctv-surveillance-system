"""Business logic shared by routers and background workers."""
import json, shutil, uuid
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session
from . import config, evidence as ev_lib, geo
from .models import *  # noqa
from .ai.risk import DEFAULTS as RISK_DEFAULTS

SETTING_DEFAULTS = {
    **{k: RISK_DEFAULTS[k] for k in ("risk_threshold", "min_duration", "cooldown", "min_consecutive", "night_enabled", "night_start", "night_end")},
    "pre_seconds": 5, "post_seconds": 5, "analysis_fps": 5,
    "system_name": "CCTV Surveillance System",
    "map_lat": 20.5937, "map_lon": 78.9629, "map_zoom": 5,   # neutral default (centre of India); set your own in Settings
}
NUMERIC_RANGE = {"risk_threshold": (0.1, 1.0), "min_duration": (1, 3600), "cooldown": (0, 86400), "min_consecutive": (1, 1000),
                 "pre_seconds": (0, 120), "post_seconds": (0, 120), "analysis_fps": (1, 15), "map_lat": (-90, 90), "map_lon": (-180, 180), "map_zoom": (1, 19)}

TRANSITIONS = {
    "PENDING": {"WORKING", "SOLVED"},
    "WORKING": {"PENDING", "SOLVED"},
    "SOLVED": {"PENDING", "WORKING"},
    "NEW": {"PENDING", "WORKING", "SOLVED", "ACKNOWLEDGED"},
    "ACKNOWLEDGED": {"PENDING", "WORKING", "SOLVED", "RESPONDING"},
    "RESPONDING": {"PENDING", "WORKING", "SOLVED", "RESOLVED"},
    "RESOLVED": {"PENDING", "WORKING", "SOLVED"}
}
POLICE_VISIBLE = ("PENDING", "WORKING", "SOLVED", "NEW", "ACKNOWLEDGED", "RESPONDING", "RESOLVED")
pending_clips: set[int] = set()   # incident ids whose live clip is still being recorded


def get_settings(db: Session) -> dict:
    s = dict(SETTING_DEFAULTS)
    for row in db.scalars(select(Setting)):
        if row.key in s:
            try: s[row.key] = json.loads(row.value)
            except Exception: pass
    return s


def save_settings(db: Session, data: dict) -> dict:
    for k, v in data.items():
        if k not in SETTING_DEFAULTS:
            continue
        if k in NUMERIC_RANGE:
            lo, hi = NUMERIC_RANGE[k]; v = float(v)
            if not lo <= v <= hi: raise ValueError(f"{k} must be between {lo} and {hi}")
            if k in ("min_consecutive", "analysis_fps", "map_zoom"): v = int(v)
        elif k in ("night_start", "night_end"):
            datetime.strptime(v, "%H:%M")
        elif k == "night_enabled": v = bool(v)
        elif k == "system_name": v = str(v)[:80]
        row = db.get(Setting, k)
        if row: row.value = json.dumps(v)
        else: db.add(Setting(key=k, value=json.dumps(v)))
    db.commit()
    return get_settings(db)


def audit(db: Session, user_id, action, detail=""):
    db.add(AuditLog(user_id=user_id, action=action, detail=str(detail)[:1000])); db.commit()


def cam_zone(cam):
    try: return json.loads(cam.zone_json) if cam and cam.zone_json else None
    except Exception: return None


def create_candidate(db: Session, ev: dict, frame, box, conf, *, source, cam=None, job=None, cfg=None):
    """Create an incident candidate + snapshot (+ clip for uploads). Returns incident."""
    cfg = cfg or get_settings(db)
    lat = lon = None; loc_text = None; loc_src = "unknown"
    if source == "live" and cam and geo.valid_coords(cam.lat, cam.lon):
        lat, lon, loc_text, loc_src = cam.lat, cam.lon, cam.location_text, "camera"
    elif job and geo.valid_coords(job.lat, job.lon):
        lat, lon, loc_src = job.lat, job.lon, job.loc_source
    inc = Incident(code=f"TMP-{uuid.uuid4().hex[:10]}", camera_id=cam.id if cam else None, job_id=job.id if job else None,
                   source=source, detected_at=datetime.now(), video_ts=ev["ts"] if source == "upload" else None,
                   track_id=ev["track_id"], risk_level=ev["level"], risk_score=ev["score"], duration=ev["duration"],
                   indicators_json=json.dumps(ev["indicators"]), summary=ev["summary"], status="CANDIDATE",
                   lat=lat, lon=lon, location_text=loc_text, loc_source=loc_src)
    db.add(inc); db.flush()
    inc.code = f"INC-{datetime.now().year}-{inc.id:04d}"
    d = ev_lib.incident_dir(inc.code)
    stamp = f"{inc.code}  {inc.detected_at:%d-%m-%Y %H:%M:%S}"
    ev_lib.save_snapshot(frame, d / "snapshot.jpg", box, f"person {conf:.2f}", stamp)
    db.add(Evidence(incident_id=inc.id, kind="snapshot", rel_path=f"{inc.code}/snapshot.jpg"))
    if source == "upload" and job:
        src = config.UPLOADS / job.path
        try:
            st0 = ev["start_ts"] if ev["start_ts"] is not None else ev["ts"]
            ev_lib.extract_clip(src, st0 - cfg["pre_seconds"], ev["ts"] + cfg["post_seconds"], d / "clip.mp4")
            db.add(Evidence(incident_id=inc.id, kind="clip", rel_path=f"{inc.code}/clip.mp4"))
        except Exception as e:
            inc.summary = (inc.summary or "") + f" [Clip extraction failed: {e}]"
    elif source == "live":
        pending_clips.add(inc.id)
    db.commit()
    return inc


def attach_live_clip(db: Session, incident_id: int, frames, fps):
    try:
        inc = db.get(Incident, incident_id)
        d = ev_lib.incident_dir(inc.code)
        ev_lib.write_clip(frames, fps, d / "clip.mp4")
        db.add(Evidence(incident_id=inc.id, kind="clip", rel_path=f"{inc.code}/clip.mp4")); db.commit()
    finally:
        pending_clips.discard(incident_id)


def confirm_incident(db: Session, inc: Incident, user, lat=None, lon=None, loc_text=None, manual=False):
    if inc.status != "CANDIDATE":
        raise ValueError("Only incident candidates can be confirmed")
    if inc.id in pending_clips:
        raise ValueError("Evidence clip is still being recorded; try again in a few seconds")
    if manual or lat is not None:
        if not geo.valid_coords(lat, lon): raise ValueError("Invalid latitude/longitude")
        inc.lat, inc.lon, inc.loc_source = lat, lon, "manual"
        if loc_text: inc.location_text = loc_text[:255]
    if not geo.valid_coords(inc.lat, inc.lon):
        raise ValueError("Location Unknown: select the incident location on the map before confirming")
    stations = list(db.scalars(select(PoliceStation).where(PoliceStation.active == True)))  # noqa
    st, km = geo.nearest_station(inc.lat, inc.lon, stations)
    if not st:
        raise ValueError("No police station is configured. Add one in Settings first")
    d = ev_lib.incident_dir(inc.code)
    if inc.source == "upload" and inc.job_id:                       # preserve original recording
        job = db.get(Job, inc.job_id); src = config.UPLOADS / job.path
        dst = d / ("original" + src.suffix.lower())
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)
            db.add(Evidence(incident_id=inc.id, kind="original", rel_path=f"{inc.code}/{dst.name}")); db.flush()
    files = []
    for e in list(inc.evidence):
        if e.kind == "manifest": continue
        p = ev_lib.safe_join(config.EVIDENCE, e.rel_path)
        if p.exists():
            e.sha256 = ev_lib.sha256_file(p); e.size = p.stat().st_size; files.append(p)
    meta = {"incident_id": inc.code, "detected_at": inc.detected_at.isoformat(), "video_ts": inc.video_ts, "duration_s": inc.duration,
            "camera": {"code": inc.camera.code, "name": inc.camera.name} if inc.camera else None,
            "location": {"lat": inc.lat, "lon": inc.lon, "text": inc.location_text, "source": inc.loc_source},
            "risk": {"level": inc.risk_level, "anomaly_score": inc.risk_score}, "station": st.name,
            "straight_line_km": km, "confirmed_by": user.username, "confirmed_at": datetime.utcnow().isoformat() + "Z",
            "source": inc.source, "note": "Live incidents have no separate original recording; the clip is the source file." if inc.source == "live" else ""}
    man = ev_lib.build_manifest(meta, files, d)
    mp = d / "manifest.json"; mp.write_text(json.dumps(man, indent=2))
    db.add(Evidence(incident_id=inc.id, kind="manifest", rel_path=f"{inc.code}/manifest.json", sha256=ev_lib.sha256_file(mp), size=mp.stat().st_size))
    inc.station_id, inc.station_km = st.id, km
    inc.status, inc.confirmed_by, inc.confirmed_at = "NEW", user.id, datetime.utcnow()
    n = Notification(incident_id=inc.id, station_id=st.id); db.add(n)
    db.commit(); audit(db, user.id, "incident_confirmed", f"{inc.code} -> {st.name} ({km} km straight-line)")
    return n


def create_and_dispatch_upload_incident(db: Session, job: Job, stats: dict, cam=None, cfg=None):
    """Automatically create, evidence-package, and dispatch a confirmed risk from an uploaded video to the Police Dashboard."""
    from .ws import hub
    cfg = cfg or get_settings(db)

    # Avoid duplicate incident for same job
    if job.incident_id:
        existing = db.get(Incident, job.incident_id)
        if existing:
            return existing

    # Determine location coordinates
    lat = lon = loc_text = None
    loc_src = "unknown"
    if cam and geo.valid_coords(cam.lat, cam.lon):
        lat, lon, loc_text, loc_src = cam.lat, cam.lon, cam.location_text, "camera"
    elif job and geo.valid_coords(job.lat, job.lon):
        lat, lon, loc_src = job.lat, job.lon, job.loc_source
    else:
        lat, lon, loc_text, loc_src = None, None, "Location Unavailable", "unavailable"

    stations = list(db.scalars(select(PoliceStation).where(PoliceStation.active == True)))
    if lat is not None and lon is not None and stations:
        st, km = geo.nearest_station(lat, lon, stations)
    else:
        st, km = (stations[0], None) if stations else (None, None)

    category = stats.get("category", "Suspicious or unusual activity")
    analysis_result = stats.get("analysis_result", "Risk Detected")
    risk_level = stats.get("risk_level", "HIGH_RISK_CANDIDATE")
    score = stats.get("risk_score", 0.85)
    peak_ts = stats.get("peak_ts", 0.0)
    dur = stats.get("video_seconds", 10.0)
    indicators = stats.get("indicators", {})
    summary = stats.get("summary", f"{category} identified during AI video analysis.")

    inc = Incident(
        code=f"TMP-{uuid.uuid4().hex[:10]}",
        camera_id=cam.id if cam else None,
        job_id=job.id,
        source="upload",
        detected_at=datetime.utcnow(),
        video_ts=peak_ts,
        risk_level=risk_level,
        risk_score=score,
        duration=dur,
        category=category,
        analysis_result=analysis_result,
        indicators_json=json.dumps(indicators),
        summary=summary,
        status="PENDING",
        lat=lat,
        lon=lon,
        location_text=loc_text,
        loc_source=loc_src,
        station_id=st.id if st else None,
        station_km=km if st else None,
        confirmed_at=datetime.utcnow()
    )
    db.add(inc)
    db.flush()
    inc.code = f"INC-{datetime.now().year}-{inc.id:04d}"
    d = ev_lib.incident_dir(inc.code)

    # 1. Snapshot evidence
    peak_frame = stats.get("peak_frame")
    box = stats.get("peak_box")
    conf = stats.get("peak_conf", score)
    stamp = f"{inc.code}  {category}  {inc.detected_at:%d-%m-%Y %H:%M:%S}"
    if peak_frame is not None:
        ev_lib.save_snapshot(peak_frame, d / "snapshot.jpg", box, f"{category} {conf:.2f}", stamp)
        db.add(Evidence(incident_id=inc.id, kind="snapshot", rel_path=f"{inc.code}/snapshot.jpg"))

    # 2. Clip evidence
    src_video = config.UPLOADS / job.path
    if src_video.exists():
        try:
            ev_lib.extract_clip(src_video, max(0, peak_ts - cfg["pre_seconds"]), peak_ts + cfg["post_seconds"], d / "clip.mp4")
            db.add(Evidence(incident_id=inc.id, kind="clip", rel_path=f"{inc.code}/clip.mp4"))
        except Exception as e:
            inc.summary = (inc.summary or "") + f" [Clip notice: {e}]"

    # 3. Hash evidence with SHA-256 and build manifest
    files = []
    for e in list(inc.evidence):
        if e.kind == "manifest": continue
        p = ev_lib.safe_join(config.EVIDENCE, e.rel_path)
        if p.exists():
            e.sha256 = ev_lib.sha256_file(p)
            e.size = p.stat().st_size
            e.verify_result = "match"
            files.append(p)

    meta = {
        "incident_id": inc.code,
        "category": category,
        "analysis_result": analysis_result,
        "detected_at": inc.detected_at.isoformat(),
        "video_ts": peak_ts,
        "duration_s": dur,
        "source": "upload",
        "video_name": job.filename,
        "location": {"lat": inc.lat, "lon": inc.lon, "text": inc.location_text},
        "risk": {"level": risk_level, "score": score},
        "station": st.name if st else "Unassigned",
        "station_km": km if st else 0.0,
        "confirmed_at": inc.confirmed_at.isoformat() + "Z"
    }
    man = ev_lib.build_manifest(meta, files, d)
    mp = d / "manifest.json"
    mp.write_text(json.dumps(man, indent=2))
    db.add(Evidence(incident_id=inc.id, kind="manifest", rel_path=f"{inc.code}/manifest.json", sha256=ev_lib.sha256_file(mp), size=mp.stat().st_size, verify_result="match"))

    # Link to job
    job.incident_id = inc.id
    db.flush()

    # 4. Create Notification and dispatch via WebSockets
    if st:
        n = Notification(incident_id=inc.id, station_id=st.id)
        db.add(n)
        db.commit()
        # Live push to open Police Dashboard
        hub.sync_to_station(st.id, notif_payload(db, n))
    else:
        db.commit()

    # Live push to Admin Dashboard
    hub.sync_to_admins({"type": "incident", "incident": inc_dict(inc, full=True)})
    audit(db, None, "incident_auto_dispatched", f"{inc.code} [{category}] -> {st.name if st else 'None'}")
    return inc


def ev_dict(e: Evidence):
    return {"id": e.id, "kind": e.kind, "sha256": e.sha256, "size": e.size, "verify_result": e.verify_result,
            "verified_at": e.verified_at.isoformat() if e.verified_at else None, "name": e.rel_path.split("/")[-1],
            "url": f"/api/evidence/{e.id}/file"}


def inc_dict(i: Incident, full=False):
    is_risk = (i.risk_score >= 0.5 or i.analysis_result == "Risk Detected" or i.risk_level in ("HIGH_RISK_CANDIDATE", "HIGH_RISK", "CRITICAL"))
    score_pct = round(i.risk_score * 100 if i.risk_score <= 1.0 else i.risk_score)
    snap_ev = next((e for e in i.evidence if e.kind == "snapshot"), None)
    clip_ev = next((e for e in i.evidence if e.kind == "clip"), None)
    d = {"id": i.id, "code": i.code, "incident_id": i.code, "detected_at": i.detected_at.isoformat(), "video_ts": i.video_ts, "source": i.source,
         "time_str": i.detected_at.strftime("%H:%M:%S"), "date_str": i.detected_at.strftime("%d %b %Y"),
         "camera": {"id": i.camera.id, "code": i.camera.code, "name": i.camera.name} if i.camera else None,
         "camera_name": i.camera.name if i.camera else (i.job.filename if i.job else "Uploaded Video"),
         "location": i.location_text or "Location Unavailable",
         "location_text": i.location_text or "Location Unavailable", "lat": i.lat, "lon": i.lon,
         "latitude": i.lat, "longitude": i.lon, "loc_source": i.loc_source,
         "risk_level": i.risk_level, "risk_score": i.risk_score, "risk_percentage": score_pct,
         "risk_category": "RISK" if is_risk else "NO RISK", "duration": i.duration, "activity_duration": i.duration, "status": i.status,
         "category": i.category or "Suspicious or unusual activity",
         "analysis_result": i.analysis_result or ("Risk Detected" if is_risk else "No Risk Detected"),
         "video_name": i.job.filename if i.job else (i.camera.name if i.camera else "Uploaded Video"),
         "station": {"id": i.station.id, "name": i.station.name, "lat": i.station.lat, "lon": i.station.lon, "phone": i.station.phone} if i.station else None,
         "station_name": i.station.name if i.station else "Central Police Station",
         "station_km": i.station_km, "distance_km": i.station_km,
         "snapshot_url": f"/api/evidence/{snap_ev.id}/file" if snap_ev else None,
         "video_clip_url": f"/api/evidence/{clip_ev.id}/file" if clip_ev else None,
         "snapshot_hash": snap_ev.sha256 if (snap_ev and snap_ev.sha256) else "N/A",
         "is_verified": snap_ev.verify_result == "match" if snap_ev else False}
    if full:
        d.update(summary=i.summary, indicators=json.loads(i.indicators_json or "{}"), remarks=i.remarks, track_id=i.track_id,
                 confirmed_at=i.confirmed_at.isoformat() + "Z" if i.confirmed_at else None,
                 camera_lat=i.camera.lat if i.camera else None, camera_lon=i.camera.lon if i.camera else None,
                 evidence=[ev_dict(e) for e in i.evidence], clip_pending=i.id in pending_clips)
    return d


def for_police(d: dict) -> dict:
    """Police never see the preserved original recording (Admin-only)."""
    d["evidence"] = [e for e in d.get("evidence", []) if e["kind"] != "original"]
    return d


def notif_payload(db, n: Notification):
    inc = db.get(Incident, n.incident_id)
    db.refresh(inc)
    return {"type": "incident", "notification_id": n.id, "incident": for_police(inc_dict(inc, full=True))}
