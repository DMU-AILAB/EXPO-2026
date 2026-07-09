import os

import numpy as np
from ultralytics import YOLO
import streamlit as st

DEFAULT_MODEL_PATH = os.path.join(
    os.path.dirname(__file__), "..", "runs/white_cane_v2/weights/best.pt"
)


@st.cache_resource
def _load_model(model_path: str) -> YOLO:
    return YOLO(model_path)


class Detector:
    def __init__(self, model_path: str = DEFAULT_MODEL_PATH):
        self.model_path = model_path
        self.model = _load_model(model_path)

    def detect(self, frame: np.ndarray, conf: float = 0.5) -> list[dict]:
        results = self.model(frame, conf=conf, verbose=False)
        detections = []
        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                cls_id = int(box.cls[0])
                detections.append({
                    "bbox": [x1, y1, x2, y2],
                    "conf": float(box.conf[0]),
                    "class": cls_id,
                    "label": r.names[cls_id],
                })
        return detections
