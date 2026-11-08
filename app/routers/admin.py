import json, re, time, uuid
from datetime import datetime
from pathlib import Path
import cv2
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse, Response, FileResponse
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from ..db import get_db
from ..models import *  # noqa
from ..security import require_admin, current_user
from .. import services as S, config, geo, runtime
from ..ai import pipeline

router = APIRouter(prefix="/api", tags=["admin"], dependencies=[Depends(current_user)])


# ---------------- summary / map / settings
@router.get("/summary")
@router.get("/admin/stats")
def summary(db: Session = Depends(get_db)):
    cams = db.scalars(select(Camera)).all()
    inc = db.scalars(select(Incident).where(Incident.status != "REJECTED")).all()
    vids = db.scalars(select(Job).where(Job.status == "done")).all()
    pending = [i for i in inc if i.status in ("PENDING", "NEW", "CANDIDATE")]
    return {
        "total_cameras": len(cams),
        "online": sum(c.status == "online" for c in cams),
        "offline": sum(c.status != "online" for c in cams),
        "total_incidents": len(inc),
        "videos_analyzed": len(vids),
        "pending_active_incidents": len(pending),
        "high_risk": sum(i.risk_level == "HIGH_RISK_CANDIDATE" for i in inc),
        "awaiting_review": sum(i.status == "CANDIDATE" for i in inc),
        "system": "Active" if config.MODEL_PATH.exists() else "Model missing",
        "model_loaded": config.MODEL_PATH.exists()
    }


@router.get("/map")
def map_data(db: Session = Depends(get_db)):
    return {"cameras": [{"id": c.id, "code": c.code, "name": c.name, "lat": c.lat, "lon": c.lon, "status": c.status}
                        for c in db.scalars(select(Camera)) if geo.valid_coords(c.lat, c.lon)],
            "incidents": [S.inc_dict(i) for i in db.scalars(select(Incident).where(Incident.status.notin_(["CANDIDATE", "REJECTED"]))) if geo.valid_coords(i.lat, i.lon)],
            "stations": [{"id": s.id, "name": s.name, "lat": s.lat, "lon": s.lon, "phone": s.phone, "address": s.address} for s in db.scalars(select(PoliceStation).where(PoliceStation.active == True))],  # noqa
            "settings": {k: S.get_settings(db)[k] for k in ("map_lat", "map_lon", "map_zoom")}}


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    return S.get_settings(db)


@router.put("/settings")
def put_settings(body: dict, u=Depends(require_admin), db: Session = Depends(get_db)):
    try: s = S.save_settings(db, body)
    except (ValueError, TypeError) as e: raise HTTPException(400, str(e))
    S.audit(db, u.id, "settings_updated", json.dumps(body)); return s


# ---------------- police stations
class StationIn(BaseModel):
    name: str; lat: float; lon: float; address: str | None = None; phone: str | None = None; active: bool = True


@router.get("/stations")
def stations(db: Session = Depends(get_db)):
    return [{"id": s.id, "name": s.name, "lat": s.lat, "lon": s.lon, "address": s.address, "phone": s.phone, "active": s.active}
            for s in db.scalars(select(PoliceStation))]


@router.post("/stations")
def add_station(b: StationIn, u=Depends(require_admin), db: Session = Depends(get_db)):
    if not geo.valid_coords(b.lat, b.lon): raise HTTPException(400, "Invalid coordinates")
    if db.scalar(select(PoliceStation).where(PoliceStation.name == b.name)): raise HTTPException(409, "Station name exists")
    s = PoliceStation(**b.model_dump()); db.add(s); db.commit(); S.audit(db, u.id, "station_added", b.name); return {"id": s.id}


@router.put("/stations/{sid}")
def edit_station(sid: int, b: StationIn, u=Depends(require_admin), db: Session = Depends(get_db)):
    s = db.get(PoliceStation, sid)
    if not s: raise HTTPException(404)
    if not geo.valid_coords(b.lat, b.lon): raise HTTPException(400, "Invalid coordinates")
    for k, v in b.model_dump().items(): setattr(s, k, v)
    db.commit(); S.audit(db, u.id, "station_edited", s.name); return {"ok": True}


