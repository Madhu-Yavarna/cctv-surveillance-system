"""Core logic tests (need only numpy/opencv/scipy). Detections are scripted -> tests tracking + risk logic, not YOLO accuracy."""
import sys, os, tempfile, hashlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["CCTV_DATA"] = tempfile.mkdtemp()
import numpy as np, cv2
from app.ai.tracker import ByteTracker
from app.ai.risk import RiskEngine
from app import geo, evidence, config

W, H, FPS = 640, 480, 5
ZONE = [[0.5, 0], [1, 0], [1, 1], [0.5, 1]]
passed = 0


def check(name, cond):
    global passed
    assert cond, "FAIL: " + name
    passed += 1; print("ok  ", name)


def person(cx, foot=400, h=200, w=80, conf=0.9):
    return (cx - w / 2, foot - h, cx + w / 2, foot, conf, 0)


def run(frames, zone=ZONE, cfg=None, engine=None, start_dt=None):
    tr = ByteTracker(buffer=FPS * 3)
    eng = engine or RiskEngine(cfg, zone, start_dt)
    events, ids = [], set()
    for i, dets in enumerate(frames):
        ts = i / FPS
        tracks = tr.update(dets, ts)
        ids |= {t.id for t in tracks}
        events += eng.update(tracks, ts, (W, H))
    events += eng.finish(len(frames) / FPS)
    return events, ids, eng


def loiter(seconds, cx=480, conf=0.9):
    rng = np.random.default_rng(1)
    return [[person(cx + rng.normal(0, 2), conf=conf)] for _ in range(int(seconds * FPS))]


# --- tracking
tr = ByteTracker(buffer=15)
seen = set()
for i in range(40):
    dets = [] if 10 <= i < 15 else [person(300 + i * 3)]          # 5-frame occlusion
    if 25 <= i < 30:
        dets = [person(300 + i * 3, conf=0.3)]                   # low-confidence dip
    for t in tr.update(dets, i / FPS):
        seen.add(t.id)
check("ByteTrack keeps one ID through 5-frame occlusion + low-conf dip", len(seen) == 1)
tr = ByteTracker()
ids = set()
for i in range(20):
    for t in tr.update([person(100 + i), person(500 - i)], i / FPS):
        ids.add(t.id)
check("two separate people get two IDs", len(ids) == 2)

# --- risk engine
ev, ids, _ = run(loiter(20))
check("20 s loitering in zone -> exactly one HIGH candidate", [e["level"] for e in ev] == ["HIGH_RISK_CANDIDATE"])
check("HIGH fires at ~15 s, not before", 14.5 <= ev[0]["ts"] <= 16.5)
check("event carries indicators + duration", ev[0]["indicators"]["zone_dwell"] and ev[0]["duration"] >= 15)
ev, _, _ = run(loiter(6))
check("brief stay (6 s) is suppressed (no event)", all(e["level"] != "HIGH_RISK_CANDIDATE" for e in ev))
walk = [[person(330 + i * 4, h=110, w=45)] for i in range(100)]   # slow walk across the zone for >15 s
ev, _, _ = run(walk)
check("walking through zone (not loitering) is not HIGH", all(e["level"] != "HIGH_RISK_CANDIDATE" for e in ev))
ev, _, _ = run(loiter(25, cx=150))
check("activity outside zone -> no event", ev == [])
ev, _, _ = run(loiter(25), zone=None)
check("camera with no zone -> no event", ev == [])
ev, _, _ = run(loiter(25, conf=0.35))
check("low-confidence detections never reach HIGH", all(e["level"] != "HIGH_RISK_CANDIDATE" for e in ev))
two = loiter(20)
for f in two:                                                         # second, overlapping person -> duplicate
    f.append(person(560, foot=420))
