"""사람 재식별 임베딩 모델 비교 — 같은 사람/다른 사람 쌍의 구분력과 지연.

로컬 전용. 양성 쌍 = 같은 트랙의 1초 이상 떨어진 시점, 음성 쌍 = 한 프레임에 동시에 보이는 서로 다른 트랙.
결과는 docs/reid_embedding_evaluation.md. 가중치는 저장소에 없다 — 같은 문서의 "가중치" 절 참고.

    python tools/eval/eval_reid_embeddings.py runs/reid_eval/crops.pkl runs/reid_eval/models/
"""
import pickle, itertools, collections, time, sys
import numpy as np, cv2
import onnxruntime as ort
import openvino as ov
M = __import__("sys").argv[2].rstrip("/") + "/"
data = pickle.load(open(__import__("sys").argv[1],"rb"))
imgs = [cv2.imdecode(np.frombuffer(d[3],np.uint8),cv2.IMREAD_COLOR) for d in data]   # BGR 256x128

MEAN=np.array([0.485,0.456,0.406],np.float32); STD=np.array([0.229,0.224,0.225],np.float32)
def prep_rgb_norm(im):   # torchreid 계열: RGB, /255, ImageNet 정규화, NCHW
    x=cv2.cvtColor(im,cv2.COLOR_BGR2RGB).astype(np.float32)/255.0
    return ((x-MEAN)/STD).transpose(2,0,1)[None]
def prep_bgr_raw(im):    # OpenVINO retail 계열: BGR 원본 0~255, NCHW
    return im.astype(np.float32).transpose(2,0,1)[None]

class OnnxModel:
    def __init__(s,path,prep):
        so=ort.SessionOptions(); so.intra_op_num_threads=1
        s.sess=ort.InferenceSession(path,so,providers=["CPUExecutionProvider"]); s.prep=prep
        s.inp=s.sess.get_inputs()[0].name
    def __call__(s,im): return s.sess.run(None,{s.inp:s.prep(im)})[0].reshape(-1)
class OvModel:
    def __init__(s,xml,prep):
        core=ov.Core(); core.set_property("CPU",{"INFERENCE_NUM_THREADS":1})
        s.m=core.compile_model(xml,"CPU"); s.prep=prep
    def __call__(s,im): return np.array(list(s.m(s.prep(im)).values())[0]).reshape(-1)

models = {
 "osnet_x0.25(MSMT17,onnx)": OnnxModel(M+"osnet_x0_25_msmt17_dyn.onnx",prep_rgb_norm),
 "openvino-0288(x0.25급)":   OvModel(M+"person-reidentification-retail-0288.xml",prep_bgr_raw),
 "openvino-0287(x0.5급)":    OvModel(M+"person-reidentification-retail-0287.xml",prep_bgr_raw),
 "openvino-0277(x1.0급)":    OvModel(M+"person-reidentification-retail-0277.xml",prep_bgr_raw),
}
by=collections.defaultdict(list)
for i,d in enumerate(data): by[(d[0],d[1])].append(i)
sel={k:[ids[j] for j in np.linspace(0,len(ids)-1,min(12,len(ids))).astype(int)] for k,ids in by.items() if len(ids)>=2}
frames=collections.defaultdict(list)
for i,d in enumerate(data): frames[(d[0],round(d[2],3))].append(i)

def pairs(skip):
    pos=[(a,b) for (v,t),ids in sel.items() if v not in skip for a,b in itertools.combinations(ids,2) if data[b][2]-data[a][2]>=1.0]
    neg=[(a,b) for (v,t),ids in frames.items() if v not in skip for a,b in itertools.combinations(ids,2) if data[a][1]!=data[b][1]]
    return pos,neg
need=set(i for ids in sel.values() for i in ids)|set(i for ids in frames.values() for i in ids)
for name,model in models.items():
    t0=time.perf_counter(); E={}
    for n,i in enumerate(sorted(need)):
        e=model(imgs[i]); E[i]=e/ (np.linalg.norm(e)+1e-9)
    ms=(time.perf_counter()-t0)/len(need)*1000
    cos=lambda a,b: 1-float(np.dot(E[a],E[b]))          # 코사인 거리 0(같음)~2
    out=[f"{name:26s} dim={len(next(iter(E.values()))):4d} {ms:5.1f}ms/크롭(1스레드, PC)"]
    for skip,label in (((),"전체"),(("1.mp4",),"1.mp4제외")):
        P,N=pairs(skip); pos=np.array([cos(a,b) for a,b in P]); neg=np.array([cos(a,b) for a,b in N])
        rng=np.random.default_rng(0); p=rng.choice(pos,20000); n_=rng.choice(neg,20000)
        auc=(p<n_).mean()+0.5*(p==n_).mean()
        r={}
        for fm in (0.01,0.05):
            thr=np.percentile(neg,fm*100); r[fm]=((pos<=thr).mean(),thr)
        out.append(f"   {label}: AUC={auc:.3f} | 오합침1%→같은사람통과 {r[0.01][0]:.2f}(thr {r[0.01][1]:.3f}) | 오합침5%→{r[0.05][0]:.2f}(thr {r[0.05][1]:.3f})")
    print("\n".join(out),flush=True)
