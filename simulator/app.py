import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
import cv2
import numpy as np
import time
import tempfile
from datetime import datetime
from PIL import Image

from simulator.detector import Detector, DEFAULT_MODEL_PATH
from simulator.roi_manager import ROIManager
from simulator.trigger_dispatcher import TriggerDispatcher
from audio_trigger import AudioPlayer

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="VisionGuide Simulator",
    page_icon="🦯",
    layout="wide",
)

CANVAS_W = 640  # fixed canvas width for ROI editing

# ---------------------------------------------------------------------------
# Session state initialization
# ---------------------------------------------------------------------------
def _init():
    defaults = {
        "running": False,
        "edit_mode": False,
        "roi_manager": ROIManager(),
        "dispatcher": TriggerDispatcher(),
        "audio_player": AudioPlayer(),
        "event_log": [],
        "cap": None,
        "frozen_frame": None,       # last captured frame (used in edit mode)
        "temp_video_path": None,
        "last_announcement": "",
        "last_announcement_time": 0.0,
        "detector": None,
        "current_model_path": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init()

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

    # --- Model ---
    st.subheader("모델")
    model_path = st.text_input("가중치 경로", value=DEFAULT_MODEL_PATH)
    conf_threshold = st.slider("신뢰도 임계값", 0.1, 1.0, 0.5, 0.05)

    # Reload detector if model path changed
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

    roi_name_input  = st.text_input("ROI 이름", placeholder="예: 횡단보도")
    roi_text_input  = st.text_input("안내 텍스트", placeholder="예: 횡단보도 앞입니다")
    roi_audio_input = st.text_input("MP3 경로 (선택)", placeholder="예: audio/crosswalk.mp3")

    btn_col1, btn_col2 = st.columns(2)
    add_roi_btn = btn_col1.button("추가", use_container_width=True)
    clear_roi_btn = btn_col2.button("전체 초기화", use_container_width=True)

    if clear_roi_btn:
        st.session_state.roi_manager.clear()
        st.session_state.dispatcher.clear()

    rois = st.session_state.roi_manager.rois
    if rois:
        st.caption(f"등록된 ROI ({len(rois)}개)")
        for roi in rois:
            r_col1, r_col2 = st.columns([4, 1])
            r_col1.caption(f"**{roi.name}**: {roi.announcement_text}")
            if r_col2.button("삭제", key=f"del_{roi.name}"):
                st.session_state.roi_manager.remove(roi.name)
                st.session_state.dispatcher.on_not_detected(roi.name)
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
                    st.rerun()
                else:
                    st.error("카메라/파일을 열 수 없습니다.")
    else:
        if st.button("⏹ 정지", type="secondary", use_container_width=True):
            st.session_state.running = False
            if st.session_state.cap:
                st.session_state.cap.release()
                st.session_state.cap = None
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
    st.subheader("이벤트 로그")
    log_ph = st.empty()

# ---------------------------------------------------------------------------
# Helper: draw overlays on frame
# ---------------------------------------------------------------------------
def draw_overlays(frame, detections, rois, dispatcher, now):
    h, w = frame.shape[:2]
    for roi in rois:
        pts = np.array(
            [[int(x * w), int(y * h)] for x, y in roi.points],
            dtype=np.int32,
        )
        if len(pts) < 3:
            continue
        remaining = dispatcher.cooldown_remaining(roi.name, now)
        color = (0, 0, 200) if remaining > 0 else roi.color

        overlay = frame.copy()
        cv2.fillPoly(overlay, [pts], color)
        cv2.addWeighted(overlay, 0.2, frame, 0.8, 0, frame)
        cv2.polylines(frame, [pts], isClosed=True, color=color, thickness=2)

        cx = int(np.mean(pts[:, 0]))
        cy = int(np.mean(pts[:, 1]))
        label = roi.name
        if remaining > 0:
            label += f" ({remaining:.1f}s)"
        cv2.putText(frame, label, (cx - 40, cy),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)

    for det in detections:
        x1, y1, x2, y2 = det["bbox"]
        conf = det["conf"]
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 165, 255), 2)
        cv2.putText(frame, f"white_cane {conf:.2f}",
                    (x1, max(y1 - 8, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 165, 255), 2)
    return frame

# ---------------------------------------------------------------------------
# Helper: render status panel
# ---------------------------------------------------------------------------
def render_status(detections, active_roi_names, now):
    with status_ph.container():
        if st.session_state.running:
            if detections:
                st.success(f"탐지 중 — {len(detections)}개 객체")
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

        # Announcement banner (3-second display)
        if time.time() - st.session_state.last_announcement_time < 3.0:
            st.divider()
            st.info(f"🔊 {st.session_state.last_announcement}")

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
# Helper: extract polygon points from canvas JSON
# ---------------------------------------------------------------------------
def extract_polygon_points(json_data: dict, canvas_w: int, canvas_h: int):
    objects = json_data.get("objects", [])
    if not objects:
        return None

    obj = objects[-1]
    path = obj.get("path", [])
    left = obj.get("left") or 0
    top = obj.get("top") or 0
    scale_x = obj.get("scaleX") or 1.0
    scale_y = obj.get("scaleY") or 1.0

    points = []
    for cmd in path:
        if not cmd or cmd[0] not in ("M", "L") or len(cmd) < 3:
            continue
        x = (left + cmd[1] * scale_x) / canvas_w
        y = (top + cmd[2] * scale_y) / canvas_h
        points.append([float(np.clip(x, 0, 1)), float(np.clip(y, 0, 1))])

    # Remove duplicate closing point
    if len(points) > 1 and points[0] == points[-1]:
        points = points[:-1]

    return points if len(points) >= 3 else None

# ---------------------------------------------------------------------------
# ROI edit mode: show drawable canvas
# ---------------------------------------------------------------------------
if st.session_state.edit_mode:
    frozen = st.session_state.frozen_frame
    if frozen is not None:
        h, w = frozen.shape[:2]
        canvas_h = int(CANVAS_W * h / w)
        resized = cv2.resize(frozen, (CANVAS_W, canvas_h))
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb)

        from streamlit_drawable_canvas import st_canvas

        with main_col:
            st.caption("폴리곤 모드: 클릭으로 꼭짓점 추가 → 더블클릭으로 닫기")
            canvas_result = st_canvas(
                fill_color="rgba(0, 255, 0, 0.1)",
                stroke_width=2,
                stroke_color="#00ff00",
                background_image=pil_img,
                drawing_mode="polygon",
                key="roi_canvas",
                height=canvas_h,
                width=CANVAS_W,
            )

        if add_roi_btn:
            if not roi_name_input or not roi_text_input:
                st.sidebar.error("ROI 이름과 안내 텍스트를 입력하세요.")
            elif canvas_result.json_data is None:
                st.sidebar.error("폴리곤을 그려주세요.")
            else:
                pts = extract_polygon_points(canvas_result.json_data, CANVAS_W, canvas_h)
                if pts is None:
                    st.sidebar.error("최소 3개 꼭짓점이 필요합니다.")
                else:
                    priority = len(st.session_state.roi_manager.rois) + 1
                    st.session_state.roi_manager.add_roi(
                        roi_name_input, pts, priority, roi_text_input,
                        audio_file=roi_audio_input,
                    )
                    st.sidebar.success(f"ROI '{roi_name_input}' 추가됨")
    else:
        with main_col:
            st.info("편집 모드: ▶ 시작을 눌러 영상을 시작한 뒤 편집 모드를 켜세요.")

    render_status([], set(), time.time())
    render_log()

