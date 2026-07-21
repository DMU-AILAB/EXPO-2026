import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# streamlit-drawable-canvas 0.9.3 호환성 패치
# Streamlit 1.37+에서 image_to_url 시그니처 변경:
#   구버전: image_to_url(image, width: int, clamp, channels, fmt, id)
#   신버전: image_to_url(image, layout_config: LayoutConfig, clamp, channels, fmt, id)
import streamlit.elements.image as _st_img_module
if not hasattr(_st_img_module, 'image_to_url'):
    from streamlit.elements.lib.image_utils import image_to_url as _new_image_to_url
    from streamlit.elements.lib.layout_utils import LayoutConfig as _LayoutConfig

    def _compat_image_to_url(image, width_or_layout, clamp, channels, output_format, image_id):
        if isinstance(width_or_layout, int):
            width_or_layout = _LayoutConfig(width=width_or_layout)
        return _new_image_to_url(image, width_or_layout, clamp, channels, output_format, image_id)

    _st_img_module.image_to_url = _compat_image_to_url

import streamlit as st
import cv2
import numpy as np
import time
import tempfile
import tkinter as tk
from tkinter import filedialog
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

from simulator.detector import Detector, DEFAULT_MODEL_PATH
from simulator.roi_manager import ROIManager
from simulator.trigger_dispatcher import TriggerDispatcher
from audio_trigger import AudioPlayer
from simple_tracker import SimpleTracker
from cane_person_assoc import CANE_CLASS_ID, PERSON_CLASS_ID, associate
from foot_traffic_counter import FootTrafficCounter, read_daily_totals

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="VisionGuide Simulator",
    page_icon="🦯",
    layout="wide",
)

CANVAS_W = 640
ROI_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "rois.json")
FOOT_TRAFFIC_DB_PATH = os.path.join(os.path.dirname(__file__), "foot_traffic_sim.db")
STATS_POLL_INTERVAL = 2.0

# 지팡이 트랙이 이만큼 연속으로 거의 안 움직이면(SimpleTracker.static_frames) 배경
# 오탐지(케이블/문틀 경계선 등)로 간주해 ROI 트리거 대상에서 제외한다.
STATIC_CANE_SUPPRESS_FRAMES = 24

_ROTATE_MAP = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}


def apply_rotation(frame, degrees: int):
    """camera_live_pi.py의 _apply_rotation()과 동일 원칙 — read() 직후, 추론 이전에 적용."""
    flag = _ROTATE_MAP.get(degrees % 360)
    return cv2.rotate(frame, flag) if flag is not None else frame


def filter_excluded(detections: list, roi_manager: "ROIManager", frame) -> list:
    """제외구역(zone_type="exclude") 안에 bbox 중심이 있는 detection을 트래킹 이전에 걸러낸다
    (camera_live_pi.py의 _filter_excluded()와 동일 로직)."""
    h, w = frame.shape[:2]
    return [
        d for d in detections
        if not roi_manager.is_excluded(
            ((d["bbox"][0] + d["bbox"][2]) / 2) / w,
            ((d["bbox"][1] + d["bbox"][3]) / 2) / h,
        )
    ]

