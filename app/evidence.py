"""Snapshots, clips, SHA-256 hashing, manifest, verification, path safety."""
import hashlib, json, shutil, subprocess
from datetime import datetime
from pathlib import Path
import cv2
from . import config


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_join(root: Path, rel: str) -> Path:
    """Resolve rel under root; reject traversal (../, absolute, backslash, drive letters)."""
    if not rel or "\\" in rel or rel.startswith("/") or ":" in rel or ".." in Path(rel).parts or "\x00" in rel:
        raise ValueError("unsafe path")
    p = (root / rel).resolve()
    if not p.is_relative_to(root.resolve()):
        raise ValueError("unsafe path")
    return p


def incident_dir(code: str) -> Path:
    d = safe_join(config.EVIDENCE, code)
    d.mkdir(parents=True, exist_ok=True)
    return d


def draw_box(frame, box, label):
    x1, y1, x2, y2 = [int(v) for v in box]
    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
    cv2.rectangle(frame, (x1, max(y1 - th - 8, 0)), (x1 + tw + 6, max(y1, th + 8)), (0, 0, 255), -1)
    cv2.putText(frame, label, (x1 + 3, max(y1 - 5, th + 3)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)


def save_snapshot(frame, path, box=None, label="", stamp=""):
    img = frame.copy()
    if box is not None:
        draw_box(img, box, label)
    if stamp:
        cv2.putText(img, stamp, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 90])


def _get_ffmpeg_exe() -> str | None:
    if shutil.which("ffmpeg"):
        return shutil.which("ffmpeg")
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _transcode(path: Path):
    """Re-encode to H.264 so browsers can play it."""
    exe = _get_ffmpeg_exe()
    if not exe:
        return False
    tmp = path.with_suffix(".h264.mp4")
    r = subprocess.run([exe, "-y", "-loglevel", "error", "-i", str(path), "-c:v", "libx264", "-preset", "veryfast",
                        "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(tmp)], capture_output=True)
    if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
        tmp.replace(path)
        return True
    tmp.unlink(missing_ok=True)
    return False


def write_clip(frames, fps, path: Path):
    if not frames:
        raise ValueError("no frames for clip")
    h, w = frames[0].shape[:2]
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), max(fps, 1), (w, h))
    for f in frames:
        vw.write(f if f.shape[:2] == (h, w) else cv2.resize(f, (w, h)))
    vw.release()
    return _transcode(path)


def extract_clip(src, start_s, end_s, path: Path, max_seconds=90):
    """Cut [start_s, end_s] out of a recorded video file."""
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        raise IOError("cannot open source video")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps if cap.get(cv2.CAP_PROP_FRAME_COUNT) else 1e9
    start_s = max(0.0, start_s); end_s = min(end_s, start_s + max_seconds, total)
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(start_s * fps))
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    n, target = 0, int((end_s - start_s) * fps)
    while n < target:
        ok, fr = cap.read()
        if not ok:
            break
        vw.write(fr); n += 1
    vw.release(); cap.release()
    if n == 0:
        raise IOError("clip is empty")
    _transcode(path)
    return n / fps


def build_manifest(incident: dict, files: list[Path], root: Path) -> dict:
    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "note": "A matching hash shows a file is unchanged since it was recorded; it does not by itself prove the original footage is authentic.",
        "incident": incident,
        "files": [{"name": f.name, "size_bytes": f.stat().st_size, "sha256": sha256_file(f)} for f in files],
    }


def verify_file(path: Path, expected: str):
    """Recompute the hash. Returns (ok, actual). Missing file -> (False, None)."""
    if not path.exists():
        return False, None
    actual = sha256_file(path)
    return actual == expected, actual
