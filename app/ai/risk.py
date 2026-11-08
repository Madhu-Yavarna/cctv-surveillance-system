"""Context analysis + Risk Suppression Engine.

Works on video time (seconds), so uploaded files and live streams use the same logic.
The score is an anomaly indicator, NOT a probability of crime. Track IDs are not identities.
Categories: LOW_RISK (suppressed), MEDIUM_RISK (admin review), HIGH_RISK_CANDIDATE (evidence + admin
review), UNKNOWN (insufficient evidence, never alerts police).
"""
import math
from datetime import datetime, timedelta
import cv2
import numpy as np

DEFAULTS = {
    "risk_threshold": 0.65,      # min anomaly score for HIGH_RISK_CANDIDATE
    "min_duration": 15.0,        # s continuously inside restricted zone
    "cooldown": 300.0,           # s between candidates on one camera
    "min_consecutive": 10,       # consecutive analysed observations inside the zone
    "min_confidence": 0.5,       # mean detection confidence required for MEDIUM/HIGH
    "exit_grace": 2.0,           # s outside zone before a stay is considered over
    "loiter_extent": 1.5,        # max movement (in bbox heights) that still counts as loitering
    "repeat_count": 3,           # distinct zone entries inside repeat_window -> repeated activity
    "repeat_window": 600.0,
    "night_enabled": False, "night_start": "20:00", "night_end": "06:00",
}


def in_night(dt: datetime, start: str, end: str) -> bool:
    s = datetime.strptime(start, "%H:%M").time(); e = datetime.strptime(end, "%H:%M").time()
    t = dt.time()
    return (s <= t or t < e) if s > e else (s <= t < e)


class TrackCtx:
    def __init__(self, tid, ts):
        self.tid = tid; self.first_ts = self.last_ts = ts
        self.in_zone = False; self.stay_start = None; self.last_in_zone_ts = None
        self.consec = 0; self.entries = 0
        self.pts = []; self.heights = []; self.confs = []
        self.emitted = None; self.bbox = None