# ---------------------------------------------------------------------------
# Session state initialization
# ---------------------------------------------------------------------------
def _init():
    defaults = {
        "running": False,
        "edit_mode": False,
        "roi_manager": (
            ROIManager.load(ROI_CONFIG_PATH)
            if os.path.exists(ROI_CONFIG_PATH)
            else ROIManager()
        ),
        "dispatcher": TriggerDispatcher(),
        "audio_player": AudioPlayer(),
        "event_log": [],
        "cap": None,
        "frozen_frame": None,
        "temp_video_path": None,
        "last_announcement": "",
        "last_announcement_time": 0.0,
        "detector": None,
        "current_model_path": None,
        "canvas_key": 0,  # 저장 후 캔버스 리셋용
        "tracker": None,
        "foot_counter": FootTrafficCounter(FOOT_TRAFFIC_DB_PATH, commit_interval_sec=5.0),
        "traffic_stats": {"date": "", "total_count": 0, "cane_user_count": 0},
        "traffic_stats_read_at": 0.0,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init()

# ---------------------------------------------------------------------------
# Helper: persist ROI list to file
# ---------------------------------------------------------------------------
def save_rois():
    st.session_state.roi_manager.save(ROI_CONFIG_PATH)


# ---------------------------------------------------------------------------
# Helper: open OS file picker for audio files (tkinter, server-side)
# ---------------------------------------------------------------------------
def browse_audio_file() -> str:
    root = tk.Tk()
    root.withdraw()
    root.wm_attributes("-topmost", 1)
    path = filedialog.askopenfilename(
        title="오디오 파일 선택",
        filetypes=[
            ("MP3 파일", "*.mp3"),
            ("오디오 파일", "*.mp3 *.wav *.ogg"),
            ("모든 파일", "*.*"),
        ],
    )
    root.destroy()
    return path or ""

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("🦯 VisionGuide Simulator")
    st.divider()

    # --- Camera source ---
    st.subheader("카메라 소스")
    source_type = st.radio(
        "소스",
        ["웹캠", "영상 파일"],
        horizontal=True,
        label_visibility="collapsed",
    )

    video_source = None
    if source_type == "웹캠":
        cam_idx = st.number_input("웹캠 인덱스", 0, 10, 0)
        video_source = int(cam_idx)
    else:
        uploaded = st.file_uploader("영상 파일 업로드", type=["mp4", "avi", "mov", "mkv"])
        if uploaded:
            if st.session_state.temp_video_path is None:
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
                tmp.write(uploaded.read())
                tmp.close()
                st.session_state.temp_video_path = tmp.name
            video_source = st.session_state.temp_video_path

    rotation = st.selectbox("회전", [0, 90, 180, 270], index=0, help="카메라 장착 각도 보정")

    # --- Model ---
    st.subheader("모델")
    model_path = st.text_input("가중치 경로", value=DEFAULT_MODEL_PATH)
    conf_threshold = st.slider("신뢰도 임계값", 0.1, 1.0, 0.5, 0.05)

    if st.session_state.current_model_path != model_path:
        st.session_state.detector = Detector(model_path)
        st.session_state.current_model_path = model_path

    # --- Pipeline ---
    st.subheader("파이프라인")
    debounce = st.slider("디바운싱 (초)", 0.1, 2.0, 0.5, 0.1)
    cooldown_sec = st.slider("쿨다운 (초)", 1.0, 30.0, 10.0, 0.5)

    disp: TriggerDispatcher = st.session_state.dispatcher
    disp.debounce = debounce
    disp.cooldown = cooldown_sec

    # --- ROI management ---
    st.subheader("ROI 관리")
    edit_mode_toggle = st.toggle("편집 모드", value=st.session_state.edit_mode)
    if edit_mode_toggle != st.session_state.edit_mode:
        st.session_state.edit_mode = edit_mode_toggle

    if st.button("전체 초기화", use_container_width=True):
        st.session_state.roi_manager.clear()
        st.session_state.dispatcher.clear()
        save_rois()
        st.rerun()

    rois = st.session_state.roi_manager.rois
    if rois:
        st.caption(f"등록된 ROI ({len(rois)}개)")
        for roi in rois:
            r_col1, r_col2 = st.columns([4, 1])
            badge = "🚫 제외구역" if getattr(roi, "zone_type", "trigger") == "exclude" else roi.announcement_text
            r_col1.caption(f"**{roi.name}**: {badge}")
            if r_col2.button("삭제", key=f"del_{roi.name}"):
                st.session_state.roi_manager.remove(roi.name)
                st.session_state.dispatcher.on_not_detected(roi.name)
                save_rois()
                st.rerun()

    st.divider()

    # --- Start / Stop ---
    if not st.session_state.running:
        start_clicked = st.button("▶ 시작", type="primary", use_container_width=True)
        if start_clicked:
            if video_source is None:
                st.error("영상 파일을 업로드하거나 웹캠을 선택하세요.")
            else:
                cap = cv2.VideoCapture(video_source)
                if cap.isOpened():
                    st.session_state.cap = cap
                    st.session_state.running = True
                    st.session_state.edit_mode = False
                    st.session_state.tracker = SimpleTracker()
                    st.rerun()
                else:
                    st.error("카메라/파일을 열 수 없습니다.")
    else:
        if st.button("⏹ 정지", type="secondary", use_container_width=True):
            st.session_state.running = False
            if st.session_state.cap:
                st.session_state.cap.release()
                st.session_state.cap = None
            if st.session_state.foot_counter is not None:
                st.session_state.foot_counter.finalize_all()
                st.session_state.foot_counter.flush()
            st.rerun()

# ---------------------------------------------------------------------------
# Main layout
# ---------------------------------------------------------------------------
main_col, status_col = st.columns([7, 3])

with main_col:
    st.subheader("실시간 영상")
    frame_ph = st.empty()

with status_col:
    st.subheader("상태")
    status_ph = st.empty()
    st.subheader("유동인구 통계")
    stats_ph = st.empty()
    st.subheader("이벤트 로그")
    log_ph = st.empty()

# ---------------------------------------------------------------------------
# Helper: Korean-aware text rendering (cv2.putText is ASCII only)
# ---------------------------------------------------------------------------
_KO_FONT = None

def _get_ko_font(size: int = 18):
    global _KO_FONT
    if _KO_FONT is not None:
        return _KO_FONT
    candidates = [
        "C:/Windows/Fonts/malgun.ttf",
        "C:/Windows/Fonts/malgunbd.ttf",
        "C:/Windows/Fonts/gulim.ttc",
        "C:/Windows/Fonts/batang.ttc",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                _KO_FONT = ImageFont.truetype(path, size)
                return _KO_FONT
            except Exception:
                continue
    _KO_FONT = ImageFont.load_default()
    return _KO_FONT


def _put_text(pil_draw: ImageDraw.ImageDraw, text: str, pos: tuple,
              color_bgr: tuple, font_size: int = 18):
    """PIL Draw로 한글 포함 텍스트를 BGR → RGB 변환 후 렌더링."""
    r, g, b = color_bgr[2], color_bgr[1], color_bgr[0]
    font = _get_ko_font(font_size)
    pil_draw.text(pos, text, font=font, fill=(r, g, b, 255))


# ---------------------------------------------------------------------------
# Helper: draw overlays on frame
# ---------------------------------------------------------------------------
def draw_overlays(frame, tracks, cane_person_map, rois, dispatcher, now):
    h, w = frame.shape[:2]
    text_items = []  # collect (pos, text, color_bgr, size) for PIL pass

    for roi in rois:
        pts = np.array(
            [[int(x * w), int(y * h)] for x, y in roi.points],
            dtype=np.int32,
        )
        if len(pts) < 3:
            continue
        is_exclude = getattr(roi, "zone_type", "trigger") == "exclude"
        remaining = dispatcher.cooldown_remaining(roi.name, now)
        color = (0, 0, 255) if is_exclude else ((0, 0, 200) if remaining > 0 else roi.color)

        overlay = frame.copy()
        cv2.fillPoly(overlay, [pts], color)
        cv2.addWeighted(overlay, 0.15 if is_exclude else 0.2, frame, 0.85 if is_exclude else 0.8, 0, frame)
        cv2.polylines(frame, [pts], isClosed=True, color=color, thickness=2)

        cx = int(np.mean(pts[:, 0]))
        cy = int(np.mean(pts[:, 1]))
        label = ("🚫 " if is_exclude else "") + roi.name
        if remaining > 0 and not is_exclude:
            label += f" ({remaining:.1f}s)"
        text_items.append((max(cx - 40, 0), max(cy - 10, 0), label, color, 18))

    for trk in tracks:
        x1, y1, x2, y2 = trk["bbox"]
        track_id = trk.get("track_id", "")
        label_name = trk.get("label", str(trk.get("class", "")))

        if trk["class"] == CANE_CLASS_ID:
            color = (0, 165, 255)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            # 하단 10% 인식 구간 표시
            strip_y = int(y2 - (y2 - y1) * 0.10)
            cv2.rectangle(frame, (x1, strip_y), (x2, y2), (0, 255, 255), 2)
            text = f"{label_name} #{track_id} {trk['conf']:.2f}"
        else:
            accompanied = cane_person_map.get(track_id, False)
            color = (255, 0, 0) if accompanied else (160, 160, 160)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            text = f"{label_name} #{track_id}" + (" 지팡이 동반" if accompanied else "")

        text_items.append((x1, max(y1 - 20, 0), text, color, 15))

    # PIL pass: render all text (Korean-safe)
    pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).convert("RGBA")
    draw = ImageDraw.Draw(pil_img)
    for x, y, text, color_bgr, size in text_items:
        _put_text(draw, text, (x, y), color_bgr, size)
    frame = cv2.cvtColor(np.array(pil_img.convert("RGB")), cv2.COLOR_RGB2BGR)

    return frame

# ---------------------------------------------------------------------------
# Helper: render status panel
# ---------------------------------------------------------------------------
def render_status(tracks, active_roi_names, now):
    with status_ph.container():
        if st.session_state.running:
            if tracks:
                st.success(f"탐지 중 — {len(tracks)}개 객체")
            else:
                st.info("탐지 없음")
        elif st.session_state.edit_mode:
            st.info("ROI 편집 중")
        else:
            st.warning("정지됨")

        rois = st.session_state.roi_manager.rois
        if rois:
            st.divider()
            for roi in rois:
                remaining = st.session_state.dispatcher.cooldown_remaining(roi.name, now)
                if remaining > 0:
                    st.warning(f"⏳ **{roi.name}** 쿨다운 {remaining:.1f}s")
                elif roi.name in active_roi_names:
                    st.error(f"🔴 **{roi.name}** 점유 중")
                else:
                    st.write(f"✅ **{roi.name}** 대기")

        if time.time() - st.session_state.last_announcement_time < 3.0:
            st.divider()
            st.info(f"🔊 {st.session_state.last_announcement}")

# ---------------------------------------------------------------------------
# Helper: render foot-traffic stats panel
# ---------------------------------------------------------------------------
def render_traffic_stats():
    with stats_ph.container():
        stats = st.session_state.traffic_stats
        c1, c2 = st.columns(2)
        c1.metric("오늘 유동인구", stats.get("total_count", 0))
        c2.metric("지팡이 사용자", stats.get("cane_user_count", 0))

# ---------------------------------------------------------------------------
# Helper: render event log
# ---------------------------------------------------------------------------
def render_log():
    with log_ph.container():
        log = st.session_state.event_log
        if log:
            for entry in log[:20]:
                st.caption(f"`{entry['time']}` **{entry['roi']}** — {entry['text']}")
        else:
            st.caption("이벤트 없음")

# ---------------------------------------------------------------------------
# Helper: extract ALL polygons from canvas JSON
# ---------------------------------------------------------------------------
def extract_all_polygon_points(json_data: dict, canvas_w: int, canvas_h: int) -> list:
    """캔버스에 그려진 모든 폴리곤의 정규화 좌표 리스트를 반환.

    streamlit-drawable-canvas(Fabric.js 4.x) 좌표 규칙:
    - path 배열의 M/L 좌표는 절대 캔버스 좌표(px)로 저장됨
    - left/top은 originX/Y='center'이므로 bbox 중심 위치
    - scaleX/Y != 1인 경우: bbox 중심(po_x, po_y) 기준 스케일 적용
      canvas_x = po_x + scaleX * (cmd_x - po_x)
    - scale = 1인 경우: canvas_x = cmd_x (그대로 사용)
    """
    result = []
    for obj in json_data.get("objects", []):
        path    = obj.get("path", [])
        left    = obj.get("left") or 0.0
        top     = obj.get("top")  or 0.0
        scale_x = obj.get("scaleX") or 1.0
        scale_y = obj.get("scaleY") or 1.0
        origin_x = obj.get("originX", "left")
        origin_y = obj.get("originY", "left")
        width   = obj.get("width",  0)
        height  = obj.get("height", 0)

        # bbox 중심 = path 좌표계 기준점
        po_x = left if origin_x == "center" else left + width / 2
        po_y = top  if origin_y == "center" else top  + height / 2

        points = []
        for cmd in path:
            if not cmd or cmd[0] not in ("M", "L") or len(cmd) < 3:
                continue
            cx = po_x + scale_x * (cmd[1] - po_x)
            cy = po_y + scale_y * (cmd[2] - po_y)
            points.append([float(np.clip(cx / canvas_w, 0, 1)),
                           float(np.clip(cy / canvas_h, 0, 1))])

        if len(points) > 1 and points[0] == points[-1]:
            points = points[:-1]

        if len(points) >= 3:
            result.append(points)
    return result

# ---------------------------------------------------------------------------
# ROI edit mode
# ---------------------------------------------------------------------------
if st.session_state.edit_mode:
    frozen = st.session_state.frozen_frame
    if frozen is not None:
        h, w = frozen.shape[:2]
        canvas_h = int(CANVAS_W * h / w)
        resized = cv2.resize(frozen, (CANVAS_W, canvas_h))
        pil_img = Image.fromarray(cv2.cvtColor(resized, cv2.COLOR_BGR2RGB))

        from streamlit_drawable_canvas import st_canvas

        with main_col:
            st.caption(
                "폴리곤 모드: 클릭으로 꼭짓점 추가 → 더블클릭으로 닫기  "
                "| 여러 폴리곤을 한번에 그린 뒤 아래 폼을 채우고 저장하세요"
            )
            canvas_result = st_canvas(
                fill_color="rgba(0, 255, 0, 0.1)",
                stroke_width=2,
                stroke_color="#00ff00",
                background_image=pil_img,
                drawing_mode="polygon",
                key=f"roi_canvas_{st.session_state.canvas_key}",
                height=canvas_h,
                width=CANVAS_W,
            )

            # 폴리곤이 하나 이상 그려진 경우 인라인 폼 표시
            all_pts = []
            if canvas_result and canvas_result.json_data:
                # ── 임시 디버그 패널 ──────────────────────────────
                with st.expander("🔍 [DEBUG] Canvas JSON (좌표 확인용 — 확인 후 삭제 예정)", expanded=False):
                    st.json(canvas_result.json_data)
                    st.caption(f"canvas_w={CANVAS_W}, canvas_h={canvas_h}")
                # ────────────────────────────────────────────────
                all_pts = extract_all_polygon_points(
                    canvas_result.json_data, CANVAS_W, canvas_h
                )

            if all_pts:
                st.divider()
                st.caption(f"**{len(all_pts)}개 폴리곤** — 각 ROI 정보를 입력하세요")
                ck = st.session_state.canvas_key
                roi_inputs = {}  # {i: (name, text, audio, zone_type)} 저장용
                for i in range(len(all_pts)):
                    c1, c2, c3, c4, c5 = st.columns([2, 2, 2, 2, 1])
                    zone_type_val = c1.selectbox(
                        "구역 유형", ["trigger", "exclude"],
                        format_func=lambda v: "🔔 트리거" if v == "trigger" else "🚫 제외",
                        key=f"roi_zone_type_{ck}_{i}",
                    )
                    name_val  = c2.text_input(
                        "이름", placeholder="예: 횡단보도",
                        key=f"roi_name_{ck}_{i}",
                    )
                    text_val  = c3.text_input(
                        "안내 텍스트" + ("" if zone_type_val == "trigger" else " (선택)"),
                        placeholder="예: 횡단보도 앞입니다",
                        key=f"roi_text_{ck}_{i}",
                    )
                    # 오디오 경로는 버퍼 키로 관리 (위젯 key와 분리해 프로그래매틱 수정 허용)
                    audio_buf_key = f"roi_audio_buf_{ck}_{i}"
                    audio_val = c4.text_input(
                        "MP3 경로 (선택)",
                        value=st.session_state.get(audio_buf_key, ""),
                        placeholder="audio/crosswalk.mp3",
                    )
                    st.session_state[audio_buf_key] = audio_val  # 타이핑 반영
                    c5.write("")  # label 높이 맞춤
                    if c5.button("📂", key=f"roi_browse_{ck}_{i}", help="파일 탐색기로 MP3 선택"):
                        selected = browse_audio_file()
                        if selected:
                            st.session_state[audio_buf_key] = selected
                            st.rerun()
                    roi_inputs[i] = (name_val, text_val, audio_val, zone_type_val)

                if st.button("✅ ROI 저장", type="primary", use_container_width=True):
                    saved, errors = 0, []
                    for i, pts in enumerate(all_pts):
                        name, text, audio, zone_type = roi_inputs[i]
                        name  = name.strip()
                        text  = text.strip()
                        audio = st.session_state.get(f"roi_audio_buf_{ck}_{i}", audio).strip()
                        if not name or (zone_type == "trigger" and not text):
                            errors.append(f"폴리곤 {i + 1}: 이름과 안내 텍스트를 입력하세요.")
                            continue
                        priority = len(st.session_state.roi_manager.rois) + 1
                        st.session_state.roi_manager.add_roi(
                            name, pts, priority, text, audio_file=audio, zone_type=zone_type
                        )
                        saved += 1
                    if saved:
                        save_rois()
                        st.session_state.canvas_key += 1  # 캔버스 리셋
                        st.success(f"{saved}개 ROI가 저장되었습니다.")
                        st.rerun()
                    for e in errors:
                        st.error(e)
    else:
        with main_col:
            st.info("편집 모드: ▶ 시작을 눌러 영상을 시작한 뒤 편집 모드를 켜세요.")

    render_status([], set(), time.time())
    render_traffic_stats()
    render_log()

# ---------------------------------------------------------------------------
# Running mode: fragment-based video loop (sidebar stays stable, no flicker)
# ---------------------------------------------------------------------------
@st.fragment(run_every=0.05)
def _video_loop():
    if not st.session_state.running:
        return

    cap = st.session_state.cap
    if cap is None or not cap.isOpened():
        st.session_state.running = False
        st.rerun()
        return

    ret, frame = cap.read()
    if not ret:
        if source_type == "영상 파일":
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, frame = cap.read()
        if not ret:
            st.session_state.running = False
            st.rerun()
            return

    frame = apply_rotation(frame, rotation)
    st.session_state.frozen_frame = frame.copy()
    h, w = frame.shape[:2]
    now = time.time()

    detector: Detector = st.session_state.detector
    if detector is None:
        st.session_state.detector = Detector(model_path)
        detector = st.session_state.detector

    detections = detector.detect(frame, conf_threshold)
    # 지형지물 오탐지 방지용 제외구역 — 트래킹 이전에 raw detection 단계에서 걸러낸다.
    detections = filter_excluded(detections, st.session_state.roi_manager, frame)
    tracks = st.session_state.tracker.update(detections)
    # 배경의 케이블/문틀 경계선 같은 고정 오탐지 대상은 지팡이와 달리 절대 움직이지
    # 않는다 — static_frames가 임계값을 넘은 트랙은 ROI 트리거 대상에서 제외한다.
    cane_tracks = [
        t for t in tracks
        if t["class"] == CANE_CLASS_ID and t.get("static_frames", 0) < STATIC_CANE_SUPPRESS_FRAMES
    ]
    person_tracks = [t for t in tracks if t["class"] == PERSON_CLASS_ID]

    cane_person_map = associate(tracks)
    st.session_state.foot_counter.update(person_tracks, cane_person_map, now)

    if now - st.session_state.traffic_stats_read_at >= STATS_POLL_INTERVAL:
        st.session_state.traffic_stats = read_daily_totals(FOOT_TRAFFIC_DB_PATH)
        st.session_state.traffic_stats_read_at = now

    active_roi_names: set[str] = set()
    for trk in cane_tracks:
        x1, y1, x2, y2 = trk["bbox"]
        # 바운딩 박스 하단 10% 구간으로 ROI 교차 판정
        strip_h = (y2 - y1) * 0.10
        roi = st.session_state.roi_manager.check_region(
            x1 / w, (y2 - strip_h) / h,
            x2 / w,  y2 / h,
        )
        if roi:
            active_roi_names.add(roi.name)
            if disp.on_detected(roi.name, now):
                st.session_state.event_log.insert(0, {
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "roi": roi.name,
                    "text": roi.announcement_text,
                })
                st.session_state.last_announcement = roi.announcement_text
                st.session_state.last_announcement_time = now
                st.toast(f"🔊 {roi.announcement_text}")
                if roi.audio_file:
                    st.session_state.audio_player.play(roi.audio_file)

    for roi in st.session_state.roi_manager.rois:
        if roi.name not in active_roi_names:
            disp.on_not_detected(roi.name)

    frame = draw_overlays(frame, tracks, cane_person_map, st.session_state.roi_manager.rois, disp, now)
    frame_ph.image(frame, channels="BGR", width="stretch")
    render_status(tracks, active_roi_names, now)
    render_traffic_stats()
    render_log()


if st.session_state.running:
    _video_loop()

# ---------------------------------------------------------------------------
# Stopped / idle mode
# ---------------------------------------------------------------------------
else:
    frozen = st.session_state.frozen_frame
    if frozen is not None:
        display = draw_overlays(
            frozen.copy(),
            [],
            {},
            st.session_state.roi_manager.rois,
            st.session_state.dispatcher,
            time.time(),
        )
        frame_ph.image(display, channels="BGR", width="stretch")
    else:
        with main_col:
            st.info("▶ 시작 버튼을 눌러 영상을 시작하세요.")

    render_status([], set(), time.time())
    render_traffic_stats()
    render_log()
