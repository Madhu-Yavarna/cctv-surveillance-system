import csv, io, mimetypes, zipfile
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from ..db import get_db
from ..models import *  # noqa
from ..security import current_user, require_admin, require_police, optional_user
from .. import services as S, evidence as E, config
from ..ws import hub

router = APIRouter(prefix="/api", tags=["incidents"])


def _visible(inc: Incident, u: User) -> bool:
    if u.role == "admin": return True
    return bool(inc and inc.station_id == u.station_id and inc.status in S.POLICE_VISIBLE)


def _get(db, iid, u) -> Incident:
    inc = db.get(Incident, iid)
    if not inc or not _visible(inc, u): raise HTTPException(404, "Incident not found")   # 404, not 403: don't reveal other stations' incidents
    return inc


@router.get("/incidents")
def list_incidents(risk: str | None = None, status: str | None = None, date_from: str | None = None, date_to: str | None = None,
                   limit: int = 200, u: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(Incident).order_by(Incident.id.desc()).limit(min(limit, 500))
    if u.role == "police": q = q.where(Incident.station_id == u.station_id, Incident.status.in_(S.POLICE_VISIBLE))
    if risk: q = q.where(Incident.risk_level == risk)
    if status: q = q.where(Incident.status == status)
    try:
        if date_from: q = q.where(Incident.detected_at >= datetime.fromisoformat(date_from))
        if date_to: q = q.where(Incident.detected_at < datetime.fromisoformat(date_to) + timedelta(days=1))
    except ValueError: raise HTTPException(400, "Bad date")
    return [S.inc_dict(i) for i in db.scalars(q)]


@router.get("/incidents/{iid}")
def get_incident(iid: int, u: User = Depends(current_user), db: Session = Depends(get_db)):
    d = S.inc_dict(_get(db, iid, u), full=True)
    return S.for_police(d) if u.role == "police" else d


@router.delete("/incidents/{iid}")
async def delete_incident(iid: int, force: bool = False, u: User = Depends(current_user), db: Session = Depends(get_db)):
    if u.role not in ("admin", "police"):
        raise HTTPException(403, "Access restricted to authorized personnel")

    inc = db.get(Incident, iid)
    if not inc:
        raise HTTPException(404, "Incident not found")

    if u.role == "police":
        if inc.station_id and u.station_id and inc.station_id != u.station_id:
            raise HTTPException(403, "You can only delete incidents assigned to your police jurisdiction.")

    # Check if incident is currently undergoing active police response unless force is requested
    if inc.status in ("WORKING", "RESPONDING") and not force:
        raise HTTPException(
            400,
            f"Cannot delete incident {inc.code}: An active police response is currently in progress. "
            f"Please resolve the incident or confirm authorized deletion."
        )

    code = inc.code
    # Unlink any jobs referencing this incident
    for j in db.scalars(select(Job).where(Job.incident_id == iid)):
        j.incident_id = None

    # Delete notifications referencing this incident
    for n in db.scalars(select(Notification).where(Notification.incident_id == iid)):
        db.delete(n)

    # Safely clean up this incident's dedicated evidence directory on disk
    inc_dir = config.EVIDENCE / code
    if inc_dir.exists() and inc_dir.is_dir():
        import shutil
        shutil.rmtree(inc_dir, ignore_errors=True)

    # Delete incident (evidence cascade deletes automatically)
    db.delete(inc)
    db.commit()

    S.audit(db, u.id, "incident_deleted", f"Authorized deletion of incident {code}")
    await hub.to_admins({"type": "INCIDENT_DELETED", "incident_id": iid, "code": code})
    await hub.to_all({"type": "INCIDENT_DELETED", "incident_id": iid, "code": code})

    return {"status": "success", "message": f"Incident {code} deleted successfully."}


class Confirm(BaseModel):
    lat: float | None = None
    lon: float | None = None
    location_text: str | None = None


@router.post("/incidents/{iid}/confirm")
async def confirm(iid: int, b: Confirm, u: User = Depends(require_admin), db: Session = Depends(get_db)):
    inc = db.get(Incident, iid)
    if not inc: raise HTTPException(404)
    try: n = S.confirm_incident(db, inc, u, b.lat, b.lon, b.location_text, manual=b.lat is not None)
    except ValueError as e: raise HTTPException(400, str(e))
    db.refresh(inc)
    sent = await hub.to_station(n.station_id, S.notif_payload(db, n))
    if sent: n.delivered_at = datetime.utcnow(); db.commit()
    return {"incident": S.inc_dict(inc, full=True), "delivered_live": sent,
            "note": "Delivered to a connected officer." if sent else "No officer online; the alert is saved and will be delivered on reconnect."}


class Loc(BaseModel):
    lat: float
    lon: float
    location_text: str | None = None


@router.put("/incidents/{iid}/location")
def set_location(iid: int, b: Loc, u: User = Depends(require_admin), db: Session = Depends(get_db)):
    from .. import geo
    inc = db.get(Incident, iid)
    if not inc: raise HTTPException(404)
    if inc.status != "CANDIDATE": raise HTTPException(400, "Location can only be changed before confirmation")
    if not geo.valid_coords(b.lat, b.lon): raise HTTPException(400, "Invalid coordinates")
    inc.lat, inc.lon, inc.loc_source = b.lat, b.lon, "manual"
    if b.location_text: inc.location_text = b.location_text[:255]
    db.commit(); S.audit(db, u.id, "incident_location_set", f"{inc.code}: {b.lat},{b.lon}")
    return S.inc_dict(inc, full=True)


class Reject(BaseModel):
    remarks: str | None = None


@router.post("/incidents/{iid}/reject")
def reject(iid: int, b: Reject, u: User = Depends(require_admin), db: Session = Depends(get_db)):
    inc = db.get(Incident, iid)
    if not inc: raise HTTPException(404)
    if inc.status != "CANDIDATE": raise HTTPException(400, "Only candidates can be rejected")
    inc.status, inc.remarks = "REJECTED", (b.remarks or "")[:1000]; db.commit(); S.audit(db, u.id, "incident_rejected", inc.code)
    return S.inc_dict(inc)


class StatusIn(BaseModel):
    status: str
    remarks: str | None = None


STATUS_NORM_MAP = {
    "pending": "PENDING",
    "working": "WORKING",
    "solved": "SOLVED",
    "new": "NEW",
    "acknowledged": "WORKING",
    "responding": "WORKING",
    "under review": "WORKING",
    "resolved": "SOLVED"
}


@router.post("/incidents/{iid}/status")
async def set_status(iid: int, b: StatusIn, u: User = Depends(require_police), db: Session = Depends(get_db)):
    inc = _get(db, iid, u)
    target_status = STATUS_NORM_MAP.get(b.status.strip().lower(), b.status.strip().upper())
    
    # If the target is the same, just update remarks
    if target_status != inc.status and target_status not in S.TRANSITIONS.get(inc.status, set()):
        # Allow flexible transition between active states
        if target_status not in ("PENDING", "WORKING", "SOLVED", "NEW", "ACKNOWLEDGED", "RESPONDING", "RESOLVED"):
            raise HTTPException(400, f"Invalid transition {inc.status} -> {target_status}. Allowed: {sorted(S.TRANSITIONS.get(inc.status, [])) or 'none'}")
    
    old = inc.status
    inc.status = target_status
    if b.remarks:
        stamp = f"[{datetime.utcnow():%Y-%m-%d %H:%M}Z {u.username} -> {target_status}] {b.remarks.strip()[:500]}"
        inc.remarks = (inc.remarks + "\n" if inc.remarks else "") + stamp
    for n in db.scalars(select(Notification).where(Notification.incident_id == iid, Notification.station_id == u.station_id)):
        if not n.acked_at and target_status in ("WORKING", "SOLVED", "ACKNOWLEDGED"):
            n.acked_at = datetime.utcnow()
    db.commit()
    S.audit(db, u.id, "response_update", f"{inc.code}: {old} -> {target_status}")
    await hub.to_admins({"type": "response_update", "incident": S.inc_dict(inc)})
    await hub.to_station(u.station_id, {"type": "incident_update", "incident": S.for_police(S.inc_dict(inc, full=True))})
    return S.for_police(S.inc_dict(inc, full=True))


@router.post("/police/incidents/{iid}/respond")
async def police_respond(iid: int, b: StatusIn, u: User = Depends(require_police), db: Session = Depends(get_db)):
    return await set_status(iid, b, u, db)


@router.post("/police/incidents/{iid}/acknowledge")
async def police_ack(iid: int, u: User = Depends(require_police), db: Session = Depends(get_db)):
    b = StatusIn(status="WORKING", remarks="Acknowledged by officer")
    return await set_status(iid, b, u, db)


# ---------------- police summary / reports
@router.get("/police/summary")
def police_summary(u: User = Depends(require_police), db: Session = Depends(get_db)):
    rows = dict(db.execute(select(Incident.status, func.count()).where(Incident.station_id == u.station_id, Incident.status.in_(S.POLICE_VISIBLE)).group_by(Incident.status)).all())
    latest = db.scalar(select(Incident).where(Incident.station_id == u.station_id, Incident.status.in_(S.POLICE_VISIBLE)).order_by(Incident.id.desc()))
    total = sum(rows.values())
    p_cnt = rows.get("PENDING", 0) + rows.get("NEW", 0)
    w_cnt = rows.get("WORKING", 0) + rows.get("ACKNOWLEDGED", 0) + rows.get("RESPONDING", 0)
    s_cnt = rows.get("SOLVED", 0) + rows.get("RESOLVED", 0)
    return {
        "total": total,
        "pending": p_cnt,
        "working": w_cnt,
        "solved": s_cnt,
        "new": p_cnt,
        "under_review": w_cnt,
        "resolved": s_cnt,
        "station": {"id": u.station.id, "name": u.station.name, "lat": u.station.lat, "lon": u.station.lon, "phone": u.station.phone} if u.station else None,
        "latest": S.for_police(S.inc_dict(latest, full=True)) if latest else None
    }


@router.get("/police/stats")
def police_stats(u: User = Depends(require_police), db: Session = Depends(get_db)):
    s = police_summary(u, db)
    return {
        "total_alerts": s["total"],
        "new_alerts": s["new"],
        "pending": s["pending"],
        "under_review": s["working"],
        "resolved": s["solved"],
        "assigned_station_code": u.station.name if u.station else "CPS-01"
    }


@router.get("/police/latest-alert")
def police_latest_alert(u: User = Depends(require_police), db: Session = Depends(get_db)):
    latest = db.scalar(select(Incident).where(Incident.station_id == u.station_id, Incident.status.in_(S.POLICE_VISIBLE)).order_by(Incident.id.desc()))
    if not latest:
        return {}
    return S.for_police(S.inc_dict(latest, full=True))


@router.get("/police/reports.csv")
def report_csv(u: User = Depends(require_police), db: Session = Depends(get_db)):
    out = io.StringIO(); w = csv.writer(out)
    w.writerow(["incident_id", "detected_at", "camera", "location", "lat", "lon", "risk", "duration_s", "status", "straight_line_km", "remarks"])
    for i in db.scalars(select(Incident).where(Incident.station_id == u.station_id, Incident.status.in_(S.POLICE_VISIBLE)).order_by(Incident.id)):
        w.writerow([i.code, i.detected_at, i.camera.name if i.camera else "", i.location_text or "", i.lat, i.lon, i.risk_level, i.duration, i.status, i.station_km, (i.remarks or "").replace("\n", " | ")])
    return StreamingResponse(iter([out.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=station_report.csv"})


# ---------------- evidence
def _ev(db, eid, u):
    e = db.get(Evidence, eid)
    if not e or not _visible(e.incident, u): raise HTTPException(404)
    if u.role == "police" and e.kind == "original": raise HTTPException(403, "Original recording is restricted to the Admin")
    return e


def _resolve_evidence_path(e):
    p = None
    try:
        p = E.safe_join(config.EVIDENCE, e.rel_path)
    except ValueError:
        pass

    if not p or not p.exists():
        if e.incident:
            cand1 = config.EVIDENCE / e.incident.code / ("snapshot.jpg" if e.kind == "snapshot" else ("clip.mp4" if e.kind == "clip" else f"{e.kind}.json"))
            if cand1.exists():
                p = cand1
            else:
                pat = f"{e.incident.code}*_{e.kind}.*"
                matches = list(config.EVIDENCE.glob(pat))
                if matches:
                    p = matches[0]
    return p


def _ev_safe(db, eid, u):
    e = db.get(Evidence, eid)
    if not e: raise HTTPException(404, "Evidence not found")
    if u:
        if not _visible(e.incident, u): raise HTTPException(404)
        if u.role == "police" and e.kind == "original": raise HTTPException(403, "Original recording is restricted to the Admin")
    return e


@router.get("/evidence/{eid}/file")
def evidence_file(eid: int, download: bool = False, u: User | None = Depends(optional_user), db: Session = Depends(get_db)):
    e = _ev_safe(db, eid, u)
    p = _resolve_evidence_path(e)

    if not p or not p.exists():
        raise HTTPException(404, "Evidence file missing on disk")

    if download and u:
        S.audit(db, u.id, "evidence_download", e.rel_path)
    return FileResponse(p, media_type=mimetypes.guess_type(p.name)[0] or "application/octet-stream", filename=p.name if download else None)


@router.get("/evidence/{eid}/download")
def evidence_download(eid: int, u: User = Depends(current_user), db: Session = Depends(get_db)):
    return evidence_file(eid, download=True, u=u, db=db)


@router.get("/incidents/{iid}/download")
def incident_download_evidence(iid: int, file_type: str = "clip", u: User = Depends(current_user), db: Session = Depends(get_db)):
    inc = _get(db, iid, u)
    target_kind = "clip" if ("clip" in file_type.lower() or "video" in file_type.lower()) else ("snapshot" if ("snap" in file_type.lower() or "image" in file_type.lower()) else None)
    if target_kind:
        ev = next((e for e in inc.evidence if e.kind == target_kind), None)
        if ev:
            return evidence_file(ev.id, download=True, u=u, db=db)
    return package(iid, u=u, db=db)


def _verify(db, e, u):
    if not e.sha256: return {"id": e.id, "kind": e.kind, "result": "not_hashed", "is_match": False, "recalculated_hash": "", "detail": "Hashes are recorded when the Admin confirms the incident"}
    p = _resolve_evidence_path(e)
    if not p or not p.exists():
        e.verify_result = "missing"
        actual = None
        ok = False
    else:
        ok, actual = E.verify_file(p, e.sha256)
        e.verify_result = "match" if ok else "mismatch"
    e.verified_at = datetime.utcnow()
    db.commit(); S.audit(db, u.id, "hash_verify", f"{e.rel_path}: {e.verify_result}")
    return {"id": e.id, "kind": e.kind, "result": e.verify_result, "is_match": e.verify_result == "match", "recalculated_hash": actual or e.sha256, "expected": e.sha256, "actual": actual, "verified_at": e.verified_at.isoformat() + "Z"}


@router.post("/evidence/{eid}/verify")
def verify_one(eid: int, file_type: str | None = None, u: User = Depends(current_user), db: Session = Depends(get_db)):
    ev = db.get(Evidence, eid)
    if not ev:
        inc = db.get(Incident, eid)
        if inc:
            target_kind = "clip" if (file_type and "clip" in file_type.lower()) else "snapshot"
            match_ev = next((e for e in inc.evidence if e.kind == target_kind), None) or next((e for e in inc.evidence if e.kind != "manifest"), None)
            if match_ev:
                return _verify(db, match_ev, u)
    return _verify(db, _ev(db, eid, u), u)


@router.post("/incidents/{iid}/verify")
def verify_all(iid: int, u: User = Depends(current_user), db: Session = Depends(get_db)):
    inc = _get(db, iid, u)
    res = [_verify(db, e, u) for e in inc.evidence if not (u.role == "police" and e.kind == "original")]
    states = {r["result"] for r in res}
    overall = "not_hashed" if states == {"not_hashed"} else ("match" if states <= {"match"} else "FAILED")
    return {"overall": overall, "files": res}


@router.get("/incidents/{iid}/package")
def package(iid: int, u: User = Depends(current_user), db: Session = Depends(get_db)):
    inc = _get(db, iid, u)
    if inc.status == "CANDIDATE": raise HTTPException(400, "Evidence package is available after confirmation")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for e in inc.evidence:
            if u.role == "police" and e.kind == "original": continue
            p = E.safe_join(config.EVIDENCE, e.rel_path)
            if p.exists(): z.write(p, p.name)
    S.audit(db, u.id, "evidence_package", inc.code); buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip", headers={"Content-Disposition": f"attachment; filename={inc.code}_evidence.zip"})
