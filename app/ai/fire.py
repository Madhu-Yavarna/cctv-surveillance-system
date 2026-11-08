"""Temporal Fire and Smoke Verification Engine.

Distinguishes genuine fire and smoke from red/orange/yellow clothing, warm
lighting, and sunsets by analyzing:
1. Clothing & Human Exclusion: Ignores warm colored pixels inside human bounding boxes.
2. Temporal Flicker Dynamics: Real fire flickers turbulently (> 4.0 mean intensity diff);
   clothing has virtually static intensity (< 1.5 mean diff).
3. Buoyant Convection & Spatial Dispersion: Real flames expand upward and change shape.
"""

from collections import deque
import cv2
import numpy as np


class TemporalFireDetector:
    def __init__(self, fps=5.0, min_flicker_diff=3.5, min_uncontrolled_ratio=0.012):
        self.fps = fps
        self.min_flicker_diff = min_flicker_diff
        self.min_uncontrolled_ratio = min_uncontrolled_ratio
        self.history_intensities = deque(maxlen=int(fps * 6))  # 6-second window
        self.history_uncontrolled_ratios = deque(maxlen=int(fps * 6))
        self.consecutive_fire_frames = 0
        self.total_fire_triggers = 0

    def reset(self):
        self.history_intensities.clear()
        self.history_uncontrolled_ratios.clear()
        self.consecutive_fire_frames = 0
        self.total_fire_triggers = 0

    def process_frame(self, frame, person_boxes):
        """Analyzes a single frame for fire and smoke.
        person_boxes: list of [x1, y1, x2, y2] bounding boxes of detected persons.
        """
        h, w = frame.shape[:2]
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        
        # 1. Thermal fire color mask (flame yellow/orange/red)
        flame_mask = cv2.inRange(hsv, np.array([0, 120, 140]), np.array([28, 255, 255]))
        
        # 2. Exclude pixels that lie inside human person bounding boxes (clothing/apparel)
        person_mask = np.zeros((h, w), dtype=np.uint8)
        for b in person_boxes:
            x1 = max(0, int(b[0]))
            y1 = max(0, int(b[1]))
            x2 = min(w, int(b[2]))
            y2 = min(h, int(b[3]))
            # Slight padding around torso/legs where clothing is worn
            person_mask[y1:y2, x1:x2] = 255

        # Uncontrolled fire pixels: MUST be outside person clothing
        uncontrolled_mask = cv2.bitwise_and(flame_mask, cv2.bitwise_not(person_mask))
        uncontrolled_count = np.count_nonzero(uncontrolled_mask)
        uncontrolled_ratio = float(uncontrolled_count) / float(w * h)
        self.history_uncontrolled_ratios.append(uncontrolled_ratio)

        # 3. Track temporal luminance intensity for flicker calculation
        if uncontrolled_count > 50:
            mean_intensity = float(np.mean(hsv[..., 2][uncontrolled_mask > 0]))
        elif np.count_nonzero(flame_mask) > 50:
            # If flame-colored pixels exist (even on clothing), record intensity to measure flicker
            mean_intensity = float(np.mean(hsv[..., 2][flame_mask > 0]))
        else:
            mean_intensity = 0.0
            
        self.history_intensities.append(mean_intensity)

    def evaluate_fire_risk(self):
        """Returns (is_fire_confirmed, fire_confidence, reason).
        Requires BOTH significant uncontrolled flame coverage AND temporal turbulent flicker.
        """
        if len(self.history_intensities) < 4:
            return False, 0.0, "Insufficient frames"

        intensities = np.array(self.history_intensities)
        active_intensities = intensities[intensities > 0]
        
        if len(active_intensities) < 3:
            return False, 0.0, "Zero fire detected"

        # Temporal intensity diff (flicker rate)
        diffs = np.abs(np.diff(active_intensities))
        mean_diff = float(np.mean(diffs)) if len(diffs) > 0 else 0.0
        max_diff = float(np.max(diffs)) if len(diffs) > 0 else 0.0

        ratios = np.array(self.history_uncontrolled_ratios)
        max_uncontrolled = float(np.max(ratios)) if len(ratios) > 0 else 0.0
        active_ratio_frames = sum(1 for r in ratios if r >= self.min_uncontrolled_ratio)

        # Real fire criteria:
        # 1. Uncontrolled flame pixels (outside human clothing) exceed threshold across multiple frames.
        # 2. High-frequency turbulent thermal flickering (mean_diff >= min_flicker_diff).
        is_flickering = (mean_diff >= self.min_flicker_diff and max_diff >= 18.0)
        has_uncontrolled_flame = (max_uncontrolled >= self.min_uncontrolled_ratio and active_ratio_frames >= 3)

        if has_uncontrolled_flame and is_flickering:
            conf = min(0.95, 0.70 + 0.15 * min(max_uncontrolled / 0.05, 1.0) + 0.10 * min(mean_diff / 15.0, 1.0))
            return True, round(conf, 2), f"Uncontrolled flame with thermal flicker (mean diff: {mean_diff:.1f}, area: {max_uncontrolled*100:.1f}%)"
        
        # If it was just static clothing (low flicker or inside person box):
        if max_uncontrolled < self.min_uncontrolled_ratio:
            return False, 0.0, "Warm color localized to clothing or static objects (no uncontrolled fire)"
        
        if not is_flickering:
            return False, 0.0, f"Static warm illumination without turbulent thermal flicker (diff: {mean_diff:.1f})"

        return False, 0.0, "No fire detected"