@router.delete("/stations/{sid}")
def del_station(sid: int, u=Depends(require_admin), db: Session = Depends(get_db)):
    s = db.get(PoliceStation, sid)
    if not s: raise HTTPException(404)
    if db.scalar(select(func.count()).select_from(Incident).where(Incident.station_id == sid)) or db.scalar(select(func.count()).select_from(User).where(User.station_id == sid)):
        s.active = False; db.commit(); return {"ok": True, "note": "Station has incidents/officers; it was deactivated instead of deleted"}
    db.delete(s); db.commit(); S.audit(db, u.id, "station_deleted", s.name); return {"ok": True}


# ---------------- cameras
class CamIn(BaseModel):
    name: str
    code: str | None = None
    camera_id: str | None = None
    rtsp_url: str
    location_text: str | None = None
    location: str | None = None
    lat: float | None = None
    latitude: float | None = None
    lon: float | None = None
    longitude: float | None = None
    zone: list[list[float]] | None = None


def _check_url(url):
    u = url.strip()
    if not (u.isdigit() or re.match(r"^(rtsp|rtsps|http|https)://", u, re.I)):
        raise HTTPException(400, "Stream URL must start with rtsp://, rtsps://, http(s)://, or be a webcam index like 0")
    return u


def _zone_ok(z):
    if z is None: return None
    if len(z) < 3 or any(len(p) != 2 or not all(0 <= v <= 1 for v in p) for p in z): raise HTTPException(400, "Zone needs >=3 points with 0..1 coordinates")
    return json.dumps(z)


def cam_dict(c):
    r = runtime.runners.get(c.id)
    live = r.state if r and r.is_alive() else None
    return {"id": c.id, "code": c.code, "camera_id": c.code, "name": c.name, "rtsp_url": c.rtsp_url,
            "location": c.location_text or "Location Unavailable", "location_text": c.location_text,
            "lat": c.lat, "lon": c.lon, "latitude": c.lat, "longitude": c.lon,
            "zone": S.cam_zone(c), "status": c.status, "last_checked": c.last_checked.isoformat() + "Z" if c.last_checked else None,
            "last_error": c.last_error, "running": bool(live),
            "live": ({"status": live["status"], "analysis_fps": live["analysis_fps"], "resolution": live["resolution"], "risk": live.get("risk")} if live else None)}


@router.get("/cameras")
def cameras(db: Session = Depends(get_db)):
    return [cam_dict(c) for c in db.scalars(select(Camera).order_by(Camera.id))]


