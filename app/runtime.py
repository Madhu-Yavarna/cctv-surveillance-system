"""Background workers: live camera runners + a single sequential job worker (keeps CPU use sane on an i5/8 GB laptop)."""
import json, queue, threading, traceback
from datetime import datetime
from .db import SessionLocal
from .models import Camera, Job, Incident
from . import services as S, config
from .ai import pipeline
from .ai.detector import Detector

_det = None; _det_lock = threading.Lock(); _det_classes = None
runners: dict[int, pipeline.LiveRunner] = {}
job_q: "queue.Queue[int]" = queue.Queue()
cancel: set[int] = set()


def get_detector():
    global _det
    with _det_lock:
        if _det is None:
            _det = Detector(classes=(0,))
        return _det


def _cfg(db):
    s = S.get_settings(db); return s


# ---------------- live cameras
def start_live(cam_id: int):
    with SessionLocal() as db:
        cam = db.get(Camera, cam_id); cfg = _cfg(db)
        if cam_id in runners and runners[cam_id].is_alive():
            return
        det = get_detector()
        zone = S.cam_zone(cam)

        def on_event(ev, frame, box, conf):
            with SessionLocal() as d2:
                c2 = d2.get(Camera, cam_id)
                inc = S.create_candidate(d2, ev, frame, box, conf, source="live", cam=c2, cfg=cfg)
                return inc.id

        def on_clip(inc_id, frames, fps):
            with SessionLocal() as d2:
                S.attach_live_clip(d2, inc_id, frames, fps)

        def on_status(cid, ok, err):
            with SessionLocal() as d2:
                c = d2.get(Camera, cid)
                if c:
                    c.status = "online" if ok else "offline"; c.last_error = err; c.last_checked = datetime.utcnow(); d2.commit()

        r = pipeline.LiveRunner(cam_id, cam.rtsp_url, det, cfg, zone, cfg["analysis_fps"], cfg["pre_seconds"], cfg["post_seconds"],
                                on_event, on_clip, on_status)
        runners[cam_id] = r; r.start()


def stop_live(cam_id: int):
    r = runners.pop(cam_id, None)
    if r: r.stop()
    with SessionLocal() as db:
        c = db.get(Camera, cam_id)
        if c: c.status = "offline"; c.last_checked = datetime.utcnow(); db.commit()


# ---------------- uploaded-video jobs
def run_job(job_id: int):
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job or job.status != "queued":
            return
        job.status = "running"; job.progress = 0; db.commit()
        cam = db.get(Camera, job.camera_id) if job.camera_id else None
        cfg = _cfg(db); zone = S.cam_zone(cam)
        found = []
        try:
            det = get_detector()

            def on_event(ev, frame, box, conf):
                found.append({"level": ev.get("level"), "video_ts": round(ev.get("ts", 0), 1), "duration": ev.get("duration", 0), "score": ev.get("score", 0)})

            def on_progress(p):
                job.progress = round(p * 100, 1); db.commit()

            stats = pipeline.analyze_video(config.UPLOADS / job.path, det, cfg, zone, on_event, on_progress,
                                           stop=lambda: job_id in cancel, analysis_fps=cfg["analysis_fps"])
            stats["detections"] = found
            stats["zone_defined"] = bool(zone)

            # Store results on job
            job.analysis_result = stats.get("analysis_result", "No Risk Detected")
            job.category = stats.get("category", "Normal activities")
            job.duration = stats.get("video_seconds", 0.0)

            # If Risk Detected: auto-create incident and dispatch real-time to Police Dashboard
            if job.analysis_result == "Risk Detected" and job_id not in cancel:
                inc = S.create_and_dispatch_upload_incident(db, job, stats, cam=cam, cfg=cfg)
                if inc:
                    found.append({"incident": inc.code, "level": inc.risk_level, "category": inc.category, "score": inc.risk_score})

            # Clean non-serializable objects before json.dumps
            def _to_json_safe(obj):
                if hasattr(obj, "tolist"):
                    return obj.tolist()
                if hasattr(obj, "item"):
                    return obj.item()
                if isinstance(obj, dict):
                    return {str(k): _to_json_safe(v) for k, v in obj.items() if k not in ("peak_frame",)}
                if isinstance(obj, (list, tuple)):
                    return [_to_json_safe(x) for x in obj]
                return obj

            serializable_stats = _to_json_safe(stats)
            job.result_json = json.dumps(serializable_stats)
            job.status = "cancelled" if job_id in cancel else "done"
            job.progress = 100 if job.status == "done" else job.progress
        except Exception as e:
            traceback.print_exc()
            job.status = "failed"
            job.analysis_result = "Processing Failed"
            job.error = str(e)[:480]
        db.commit()


def _worker():
    while True:
        jid = job_q.get()
        try: run_job(jid)
        except Exception: traceback.print_exc()


def startup():
    with SessionLocal() as db:
        for c in db.query(Camera).all(): c.status = "offline"      # nothing is Online until verified again
        for j in db.query(Job).filter(Job.status.in_(["queued", "running"])).all():
            j.status = "failed"; j.error = "Interrupted by server restart"
        db.commit()
    threading.Thread(target=_worker, daemon=True, name="job-worker").start()