ev, _, _ = run(two)
check("duplicate track -> still one alert", sum(e["level"] == "HIGH_RISK_CANDIDATE" for e in ev) == 1)
ev, _, _ = run(loiter(20) + [[]] * 30 + loiter(20))
check("second alert inside cooldown is suppressed", sum(e["level"] == "HIGH_RISK_CANDIDATE" for e in ev) == 1)
ev, _, _ = run(loiter(20) + [[]] * 30 + loiter(20), cfg={"cooldown": 5})
check("second alert after cooldown is allowed", sum(e["level"] == "HIGH_RISK_CANDIDATE" for e in ev) == 2)
ev, _, _ = run(loiter(10) + [[]] * 40)
check("partial stay (10 s) ends as MEDIUM for admin review", [e["level"] for e in ev] == ["MEDIUM_RISK"])
from datetime import datetime
ev, _, _ = run(loiter(20), cfg={"night_enabled": True}, start_dt=datetime(2026, 1, 1, 23, 0))
check("night-time adds indicator (live)", ev and ev[0]["indicators"]["night_time"] is True)
ev, _, _ = run(loiter(20), cfg={"night_enabled": True}, start_dt=None)
check("uploaded video: night indicator not evaluated", ev[0]["indicators"]["night_time"] is None)

# --- geo
d = geo.haversine_km(17.3850, 78.4867, 17.4065, 78.4772)
check("haversine Hyderabad ~2.5 km", 2.3 < d < 2.7)
check("haversine of identical points = 0", geo.haversine_km(10, 10, 10, 10) == 0)
class S:  # noqa
    def __init__(s, n, la, lo, a=True): s.name, s.lat, s.lon, s.active = n, la, lo, a
st, km = geo.nearest_station(17.385, 78.4867, [S("far", 18, 79), S("near", 17.39, 78.49), S("off", 17.385, 78.4867, False)])
check("nearest eligible station ignores inactive", st.name == "near")
check("no stations -> None", geo.nearest_station(1, 1, []) == (None, None))
check("ISO6709 parse", geo.parse_iso6709("+17.3850+078.4867/") == (17.385, 78.4867))

# --- evidence
d = evidence.incident_dir("INC-TEST-001")
vid = d / "src.avi"
vw = cv2.VideoWriter(str(vid), cv2.VideoWriter_fourcc(*"MJPG"), 20, (160, 120))
for i in range(200):
    vw.write(np.full((120, 160, 3), i % 255, np.uint8))
vw.release()
clip = d / "clip.mp4"
secs = evidence.extract_clip(vid, 2, 5, clip)
cap = cv2.VideoCapture(str(clip)); n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)); cap.release()
check("clip extracted (~60 frames)", 50 <= n <= 62 and secs > 2.5)
snap = d / "s.jpg"
evidence.save_snapshot(np.zeros((120, 160, 3), np.uint8), snap, [10, 10, 50, 100], "person 0.92")
h0 = evidence.sha256_file(clip)
check("sha256 matches hashlib", h0 == hashlib.sha256(clip.read_bytes()).hexdigest())
check("verify passes on untouched file", evidence.verify_file(clip, h0)[0])
b = bytearray(clip.read_bytes()); b[100] ^= 1; clip.write_bytes(bytes(b))
check("one flipped byte -> verification fails", not evidence.verify_file(clip, h0)[0])
check("missing file -> verification fails", evidence.verify_file(d / "nope", h0) == (False, None))
for bad in ["../x", "a/../../x", "/etc/passwd", "..\\x", "a\\..\\b", "C:/x", ""]:
    try:
        evidence.safe_join(config.EVIDENCE, bad); ok = False
    except ValueError:
        ok = True
    check(f"path traversal blocked: {bad!r}", ok)
m = evidence.build_manifest({"id": "INC-TEST-001"}, [snap, clip], d)
check("manifest lists hashes", len(m["files"]) == 2 and all(len(f["sha256"]) == 64 for f in m["files"]))
print(f"\nALL {passed} CHECKS PASSED")
