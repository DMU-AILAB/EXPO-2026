"""평가 영상에서 사람 크롭(128x256)을 뽑아 캐시한다 — eval_reid_embeddings.py의 입력.

로컬 전용. 영상마다 6fps로 yolov8l 사람 탐지 + SimpleTracker 트랙 id를 붙여 (영상, 트랙 id, 시각, jpg, 면적)을 저장한다.
같은 트랙 id를 "같은 사람"의 근사 정답으로 쓰므로, 트래커의 id 오류가 정답 잡음으로 들어간다(모든 모델에 똑같이 불리).

    python tools/eval/cache_person_crops.py runs/reid_eval/crops.pkl
"""
import sys, glob, os, pickle
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "device"))
import cv2, numpy as np
from ultralytics import YOLO
import simple_tracker
model = YOLO("weights/yolov8l.pt")
out = []   # (video, tid, t, crop32x64, simul_ids)
for path in sorted(glob.glob("datasets/videos/*.mp4")):
    name = os.path.basename(path)
    cap = cv2.VideoCapture(path); fps = cap.get(cv2.CAP_PROP_FPS) or 30
    step = max(1, int(round(fps/6))); tr = simple_tracker.SimpleTracker(); i=0
    while True:
        ok, fr = cap.read()
        if not ok: break
        if i % step == 0:
            r = model.predict(fr, classes=[0], conf=0.4, imgsz=640, verbose=False)[0]
            dets = [{"bbox":b.xyxy[0].tolist(),"conf":float(b.conf),"class":1,"label":"person"} for b in r.boxes]
            t = i/fps; fh, fw = fr.shape[:2]
            for tk in tr.update(dets, t):
                if tk["age"]!=0: continue
                x1,y1,x2,y2=[int(v) for v in tk["bbox"]]; x1,x2=max(0,x1),min(fw,x2); y1,y2=max(0,y1),min(fh,y2)
                if x2-x1<12 or y2-y1<24: continue
                out.append((name, tk["track_id"], t, cv2.imencode(".jpg", cv2.resize(fr[y1:y2,x1:x2],(128,256),interpolation=cv2.INTER_AREA))[1].tobytes(), (x2-x1)*(y2-y1)))
        i+=1
pickle.dump(out, open(sys.argv[1],"wb")); print(len(out))
