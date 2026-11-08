"""Detection -> ByteTrack -> context/risk. DB-free: callers pass callbacks. Same Analyzer for live + upload."""
import os, threading, time
from collections import deque
from datetime import datetime
import cv2
import numpy as np
from .tracker import ByteTracker
from .risk import RiskEngine, DEFAULTS
from ..evidence import draw_box
from .detector import COCO
from .action import SpatialTemporalActionRecognizer
from .fire import TemporalFireDetector
from .accident import VehicleAccidentDetector

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|stimeout;5000000")


class Analyzer:
    def __init__(self, detector, cfg, zone, analysis_fps=5, start_dt=None):
        self.det, self.cfg = detector, {**DEFAULTS, **cfg}
        self.tracker = ByteTracker(buffer=int(analysis_fps * 3))
        self.engine = RiskEngine(self.cfg, zone, start_dt)
        self.best = {}          # track id -> (frame, box, conf, ts): last in-zone view, used for snapshots
        self.last_tracks, self.last_dets = [], []

    def process(self, frame, ts):
        h, w = frame.shape[:2]
        dets = self.det(frame)
        tracks = self.tracker.update(dets, ts)
        events = self.engine.update(tracks, ts, (w, h))
        for t in tracks:
            c = self.engine.ctx.get(t.id)
            if c and c.in_zone and (t.id not in self.best or ts - self.best[t.id][3] >= 1.0):
                self.best[t.id] = (frame.copy(), t.box.copy(), float(t.conf), ts)
        self.last_tracks, self.last_dets = tracks, dets
        return tracks, events

    def snapshot_for(self, ev, frame):
        b = self.best.get(ev["track_id"])
        f, box, conf = (b[0], b[1], b[2]) if b else (frame, ev.get("bbox"), ev.get("confidence", 0))
        return f, box, conf

    def finish(self, ts):
        return self.engine.finish(ts)

    def annotate(self, frame, tracks=None):
        out = frame.copy()
        h, w = out.shape[:2]
        if self.engine.zone is not None:
            pts = (self.engine.zone * [w, h]).astype("int32")
            ov = out.copy(); cv2.fillPoly(ov, [pts], (0, 140, 255)); out = cv2.addWeighted(ov, 0.15, out, 0.85, 0)
            cv2.polylines(out, [pts], True, (0, 165, 255), 2)
        for t in (self.last_tracks if tracks is None else tracks):
            name = COCO[t.cls] if t.cls < len(COCO) else str(t.cls)
            draw_box(out, t.box, f"{name} {t.conf:.2f} #{t.id}")
        return out


