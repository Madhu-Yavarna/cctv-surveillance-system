"""YOLOv8n ONNX detector (CPU, onnxruntime). Export weights once with scripts/export_model.py."""
import time
import cv2
import numpy as np
from .. import config

COCO = ["person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat", "traffic light",
        "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow",
        "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase"]


class Detector:
    def __init__(self, path=None, size=640, classes=(0,), conf=0.25, iou=0.45):
        import onnxruntime as ort
        path = path or config.MODEL_PATH
        if not path.exists():
            raise FileNotFoundError(f"YOLO model not found at {path}. Run: python scripts/export_model.py")
        so = ort.SessionOptions(); so.intra_op_num_threads = 4
        self.sess = ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])
        self.inp = self.sess.get_inputs()[0].name
        self.size, self.classes, self.conf, self.iou = size, set(classes), conf, iou
        self.last_ms = 0.0

    def __call__(self, frame):
        t0 = time.perf_counter()
        h, w = frame.shape[:2]
        r = self.size / max(h, w)
        nh, nw = int(round(h * r)), int(round(w * r))
        img = np.full((self.size, self.size, 3), 114, np.uint8)
        px, py = (self.size - nw) // 2, (self.size - nh) // 2
        img[py:py + nh, px:px + nw] = cv2.resize(frame, (nw, nh))
        blob = img[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        out = self.sess.run(None, {self.inp: blob})[0][0].T          # (8400, 84)
        scores = out[:, 4:].max(1); cls = out[:, 4:].argmax(1)
        keep = (scores >= self.conf) & np.isin(cls, list(self.classes))
        out, scores, cls = out[keep], scores[keep], cls[keep]
        res = []
        if len(out):
            cx, cy, bw, bh = out[:, 0], out[:, 1], out[:, 2], out[:, 3]
            x1 = (cx - bw / 2 - px) / r; y1 = (cy - bh / 2 - py) / r
            x2 = (cx + bw / 2 - px) / r; y2 = (cy + bh / 2 - py) / r
            boxes = np.stack([x1, y1, x2 - x1, y2 - y1], 1)
            idx = cv2.dnn.NMSBoxes(boxes.tolist(), scores.tolist(), self.conf, self.iou)
            for i in np.array(idx).flatten():
                res.append((float(np.clip(x1[i], 0, w)), float(np.clip(y1[i], 0, h)),
                            float(np.clip(x2[i], 0, w)), float(np.clip(y2[i], 0, h)), float(scores[i]), int(cls[i])))
        self.last_ms = (time.perf_counter() - t0) * 1000
        return res