@router.post("/cameras")
def add_camera(b: CamIn, u=Depends(require_admin), db: Session = Depends(get_db)):
    if db.scalar(select(func.count()).select_from(Camera)) >= 2:
        raise HTTPException(400, "Maximum limit reached: The system supports a maximum of 2 CCTV camera connections.")
    cam_code = (b.code or b.camera_id or f"CAM-{uuid.uuid4().hex[:6].upper()}").strip()
    loc_text = b.location_text or b.location
    c_lat = b.lat if b.lat is not None else b.latitude
    c_lon = b.lon if b.lon is not None else b.longitude
    if db.scalar(select(Camera).where(Camera.code == cam_code)): raise HTTPException(409, "Camera ID already exists")

    # Automatic real-world geocoding fallback if coordinates were not supplied
    if (c_lat is None or c_lon is None) and loc_text:
        import urllib.request, urllib.parse, json
        try:
            q_enc = urllib.parse.quote(loc_text.strip())
            url = f"https://nominatim.openstreetmap.org/search?format=json&limit=1&q={q_enc}"
            req = urllib.request.Request(url, headers={"User-Agent": "CCTV-Security-Platform/1.0 (admin@cctv.local)"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data:
                    c_lat = float(data[0]["lat"])
                    c_lon = float(data[0]["lon"])
        except Exception:
            pass

    if c_lat is not None and not geo.valid_coords(c_lat, c_lon): raise HTTPException(400, "Invalid coordinates")
    c = Camera(name=b.name, code=cam_code, rtsp_url=_check_url(b.rtsp_url), location_text=loc_text, lat=c_lat, lon=c_lon, zone_json=_zone_ok(b.zone))
    db.add(c); db.commit(); S.audit(db, u.id, "camera_added", cam_code); return cam_dict(c)


@router.put("/cameras/{cid}")
def edit_camera(cid: int, b: CamIn, u=Depends(require_admin), db: Session = Depends(get_db)):
    c = db.get(Camera, cid)
    if not c: raise HTTPException(404)
    if runtime.runners.get(cid) and runtime.runners[cid].is_alive(): raise HTTPException(409, "Stop the camera before editing it")
    loc_text = b.location_text or b.location or c.location_text
    c_lat = b.lat if b.lat is not None else (b.latitude if b.latitude is not None else c.lat)
    c_lon = b.lon if b.lon is not None else (b.longitude if b.longitude is not None else c.lon)

    if (c_lat is None or c_lon is None) and loc_text:
        import urllib.request, urllib.parse, json
        try:
            q_enc = urllib.parse.quote(loc_text.strip())
            url = f"https://nominatim.openstreetmap.org/search?format=json&limit=1&q={q_enc}"
            req = urllib.request.Request(url, headers={"User-Agent": "CCTV-Security-Platform/1.0 (admin@cctv.local)"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data:
                    c_lat = float(data[0]["lat"])
                    c_lon = float(data[0]["lon"])
        except Exception:
            pass

    c.name, c.rtsp_url, c.location_text, c.lat, c.lon = b.name, _check_url(b.rtsp_url), loc_text, c_lat, c_lon
    if b.zone is not None: c.zone_json = _zone_ok(b.zone)
    db.commit(); S.audit(db, u.id, "camera_edited", c.code); return cam_dict(c)


@router.delete("/cameras/{cid}")
def del_camera(cid: int, u=Depends(current_user), db: Session = Depends(get_db)):
    c = db.get(Camera, cid)
    if not c: raise HTTPException(404, "Camera not found")
    runtime.stop_live(cid)
    for inc in db.scalars(select(Incident).where(Incident.camera_id == cid)):
        inc.camera_id = None
    code, name = c.code, c.name
    db.delete(c)
    db.commit()
    S.audit(db, u.id, "camera_deleted", code)
    return {"ok": True, "message": f"Camera '{name}' ({code}) removed successfully"}


class UrlIn(BaseModel):
    rtsp_url: str


@router.post("/cameras/test")
def test_new(b: UrlIn):
    r = pipeline.test_stream(_check_url(b.rtsp_url)); return {"ok": bool(r.get("ok")), **r}


@router.post("/cameras/{cid}/test")
def test_cam(cid: int, db: Session = Depends(get_db)):
    c = db.get(Camera, cid)
    if not c: raise HTTPException(404)
    r = pipeline.test_stream(c.rtsp_url)
    if not (runtime.runners.get(cid) and runtime.runners[cid].is_alive()):
        c.status = "online" if r.get("ok") else "offline"; c.last_error = r.get("err"); c.last_checked = datetime.utcnow(); db.commit()
    return {"ok": bool(r.get("ok")), **r}


@router.post("/cameras/{cid}/start")
def start_cam(cid: int, u=Depends(require_admin), db: Session = Depends(get_db)):
    c = db.get(Camera, cid)
    if not c: raise HTTPException(404)
    if not config.MODEL_PATH.exists(): raise HTTPException(503, "YOLO model missing. Run: python scripts/export_model.py")
    r = pipeline.test_stream(c.rtsp_url)
    if not r.get("ok"):
        c.status = "offline"; c.last_error = r.get("err"); c.last_checked = datetime.utcnow(); db.commit()
        raise HTTPException(502, f"Stream connection failed: {r.get('err')}")
    c.status = "online"; c.last_error = None; c.last_checked = datetime.utcnow(); db.commit()
    if len([r for r in runtime.runners.values() if r.is_alive()]) >= 2: raise HTTPException(409, "Max 2 concurrent cameras on this hardware profile")
    try: runtime.start_live(cid)
    except Exception as e: raise HTTPException(500, f"Could not start analysis: {e}")
    S.audit(db, u.id, "camera_started", c.code); return cam_dict(c)


@router.post("/cameras/{cid}/stop")
def stop_cam(cid: int, u=Depends(require_admin), db: Session = Depends(get_db)):
    c = db.get(Camera, cid)
    if not c: raise HTTPException(404)
    runtime.stop_live(cid); S.audit(db, u.id, "camera_stopped", c.code); db.refresh(c); return cam_dict(c)


@router.get("/cameras/{cid}/stream.mjpg")
def mjpeg(cid: int):
    def gen():
        last = None
        while True:
            r = runtime.runners.get(cid)
            if not r or not r.is_alive(): return
            with r.lock: j = r.jpeg
            if j is not None and j is not last:
                last = j; yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(j)).encode() + b"\r\n\r\n" + j + b"\r\n"
            time.sleep(0.1)
    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")


@router.get("/cameras/{cid}/frame.jpg")
def frame(cid: int, db: Session = Depends(get_db)):
    """Latest annotated frame if running, else one raw frame (used for zone drawing)."""
    r = runtime.runners.get(cid)
    if r and r.is_alive() and r.jpeg: return Response(r.jpeg, media_type="image/jpeg")
    c = db.get(Camera, cid)
    if not c: raise HTTPException(404)
    cap = cv2.VideoCapture(pipeline._src(c.rtsp_url)); ok, fr = cap.read(); cap.release()
    if not ok: raise HTTPException(502, "Could not read a frame from the camera")
    return Response(cv2.imencode(".jpg", fr)[1].tobytes(), media_type="image/jpeg")


# ---------------- video upload + analysis jobs
def job_dict(j):
    inc_code = j.incident.code if getattr(j, "incident", None) else None
    return {"id": j.id, "job_id": f"JOB-{j.id:04d}", "filename": j.filename, "filesize": getattr(j, "filesize", None), "format": getattr(j, "format", None),
            "duration": getattr(j, "duration", None), "status": j.status, "progress": j.progress,
            "analysis_result": getattr(j, "analysis_result", None), "category": getattr(j, "category", None),
            "incident_id": getattr(j, "incident_id", None), "incident_code": inc_code,
            "camera_id": j.camera_id, "lat": j.lat, "lon": j.lon, "loc_source": j.loc_source, "error": j.error,
            "created_at": j.created_at.isoformat() + "Z", "result": json.loads(j.result_json) if j.result_json else None,
            "results": j.result_json}


@router.post("/jobs")
async def upload(file: UploadFile = File(...), camera_id: int | None = Form(None), u=Depends(require_admin), db: Session = Depends(get_db)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in config.ALLOWED_EXT: raise HTTPException(400, f"Unsupported format. Allowed: {', '.join(sorted(config.ALLOWED_EXT))}")
    name = f"{uuid.uuid4().hex}{ext}"; dst = config.UPLOADS / name; size = 0; cap = config.MAX_UPLOAD_MB * 1024 * 1024
    with open(dst, "wb") as f:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > cap:
                f.close(); dst.unlink(missing_ok=True); raise HTTPException(413, f"File exceeds {config.MAX_UPLOAD_MB} MB")
            f.write(chunk)
    cap_ = cv2.VideoCapture(str(dst))
    ok = cap_.isOpened() and cap_.get(cv2.CAP_PROP_FRAME_COUNT) > 0
    fps = cap_.get(cv2.CAP_PROP_FPS) or 25.0
    frames = cap_.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    dur = round(frames / fps, 1) if fps else 0.0
    cap_.release()
    if not ok:
        dst.unlink(missing_ok=True); raise HTTPException(400, "File is not a readable video (corrupt or unsupported codec)")
    j = Job(filename=Path(file.filename).name[:200], path=name, filesize=size, format=ext.replace(".", "").upper(), duration=dur, camera_id=camera_id, created_by=u.id)
    g = geo.video_gps(dst)
    if g: j.lat, j.lon, j.loc_source = g[0], g[1], "video_metadata"
    db.add(j); db.commit(); S.audit(db, u.id, "video_uploaded", j.filename); return job_dict(j)


class JobStart(BaseModel):
    camera_id: int | None = None
    lat: float | None = None
    lon: float | None = None


@router.post("/jobs/{jid}/start")
def start_job(jid: int, b: JobStart, u=Depends(require_admin), db: Session = Depends(get_db)):
    j = db.get(Job, jid)
    if not j: raise HTTPException(404)
    if j.status not in ("uploaded", "failed", "cancelled", "done"): raise HTTPException(409, f"Job is {j.status}")
    if not config.MODEL_PATH.exists(): raise HTTPException(503, "YOLO model missing. Run: python scripts/export_model.py")
    if b.camera_id is not None: j.camera_id = b.camera_id
    if b.lat is not None:
        if not geo.valid_coords(b.lat, b.lon): raise HTTPException(400, "Invalid coordinates")
        j.lat, j.lon, j.loc_source = b.lat, b.lon, "manual"
    j.status, j.progress, j.error, j.result_json = "queued", 0, None, None; db.commit()
    runtime.cancel.discard(jid); runtime.job_q.put(jid); S.audit(db, u.id, "analysis_started", j.filename); return job_dict(j)


@router.post("/jobs/{jid}/cancel")
def cancel_job(jid: int, db: Session = Depends(get_db)):
    runtime.cancel.add(jid); j = db.get(Job, jid)
    if j and j.status == "queued": j.status = "cancelled"; db.commit()
    return {"ok": True}


@router.get("/jobs")
@router.get("/videos/jobs")
def jobs(db: Session = Depends(get_db)):
    return [job_dict(j) for j in db.scalars(select(Job).order_by(Job.id.desc()).limit(100))]


@router.get("/jobs/{jid}")
def job(jid: int, db: Session = Depends(get_db)):
    j = db.get(Job, jid)
    if not j: raise HTTPException(404)
    return job_dict(j)


@router.get("/jobs/{jid}/video")
def job_video(jid: int, db: Session = Depends(get_db)):
    j = db.get(Job, jid)
    if not j: raise HTTPException(404)
    return FileResponse(config.UPLOADS / j.path, filename=None)


@router.get("/jobs/{jid}/download")
def download_job_video(jid: int, db: Session = Depends(get_db)):
    j = db.get(Job, jid)
    if not j: raise HTTPException(404)
    filepath = config.UPLOADS / j.path
    if not filepath.exists(): raise HTTPException(404, "Video file not found on disk")
    return FileResponse(filepath, media_type="video/mp4", filename=j.filename)


@router.delete("/jobs/{jid}")
def delete_job(jid: int, u=Depends(current_user), db: Session = Depends(get_db)):
    runtime.cancel.add(jid)
    j = db.get(Job, jid)
    if not j: raise HTTPException(404, "Video record not found")
    if j.incident_id:
        inc = db.get(Incident, j.incident_id)
        if inc and inc.status in ("PENDING", "WORKING", "NEW", "ACKNOWLEDGED", "RESPONDING"):
            raise HTTPException(409, f"This video is linked to active police incident {inc.code} with forensic evidence and cannot be deleted until the incident is solved.")
        elif inc and inc.status in ("SOLVED", "RESOLVED", "REJECTED"):
            j.incident_id = None
            db.commit()
    filepath = config.UPLOADS / j.path
    filepath.unlink(missing_ok=True)
    filename = j.filename
    db.delete(j)
    db.commit()
    S.audit(db, u.id, "video_deleted", filename)
    return {"ok": True, "message": f"Video record '{filename}' deleted successfully"}


@router.get("/jobs/{jid}/frame.jpg")
def job_frame(jid: int, t: float = 0, db: Session = Depends(get_db)):
    j = db.get(Job, jid)
    if not j: raise HTTPException(404)
    cap = cv2.VideoCapture(str(config.UPLOADS / j.path)); cap.set(cv2.CAP_PROP_POS_MSEC, max(t, 0) * 1000); ok, fr = cap.read(); cap.release()
    if not ok: raise HTTPException(404)
    return Response(cv2.imencode(".jpg", fr)[1].tobytes(), media_type="image/jpeg")
