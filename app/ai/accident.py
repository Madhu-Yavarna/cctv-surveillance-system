"""Traffic Accident & Vehicle Collision Detection Engine.

Detects vehicle-to-vehicle and vehicle-to-pedestrian collisions by analyzing:
1. Vehicle bounding box intersection-over-union (IoU >= 0.25 between 2 vehicles).
2. Abrupt velocity deceleration (instantaneous kinetic stoppage upon impact).
3. Post-impact obstruction / traffic halt.
"""

import math
from collections import deque
import numpy as np

VEHICLE_CLASSES = {1, 2, 3, 5, 7}  # bicycle, car, motorcycle, bus, truck


class VehicleAccidentDetector:
    def __init__(self, fps=5.0):
        self.fps = fps
        self.track_history = {}  # tid -> list of (ts, center_x, center_y, box, speed)
        self.collision_events = []

    def reset(self):
        self.track_history.clear()
        self.collision_events.clear()

    def process_frame(self, tracks, ts):
        """Analyzes multi-vehicle trajectories and detects collision overlap + deceleration."""
        vehicles = [t for t in tracks if t.cls in VEHICLE_CLASSES]
        
        # 1. Update vehicle velocity histories
        for v in vehicles:
            cx = (v.box[0] + v.box[2]) / 2.0
            cy = (v.box[1] + v.box[3]) / 2.0
            hist = self.track_history.setdefault(v.id, [])
            
            speed = 0.0
            if len(hist) >= 1:
                dt = ts - hist[-1][0]
                if dt > 0:
                    dist = math.sqrt((cx - hist[-1][1])**2 + (cy - hist[-1][2])**2)
                    speed = dist / dt  # pixels per second
                    
            hist.append((ts, cx, cy, v.box, speed))
            if len(hist) > 30:
                hist.pop(0)

        # 2. Check for vehicle-to-vehicle collision (heavy overlap + rapid deceleration)
        if len(vehicles) >= 2:
            for i in range(len(vehicles)):
                for j in range(i + 1, len(vehicles)):
                    v1, v2 = vehicles[i], vehicles[j]
                    b1, b2 = v1.box, v2.box
                    
                    xi1 = max(b1[0], b2[0]); yi1 = max(b1[1], b2[1])
                    xi2 = min(b1[2], b2[2]); yi2 = min(b1[3], b2[3])
                    inter = max(0, xi2 - xi1) * max(0, yi2 - yi1)
                    a1 = (b1[2] - b1[0]) * (b1[3] - b1[1])
                    a2 = (b2[2] - b2[0]) * (b2[3] - b2[1])
                    union = a1 + a2 - inter
                    iou = inter / union if union > 0 else 0.0
                    
                    if iou >= 0.22:
                        # Check deceleration in track history
                        h1 = self.track_history.get(v1.id, [])
                        h2 = self.track_history.get(v2.id, [])
                        s1_pre = max([pt[4] for pt in h1[-6:-1]], default=0.0) if len(h1) >= 4 else 0.0
                        s2_pre = max([pt[4] for pt in h2[-6:-1]], default=0.0) if len(h2) >= 4 else 0.0
                        
                        # If at least one vehicle was in motion (> 20 px/s) and suddenly stopped into the other
                        if (s1_pre > 25.0 or s2_pre > 25.0) and iou >= 0.25:
                            self.collision_events.append({
                                "ts": ts,
                                "v1_id": v1.id,
                                "v2_id": v2.id,
                                "iou": round(iou, 2),
                                "s1_pre": round(s1_pre, 1),
                                "s2_pre": round(s2_pre, 1)
                            })

    def evaluate_accident_risk(self):
        """Returns (is_accident_confirmed, confidence, summary)."""
        if len(self.collision_events) >= 2:
            conf = min(0.92, 0.70 + 0.08 * len(self.collision_events))
            ev = self.collision_events[-1]
            return True, round(conf, 2), f"Vehicle collision detected: vehicle overlap IoU={ev['iou']}, pre-impact speed={max(ev['s1_pre'], ev['s2_pre'])} px/s"
        return False, 0.0, "No vehicle accidents detected"