def analyze_video(path, detector, cfg, zone, on_event, on_progress=None, stop=lambda: False, analysis_fps=5):
    """Process a recorded file with multi-category activity and risk analysis.
    Returns comprehensive stats including category, analysis_result, risk_score, and peak evidence.
    """
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise IOError("Cannot open video (unsupported codec or corrupt file)")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(1, round(fps / analysis_fps))
    an = Analyzer(detector, cfg, zone, analysis_fps=fps / step)
    action_rec = SpatialTemporalActionRecognizer(fps=fps / step)
    fire_detector = TemporalFireDetector(fps=fps / step)
    accident_detector = VehicleAccidentDetector(fps=fps / step)
    
    idx = n = 0
    t0 = time.perf_counter()
    events = []
    frame = None
    prev_gray = None
    ts = 0.0
    
    # Category detection metrics
    max_motion_ratio = 0.0
    fire_frames = 0
    close_proximity_events = 0
    high_speed_events = 0
    max_people_in_frame = 0
    loiter_detected = False
    
    peak_score = 0.0
    peak_frame = None
    peak_box = None
    peak_conf = 0.0
    peak_ts = 0.0
    peak_category = "Normal activities"
    
    track_history = {}  # tid -> list of (ts, center_x, center_y, box, conf)

    while not stop():
        if idx % step:
            if not cap.grab():
                break
            idx += 1
            continue
        ok, frame = cap.read()
        if not ok:
            break
        ts = idx / fps
        h, w = frame.shape[:2]
        
        # 1. Optical Motion Analysis via frame differencing
        curr_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if prev_gray is not None:
            diff = cv2.absdiff(prev_gray, curr_gray)
            _, thresh = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)
            m_ratio = float(np.count_nonzero(thresh)) / float(w * h)
            if m_ratio > max_motion_ratio:
                max_motion_ratio = m_ratio
        else:
            m_ratio = 0.0
        prev_gray = curr_gray
        
        # 2. Fire / Flame color detection in HSV
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        flame_mask = cv2.inRange(hsv, np.array([0, 120, 150]), np.array([28, 255, 255]))
        flame_ratio = float(np.count_nonzero(flame_mask)) / float(w * h)
        if flame_ratio > 0.015:
            fire_frames += 1

        # 3. Object Detection & ByteTrack tracking
        tracks, evs = an.process(frame, ts)
        persons = [t.box for t in tracks if t.cls == 0]
        num_persons = len(persons)
        if num_persons > max_people_in_frame:
            max_people_in_frame = num_persons

        # 4. Multi-Category Specialized Processors
        action_rec.process_frame(frame, persons)
        fire_detector.process_frame(frame, persons)
        accident_detector.process_frame(tracks, ts)

        # Record track history for trajectory and velocity computation
        for t in tracks:
            cx, cy = (t.box[0] + t.box[2]) / 2.0, (t.box[1] + t.box[3]) / 2.0
            hist = track_history.setdefault(t.id, [])
            hist.append((ts, cx, cy, t.box, float(t.conf)))
            if len(hist) >= 2:
                dt = hist[-1][0] - hist[-2][0]
                if dt > 0:
                    dist = ((hist[-1][1] - hist[-2][1]) ** 2 + (hist[-1][2] - hist[-2][2]) ** 2) ** 0.5
                    speed = dist / dt  # pixels per second
                    box_h = max(t.box[3] - t.box[1], 10)
                    norm_speed = speed / box_h  # body-heights per second
                    if norm_speed > 2.2:  # Rapid running/fleeing
                        high_speed_events += 1

        # 5. Multi-person proximity & interaction
        if num_persons >= 2:
            for i in range(num_persons):
                for j in range(i + 1, num_persons):
                    b1, b2 = tracks[i].box, tracks[j].box
                    c1 = ((b1[0] + b1[2]) / 2, (b1[1] + b1[3]) / 2)
                    c2 = ((b2[0] + b2[2]) / 2, (b2[1] + b2[3]) / 2)
                    dist = ((c1[0] - c2[0]) ** 2 + (c1[1] - c2[1]) ** 2) ** 0.5
                    avg_h = ((b1[3] - b1[1]) + (b2[3] - b2[1])) / 2.0
                    if dist < 1.2 * avg_h:  # Close physical contact/proximity
                        close_proximity_events += 1

        # Process any events from RiskEngine
        for ev in evs:
            f, b, c = an.snapshot_for(ev, frame)
            on_event(ev, f, b, c)
            events.append(ev)
            if ev["score"] > peak_score:
                peak_score = ev["score"]
                peak_frame = f.copy()
                peak_box = b
                peak_conf = c
                peak_ts = ts
                loiter_detected = True

        # Keep best representative frame if none captured yet
        if peak_frame is None and tracks:
            peak_frame = frame.copy()
            peak_box = tracks[0].box
            peak_conf = tracks[0].conf
            peak_ts = ts

        n += 1
        idx += 1
        if on_progress and n % 5 == 0:
            if total > 0:
                on_progress(min(idx / total, 0.99))
            else:
                on_progress(min(0.3 + (n * 0.03), 0.95))

    # Process finish events
    if not stop():
        for ev in an.finish(ts):
            f, b, c = an.snapshot_for(ev, frame if frame is not None else None)
            if f is not None:
                on_event(ev, f, b, c)
                events.append(ev)
                if ev["score"] > peak_score:
                    peak_score = ev["score"]
                    peak_frame = f.copy()
                    peak_box = b
                    peak_conf = c
                    peak_ts = ev["ts"]
                    loiter_detected = True

    cap.release()
    el = time.perf_counter() - t0
    vid_dur = round(idx / fps, 1)

    # Evaluate specialized multi-category models
    act_summary = action_rec.compute_summary_features()
    violence_prob = act_summary.get("violence_prob", 0.0)
    is_conversation = act_summary.get("is_conversation", False)
    grapple_ratio = act_summary.get("grapple_ratio", 0.0)
    combat_persistence = act_summary.get("combat_persistence", 0.0)
    max_jerk = act_summary.get("max_jerk", 0.0)
    max_iou = act_summary.get("max_iou", 0.0)

    is_fire, fire_conf, fire_reason = fire_detector.evaluate_fire_risk()
    is_accident, accident_conf, accident_reason = accident_detector.evaluate_accident_risk()

    # Multi-category classification indicators
    indicators = {
        "person_detected": max_people_in_frame > 0,
        "conversation_detected": bool(is_conversation),
        "violent_combat_dynamics": bool(violence_prob >= 0.50 and combat_persistence >= 0.20 and (grapple_ratio >= 0.20 or max_iou >= 0.35)),
        "physical_assault_pattern": bool(violence_prob >= 0.45 and max_jerk >= 16.0 and high_speed_events >= 1),
        "sudden_fleeing": high_speed_events >= 2,
        "flame_detected": bool(is_fire),
        "vehicle_accident": bool(is_accident),
        "loitering": loiter_detected or (len(events) > 0 and peak_score >= 0.65),
        "violence_confidence": round(violence_prob * 100),
        "fire_confidence": round(fire_conf * 100),
        "conversation_score": round(act_summary.get("conversation_proxemics", 0.0) * 100),
    }

    # Complete multi-category decision engine
    # 1. Fire & Smoke Incidents
    if is_fire:
        category = "Fire-related incidents"
        score = max(fire_conf, 0.88)
        analysis_result = "Risk Detected"
        risk_level = "HIGH_RISK_CANDIDATE"
        summary = f"Fire/flame activity confirmed: {fire_reason}"

    # 2. Vehicle Collisions & Traffic Accidents
    elif is_accident:
        category = "Accidents and collisions"
        score = max(accident_conf, 0.85)
        analysis_result = "Risk Detected"
        risk_level = "HIGH_RISK_CANDIDATE"
        summary = f"Vehicle collision detected: {accident_reason}"

    # 3. Explicit Normal Human Interaction & Conversation (2-person and group discussions)
    elif is_conversation:
        category = "Normal activities"
        score = 0.12
        analysis_result = "No Risk Detected"
        risk_level = "LOW_RISK"
        summary = "Normal human interaction and conversation observed. Stable interpersonal proxemics, peaceful gestures, and no physical fighting or danger detected."

    # 4. Violent Combat & Physical Fighting
    elif indicators["violent_combat_dynamics"]:
        category = "Fighting and violence"
        score = max(violence_prob, 0.82)
        analysis_result = "Risk Detected"
        risk_level = "HIGH_RISK_CANDIDATE"
        summary = "Violent physical altercation confirmed: sustained multi-frame kinetic jerk, grappling body overlap, and aggressive combat dynamics detected."

    # 5. Physical Assault (One-way ballistic impact / strike pattern)
    elif indicators["physical_assault_pattern"]:
        category = "Assault"
        score = max(violence_prob, 0.84)
        analysis_result = "Risk Detected"
        risk_level = "HIGH_RISK_CANDIDATE"
        summary = "Physical assault pattern detected with high ballistic kinetic impact jerk and abrupt confrontational motion."

    # 6. Robbery (violent confrontation followed by rapid fleeing)
    elif indicators["sudden_fleeing"] and (indicators["violent_combat_dynamics"] or indicators["physical_assault_pattern"] or (violence_prob >= 0.55 and max_jerk >= 12.0)):
        category = "Robbery"
        score = max(violence_prob, 0.80)
        analysis_result = "Risk Detected"
        risk_level = "HIGH_RISK_CANDIDATE"
        summary = "Robbery / violent altercation followed by sudden rapid escape."

    # 7. Snatch / Theft (Physical snatch altercation followed by sudden fleeing)
    elif indicators["sudden_fleeing"] and max_people_in_frame >= 2 and violence_prob >= 0.50 and max_jerk >= 10.0:
        category = "Theft and stealing"
        score = max(violence_prob, 0.75)
        analysis_result = "Risk Detected"
        risk_level = "HIGH_RISK_CANDIDATE"
        summary = "Rapid snatch / theft pattern with sudden high-velocity egress."

    # 8. Restricted Zone Dwell / Loitering
    elif indicators["loitering"] or peak_score >= 0.65:
        category = "Suspicious or unusual activity"
        score = peak_score if peak_score >= 0.65 else 0.72
        analysis_result = "Risk Detected"
        risk_level = "HIGH_RISK_CANDIDATE"
        summary = "Suspicious loitering / prolonged presence or restricted zone dwell detected."

    # 9. Uncertain – Manual Review Required (When models detect ambiguous/elevated motion but insufficient evidence for risk)
    elif violence_prob >= 0.55 or (0.45 <= max_motion_ratio and not is_conversation and peak_score >= 0.55):
        category = "Suspicious or unusual activity"
        score = max(violence_prob, 0.52)
        analysis_result = "Uncertain – Manual Review Required"
        risk_level = "MEDIUM_RISK"
        summary = "Uncertain activity pattern: atypical motion dynamics observed. Manual human verification required."

    # 10. All other normal activities (Walking, standing, routine transit)
    else:
        category = "Normal activities"
        score = max(peak_score, 0.15) if max_people_in_frame > 0 else 0.05
        analysis_result = "No Risk Detected"
        risk_level = "LOW_RISK"
        summary = "Normal routine activity. No security risks or violent patterns detected."

    if peak_frame is None and frame is not None:
        peak_frame = frame.copy()
        peak_ts = vid_dur / 2.0

    return {
        "frames_analyzed": n,
        "video_seconds": vid_dur,
        "processing_seconds": round(el, 1),
        "measured_analysis_fps": round(n / el, 2) if el > 0 else 0,
        "sampling": f"every {step} frame(s) of {fps:.1f} fps",
        "events": len(events),
        "unknown": an.engine.unknown_log,
        "analysis_result": analysis_result,
        "category": category,
        "risk_score": round(score, 2),
        "risk_level": risk_level,
        "indicators": indicators,
        "summary": summary,
        "peak_ts": round(peak_ts, 1),
        "peak_box": peak_box,
        "peak_conf": round(peak_conf, 2),
        "peak_frame": peak_frame
    }