class RiskEngine:
    def __init__(self, cfg=None, zone=None, start_dt: datetime | None = None):
        self.cfg = {**DEFAULTS, **(cfg or {})}
        self.zone = None if not zone else np.array(zone, np.float32)  # normalised [0..1] polygon
        self.start_dt = start_dt            # wall-clock time of ts=0 (live only); None = unknown
        self.ctx: dict[int, TrackCtx] = {}
        self.last_event_ts = -1e18
        self.entry_times: list[float] = []
        self.unknown_log: list[dict] = []

    # ---- helpers
    def _inside(self, box, frame_wh):
        if self.zone is None:
            return True  # Full frame considered when no specific zone is drawn
        fx, fy = (box[0] + box[2]) / 2 / frame_wh[0], box[3] / frame_wh[1]  # foot point
        return cv2.pointPolygonTest(self.zone.reshape(-1, 1, 2), (float(fx), float(fy)), False) >= 0

    def _loitering(self, c: TrackCtx):
        if len(c.pts) < 3 or not c.heights:
            return False
        p = np.array(c.pts)
        extent = max(np.ptp(p[:, 0]), np.ptp(p[:, 1])) / max(np.mean(c.heights), 1.0)
        return extent <= self.cfg["loiter_extent"]

    def _night(self, ts):
        if not self.cfg["night_enabled"] or self.start_dt is None:
            return None  # not evaluated (unknown time of day)
        return in_night(self.start_dt + timedelta(seconds=ts), self.cfg["night_start"], self.cfg["night_end"])

    def _evaluate(self, c: TrackCtx, ts):
        cfg = self.cfg
        dwell = (c.last_in_zone_ts - c.stay_start) if c.stay_start is not None else 0.0
        repeated = (c.entries >= 2) or (sum(1 for t in self.entry_times if ts - t <= cfg["repeat_window"]) >= cfg["repeat_count"])
        ind = {
            "restricted_zone_entry": c.entries >= 1,
            "zone_dwell": dwell >= cfg["min_duration"],
            "loitering": dwell >= cfg["min_duration"] / 2 and self._loitering(c),
            "repeated_activity": bool(repeated),
            "night_time": self._night(ts),
        }
        score = 0.0
        if ind["zone_dwell"]:
            score += 0.40 + 0.10 * min(max(dwell / cfg["min_duration"] - 1, 0), 1)
        score += 0.25 * bool(ind["loitering"]) + 0.15 * bool(ind["repeated_activity"]) + 0.10 * bool(ind["night_time"])
        n_ind = sum(1 for k in ("zone_dwell", "loitering", "repeated_activity", "night_time") if ind[k])
        conf = float(np.mean(c.confs)) if c.confs else 0.0
        return dwell, ind, round(score, 3), n_ind, conf

    def _event(self, c, level, ts, dwell, ind, score, conf, reason):
        return {"track_id": c.tid, "level": level, "ts": ts, "start_ts": c.stay_start, "duration": round(dwell, 1),
                "score": score, "indicators": ind, "confidence": round(conf, 2), "bbox": c.bbox, "reason": reason,
                "summary": f"Object track #{c.tid} stayed {dwell:.0f}s in a restricted zone "
                           f"(anomaly score {score:.2f}, mean detection confidence {conf:.2f}). "
                           f"Indicators: {', '.join(k for k, v in ind.items() if v) or 'none'}. "
                           "This is an automated anomaly indicator, not a determination of any crime."}

    # ---- main entry
    def update(self, tracks, ts, frame_wh):
        """tracks: tracks updated this frame (objects with .id .box .conf). Returns list of events."""
        cfg, events = self.cfg, []
        seen = set()
        for t in tracks:
            seen.add(t.id)
            c = self.ctx.setdefault(t.id, TrackCtx(t.id, ts))
            c.last_ts = ts; c.bbox = [float(v) for v in t.box]
            inside = self._inside(t.box, frame_wh)
            if inside:
                if c.stay_start is None or (ts - c.last_in_zone_ts) > cfg["exit_grace"]:
                    c.stay_start = ts; c.consec = 0; c.pts = []; c.heights = []; c.confs = []
                    c.entries += 1; self.entry_times.append(ts)
                c.in_zone = True; c.last_in_zone_ts = ts; c.consec += 1
                c.pts.append(((t.box[0] + t.box[2]) / 2, t.box[3])); c.heights.append(t.box[3] - t.box[1])
                c.confs.append(float(t.conf))
            else:
                c.in_zone = False
                if c.last_in_zone_ts is None or ts - c.last_in_zone_ts > cfg["exit_grace"]:
                    c.consec = 0
            if inside and c.emitted != "HIGH_RISK_CANDIDATE":
                dwell, ind, score, n_ind, conf = self._evaluate(c, ts)
                if ind["zone_dwell"] and c.consec >= cfg["min_consecutive"]:
                    if conf < cfg["min_confidence"]:
                        if c.emitted != "UNKNOWN":
                            c.emitted = "UNKNOWN"
                            self.unknown_log.append({"track_id": c.tid, "ts": ts, "reason": "low detection confidence"})
                    elif n_ind >= 2 and score >= cfg["risk_threshold"]:
                        if ts - self.last_event_ts < cfg["cooldown"]:
                            continue  # suppressed by cooldown / duplicate-incident window
                        c.emitted = "HIGH_RISK_CANDIDATE"; self.last_event_ts = ts
                        events.append(self._event(c, "HIGH_RISK_CANDIDATE", ts, dwell, ind, score, conf, "multi-indicator"))
        events += self._finalize_stale(seen, ts)
        return events

    def _finalize_stale(self, seen, ts, force=False):
        """Tracks that disappeared: if they never reached HIGH but had a meaningful stay -> MEDIUM (review)."""
        cfg, out = self.cfg, []
        for tid, c in list(self.ctx.items()):
            if tid in seen or (not force and ts - c.last_ts <= 3.0):
                continue
            if c.emitted in (None, "UNKNOWN") and c.stay_start is not None:
                dwell, ind, score, n_ind, conf = self._evaluate(c, c.last_ts)
                if (dwell >= cfg["min_duration"] / 2 and c.consec >= cfg["min_consecutive"] // 2 and conf >= cfg["min_confidence"]
                        and (n_ind >= 1 or ind["restricted_zone_entry"])
                        and c.last_ts - self.last_event_ts >= cfg["cooldown"]):
                    c.emitted = "MEDIUM_RISK"; self.last_event_ts = c.last_ts
                    out.append(self._event(c, "MEDIUM_RISK", c.last_ts, dwell, ind, score, conf, "partial indicators"))
            del self.ctx[tid]
        return out

    def finish(self, ts):
        """End of a video file: close every open track."""
        return self._finalize_stale(set(), ts, force=True)

    def current_state(self, ts):
        """Live view for the dashboard: strongest active track."""
        best = None
        for c in self.ctx.values():
            if c.stay_start is None or ts - c.last_ts > 3.0:
                continue
            dwell, ind, score, n_ind, conf = self._evaluate(c, ts)
            if best is None or score > best["score"] or (score == best["score"] and dwell > best["duration"]):
                lvl = c.emitted or ("MEDIUM_RISK" if dwell >= self.cfg["min_duration"] / 2 else "LOW_RISK")
                best = {"level": lvl, "score": score, "duration": round(dwell, 1), "indicators": ind, "track_id": c.tid}
        return best or {"level": "LOW_RISK", "score": 0.0, "duration": 0.0, "indicators": {}, "track_id": None}
