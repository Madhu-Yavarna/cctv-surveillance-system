"""ByteTrack (Zhang et al., 2022): Kalman prediction + two-stage IoU association
(high-confidence detections first, then low-confidence ones against leftover tracks)."""
import numpy as np
from scipy.optimize import linear_sum_assignment


def iou_matrix(a, b):
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)))
    a = np.asarray(a, float); b = np.asarray(b, float)
    x1 = np.maximum(a[:, None, 0], b[None, :, 0]); y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2]); y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    aa = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1]); ab = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return inter / (aa[:, None] + ab[None, :] - inter + 1e-9)


class Kalman:
    """Constant-velocity filter on (cx, cy, w, h)."""
    def __init__(self):
        self.F = np.eye(8); self.F[:4, 4:] = np.eye(4)
        self.H = np.eye(4, 8)

    def init(self, box):
        cx, cy, w, h = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2, box[2] - box[0], box[3] - box[1]
        m = np.array([cx, cy, w, h, 0, 0, 0, 0], float)
        P = np.diag([w, h, w, h, 10 * w, 10 * h, 10 * w, 10 * h]) ** 2 * 0.01
        return m, P

    def predict(self, m, P):
        w, h = max(m[2], 1), max(m[3], 1)
        Q = np.diag([w, h, w, h, w, h, w, h]) ** 2 * 0.0004
        return self.F @ m, self.F @ P @ self.F.T + Q

    def update(self, m, P, box):
        z = np.array([(box[0] + box[2]) / 2, (box[1] + box[3]) / 2, box[2] - box[0], box[3] - box[1]], float)
        w, h = max(m[2], 1), max(m[3], 1)
        R = np.diag([w, h, w, h]) ** 2 * 0.0025
        S = self.H @ P @ self.H.T + R
        K = P @ self.H.T @ np.linalg.inv(S)
        m = m + K @ (z - self.H @ m)
        P = (np.eye(8) - K @ self.H) @ P
        return m, P


class Track:
    _next = 1

    def __init__(self, box, conf, cls, ts, kf):
        self.id = Track._next; Track._next += 1
        self.kf = kf
        self.mean, self.cov = kf.init(box)
        self.conf, self.cls = conf, cls
        self.hits, self.since_update = 1, 0
        self.state = "tracked"
        self.start_ts = self.last_ts = ts
        self.history = [(ts, tuple(box), conf)]  # bounding-box history

    @property
    def box(self):
        cx, cy, w, h = self.mean[:4]
        return np.array([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2])

    def predict(self):
        self.mean, self.cov = self.kf.predict(self.mean, self.cov)
        self.since_update += 1

    def update(self, box, conf, cls, ts):
        self.mean, self.cov = self.kf.update(self.mean, self.cov, box)
        self.conf, self.cls = conf, cls
        self.hits += 1; self.since_update = 0; self.state = "tracked"; self.last_ts = ts
        self.history.append((ts, tuple(box), conf))
        if len(self.history) > 600:
            self.history = self.history[-600:]


class ByteTracker:
    def __init__(self, high_thresh=0.5, low_thresh=0.1, match_thresh=0.8, second_thresh=0.5, buffer=15):
        self.high, self.low = high_thresh, low_thresh
        self.match_thresh, self.second_thresh, self.buffer = match_thresh, second_thresh, buffer
        self.kf = Kalman()
        self.tracked: list[Track] = []
        self.lost: list[Track] = []
        self.removed_last: list[Track] = []

    @staticmethod
    def _assign(tracks, boxes, max_cost):
        if not tracks or len(boxes) == 0:
            return [], list(range(len(tracks))), list(range(len(boxes)))
        cost = 1 - iou_matrix([t.box for t in tracks], boxes)
        r, c = linear_sum_assignment(cost)
        matches, ut, ud = [], set(range(len(tracks))), set(range(len(boxes)))
        for i, j in zip(r, c):
            if cost[i, j] <= max_cost:
                matches.append((i, j)); ut.discard(i); ud.discard(j)
        return matches, sorted(ut), sorted(ud)

    def update(self, dets, ts):
        """dets: list of (x1,y1,x2,y2,conf,cls). Returns tracks updated this frame."""
        dets = list(dets)
        hi = [d for d in dets if d[4] >= self.high]
        lo = [d for d in dets if self.low <= d[4] < self.high]
        pool = self.tracked + self.lost
        for t in pool:
            t.predict()
        m, ut, ud = self._assign(pool, [d[:4] for d in hi], self.match_thresh)
        updated = []
        for i, j in m:
            pool[i].update(hi[j][:4], hi[j][4], hi[j][5], ts); updated.append(pool[i])
        rem = [pool[i] for i in ut if pool[i].state == "tracked"]
        m2, _, _ = self._assign(rem, [d[:4] for d in lo], self.second_thresh)
        for i, j in m2:
            rem[i].update(lo[j][:4], lo[j][4], lo[j][5], ts); updated.append(rem[i])
        upd_ids = {t.id for t in updated}
        for t in pool:
            if t.id not in upd_ids:
                t.state = "lost"
        for j in ud:
            d = hi[j]
            updated.append(Track(d[:4], d[4], d[5], ts, self.kf))
        upd_ids = {t.id for t in updated}
        allt = [t for t in pool if t.id not in upd_ids] + updated
        self.removed_last = [t for t in allt if t.state == "lost" and t.since_update > self.buffer]
        self.tracked = [t for t in allt if t.state == "tracked"]
        self.lost = [t for t in allt if t.state == "lost" and t.since_update <= self.buffer]
        return updated