# ---------------------------------------------------------------------------
# Running mode: live video loop (one frame per Streamlit rerun)
# ---------------------------------------------------------------------------
elif st.session_state.running:
    cap = st.session_state.cap
    if cap is None or not cap.isOpened():
        st.session_state.running = False
        st.error("카메라 연결이 끊겼습니다.")
        st.stop()

    ret, frame = cap.read()
    if not ret:
        # Loop video file; stop if webcam ends
        if source_type == "영상 파일":
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, frame = cap.read()
        if not ret:
            st.session_state.running = False
            st.rerun()

    if ret:
        st.session_state.frozen_frame = frame.copy()
        h, w = frame.shape[:2]
        now = time.time()

        detector: Detector = st.session_state.detector
        if detector is None:
            detector = Detector(model_path)
            st.session_state.detector = detector

        detections = detector.detect(frame, conf_threshold)

        # ROI check + trigger
        active_roi_names: set[str] = set()
        for det in detections:
            x1, y1, x2, y2 = det["bbox"]
            cx = (x1 + x2) / 2 / w
            cy = (y1 + y2) / 2 / h
            roi = st.session_state.roi_manager.check(cx, cy)
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

        # Reset debounce for ROIs with no detection this frame
        for roi in st.session_state.roi_manager.rois:
            if roi.name not in active_roi_names:
                disp.on_not_detected(roi.name)

        # Draw and display
        frame = draw_overlays(frame, detections, st.session_state.roi_manager.rois, disp, now)
        frame_ph.image(frame, channels="BGR", use_container_width=True)

        render_status(detections, active_roi_names, now)
        render_log()

    time.sleep(0.05)   # ~20 fps
    st.rerun()

# ---------------------------------------------------------------------------
# Stopped / idle mode
# ---------------------------------------------------------------------------
else:
    frozen = st.session_state.frozen_frame
    if frozen is not None:
        display = draw_overlays(
            frozen.copy(),
            [],
            st.session_state.roi_manager.rois,
            st.session_state.dispatcher,
            time.time(),
        )
        frame_ph.image(display, channels="BGR", use_container_width=True)
    else:
        with main_col:
            st.info("▶ 시작 버튼을 눌러 영상을 시작하세요.")

    render_status([], set(), time.time())
    render_log()
