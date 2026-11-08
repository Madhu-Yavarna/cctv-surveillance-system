"""End-to-end analysis of a synthetic video with a FAKE detector (no YOLO weights needed)."""
import sys, os, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["CCTV_DATA"] = tempfile.mkdtemp()
import numpy as np, cv2
from app.ai.pipeline import analyze_video
from app import evidence

tmp = Path(tempfile.mkdtemp()); vid = tmp / "t.avi"
vw = cv2.VideoWriter(str(vid), cv2.VideoWriter_fourcc(*"MJPG"), 25, (320, 240))
for i in range(25 * 24):          # 24 s
    f = np.full((240, 320, 3), 60, np.uint8)
    cv2.rectangle(f, (220, 80), (260, 200), (200, 200, 200), -1)   # "person" in the right half
    vw.write(f)
vw.release()

class FakeDet:
    def __call__(self, frame):
        return [(220.0, 80.0, 260.0, 200.0, 0.88, 0)]

got = []
stats = analyze_video(vid, FakeDet(), {}, [[0.5, 0], [1, 0], [1, 1], [0.5, 1]],
                      lambda ev, f, b, c: got.append((ev, f, b, c)), lambda p: None)
assert [g[0]["level"] for g in got] == ["HIGH_RISK_CANDIDATE"], got
ev, fr, box, conf = got[0]
assert 14 <= ev["ts"] <= 17
evidence.save_snapshot(fr, tmp / "s.jpg", box, f"person {conf:.2f}")
evidence.extract_clip(vid, ev["start_ts"] - 3, ev["ts"] + 3, tmp / "c.mp4")
assert (tmp / "c.mp4").stat().st_size > 1000
print("ok  video -> HIGH candidate at", round(ev["ts"], 1), "s; snapshot+clip written")
print("measured stats:", {k: stats[k] for k in ("frames_analyzed", "measured_analysis_fps", "sampling")})