def _src(url):
    u = str(url).strip()
    return int(u) if u.isdigit() else u      # "0" = first USB webcam


def test_stream(url, timeout=8):
    """Open the stream and read one real frame. Only then may a camera be shown as Online."""
    res = {}
    def _t():
        cap = cv2.VideoCapture(_src(url))
        try:
            if not cap.isOpened():
                res["err"] = "Could not open stream"; return
            ok, fr = cap.read()
            if ok and fr is not None:
                res["ok"] = True; res["size"] = f"{fr.shape[1]}x{fr.shape[0]}"
            else:
                res["err"] = "Stream opened but no frame received"
        except Exception as e:
            res["err"] = str(e)
        finally:
            cap.release()
    th = threading.Thread(target=_t, daemon=True); th.start(); th.join(timeout)
    return res if res else {"err": f"Timed out after {timeout}s"}


class LiveRunner(threading.Thread):
    """One thread per started camera: read RTSP, analyse at <= analysis_fps, keep annotated JPEG for MJPEG."""
    def __init__(self, cam_id, url, detector, cfg, zone, analysis_fps, pre_s, post_s, on_event, on_clip, on_status):
        super().__init__(daemon=True, name=f"cam-{cam_id}")
        self.cam_id, self.url, self.fps_t, self.pre_s, self.post_s = cam_id, url, analysis_fps, pre_s, post_s
        self.on_event, self.on_clip, self.on_status = on_event, on_clip, on_status
        self.an = Analyzer(detector, cfg, zone, analysis_fps, start_dt=datetime.now())
        self.stop_flag = threading.Event()
        self.jpeg = None; self.state = {"status": "starting", "analysis_fps": 0.0, "resolution": None}
        self.lock = threading.Lock()
        self.buf = deque(maxlen=max(1, int(pre_s * analysis_fps)))
        self.pending = []

    def stop(self):
        self.stop_flag.set()

    def run(self):
        t_start = time.monotonic(); last_an = 0.0; ema = 0.0; fails = 0
        cap = None
        while not self.stop_flag.is_set():
            if cap is None or not cap.isOpened():
                cap = cv2.VideoCapture(_src(self.url))
                if not cap.isOpened():
                    self.state["status"] = "reconnecting"; self.on_status(self.cam_id, False, "Cannot open stream")
                    time.sleep(3); continue
            ok, frame = cap.read()
            if not ok:
                fails += 1
                if fails > 30:
                    cap.release(); cap = None; fails = 0
                    self.state["status"] = "reconnecting"; self.on_status(self.cam_id, False, "Stream lost")
                time.sleep(0.05); continue
            if fails or self.state["status"] != "live":
                self.on_status(self.cam_id, True, None)
            fails = 0; self.state["status"] = "live"
            now = time.monotonic(); ts = now - t_start
            h, w = frame.shape[:2]; self.state["resolution"] = f"{w}x{h}"
            if now - last_an < 1.0 / self.fps_t:
                continue                                    # frame sampling (grab-and-skip)
            dt = now - last_an; last_an = now
            try:
                tracks, evs = self.an.process(frame, ts)
            except Exception as e:
                self.state["status"] = f"error: {e}"; time.sleep(1); continue
            if dt < 5:
                ema = 0.9 * ema + 0.1 / max(dt, 1e-3) if ema else 1 / max(dt, 1e-3)
            self.state["analysis_fps"] = round(ema, 2)
            ann = self.an.annotate(frame, tracks)
            cv2.putText(ann, datetime.now().strftime("%d-%m-%Y %a %H:%M:%S"), (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
            sm = ann if w <= 960 else cv2.resize(ann, (960, int(h * 960 / w)))
            okj, jb = cv2.imencode(".jpg", sm, [cv2.IMWRITE_JPEG_QUALITY, 70])
            with self.lock:
                if okj:
                    self.jpeg = jb.tobytes()
                self.state["risk"] = self.an.engine.current_state(ts)
            small = frame if w <= 960 else cv2.resize(frame, (960, int(h * 960 / w)))
            self.buf.append(small)
            for p in list(self.pending):
                p["frames"].append(small)
                if ts >= p["until"]:
                    self.pending.remove(p)
                    try:
                        self.on_clip(p["code_key"], p["frames"], max(self.state["analysis_fps"], 1.0))
                    except Exception:
                        pass
            for ev in evs:
                f, b, c = self.an.snapshot_for(ev, frame)
                key = self.on_event(ev, f, b, c)
                if key:
                    self.pending.append({"code_key": key, "frames": list(self.buf), "until": ts + self.post_s})
        for p in list(self.pending):          # runner stopped mid-clip: save what we have
            try: self.on_clip(p["code_key"], p["frames"], max(self.state["analysis_fps"], 1.0))
            except Exception: pass
        if cap:
            cap.release()
        self.state["status"] = "stopped"
