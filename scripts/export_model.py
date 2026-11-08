"""One-time: download YOLOv8n and export to ONNX (needs internet + `pip install ultralytics`)."""
import shutil
from pathlib import Path
from ultralytics import YOLO
out = Path(__file__).resolve().parent.parent / "models"; out.mkdir(exist_ok=True)
p = YOLO("yolov8n.pt").export(format="onnx", imgsz=640, opset=12, simplify=False)
shutil.move(p, out / "yolov8n.onnx"); print("Saved", out / "yolov8n.onnx")
