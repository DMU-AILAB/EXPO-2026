import { useEffect, useRef, useState } from 'react';
import { useAppState } from '../state/AppState';
import RoiOverlay, { OVERLAY_WIDTH, OVERLAY_HEIGHT } from '../components/RoiOverlay';
import ConfirmModal from '../components/ConfirmModal';

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

export default function RoiEditPage() {
  const { roiList, setRoiList, upsertRoi, deleteRoi } = useAppState();

  const [selectedId, setSelectedId] = useState(roiList[0]?.id ?? null);
  const [drawing, setDrawing] = useState(false);
  const [dragState, setDragState] = useState(null);
  const [showConfirm, setShowConfirm] = useState(false);

  const [formName, setFormName] = useState('');
  const [formPrompt, setFormPrompt] = useState('');
  const [formActive, setFormActive] = useState(true);

  const containerRef = useRef(null);

  const selectedRoi = roiList.find((r) => r.id === selectedId) ?? null;

  useEffect(() => {
    if (selectedRoi) {
      setFormName(selectedRoi.name);
      setFormPrompt(selectedRoi.voicePrompt);
      setFormActive(selectedRoi.active);
    }
  }, [selectedId]); // eslint-disable-line react-hooks/exhaustive-deps

  function clientToViewBox(clientX, clientY) {
    const rect = containerRef.current.getBoundingClientRect();
    const x = clamp(((clientX - rect.left) / rect.width) * OVERLAY_WIDTH, 0, OVERLAY_WIDTH);
    const y = clamp(((clientY - rect.top) / rect.height) * OVERLAY_HEIGHT, 0, OVERLAY_HEIGHT);
    return [x, y];
  }

  function handleCanvasMouseDown(e) {
    if (!drawing) return;
    const [x, y] = clientToViewBox(e.clientX, e.clientY);
    setDragState({ type: 'draw', start: [x, y], current: [x, y] });
  }

  function handleVertexMouseDown(e, roiId, vertexIndex) {
    e.stopPropagation();
    setSelectedId(roiId);
    setDragState({ type: 'vertex', roiId, vertexIndex });
  }

  useEffect(() => {
    if (!dragState) return undefined;

    function handleMouseMove(e) {
      const [x, y] = clientToViewBox(e.clientX, e.clientY);
      if (dragState.type === 'vertex') {
        setRoiList((prev) =>
          prev.map((roi) => {
            if (roi.id !== dragState.roiId) return roi;
            const points = roi.points.map((pt, i) => (i === dragState.vertexIndex ? [x, y] : pt));
            return { ...roi, points };
          })
        );
      } else if (dragState.type === 'draw') {
        setDragState((prev) => ({ ...prev, current: [x, y] }));
      }
    }

    function handleMouseUp(e) {
      if (dragState.type === 'draw') {
        const [x, y] = clientToViewBox(e.clientX, e.clientY);
        const [x0, y0] = dragState.start;
        const x1 = Math.min(x0, x);
        const y1 = Math.min(y0, y);
        const x2 = Math.max(x0, x);
        const y2 = Math.max(y0, y);
        if (x2 - x1 > 15 && y2 - y1 > 15) {
          const newRoi = {
            id: `roi-${Date.now()}`,
            name: '새 구역',
            voicePrompt: '',
            active: true,
            points: [
              [x1, y1],
              [x2, y1],
              [x2, y2],
              [x1, y2],
            ],
          };
          upsertRoi(newRoi);
          setSelectedId(newRoi.id);
        }
        setDrawing(false);
      }
      setDragState(null);
    }

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [dragState, setRoiList, upsertRoi]);

  function handleDelete() {
    if (!selectedId) return;
    const remaining = roiList.filter((r) => r.id !== selectedId);
    deleteRoi(selectedId);
    setSelectedId(remaining[0]?.id ?? null);
  }

  function handleSaveConfirm() {
    if (selectedRoi) {
      upsertRoi({ ...selectedRoi, name: formName, voicePrompt: formPrompt, active: formActive });
    }
    setShowConfirm(false);
  }

  const drawPreview =
    dragState?.type === 'draw'
      ? (() => {
          const [x0, y0] = dragState.start;
          const [x1, y1] = dragState.current;
          const x = Math.min(x0, x1);
          const y = Math.min(y0, y1);
          const w = Math.abs(x1 - x0);
          const h = Math.abs(y1 - y0);
          return (
            <rect
              x={x}
              y={y}
              width={w}
              height={h}
              fill="var(--accent-soft)"
              stroke="var(--accent)"
              strokeDasharray="6 4"
              strokeWidth={2}
            />
          );
        })()
      : null;

  return (
    <div className="page">
      <div style={{ display: 'grid', gridTemplateColumns: '68% 32%', gap: 16, alignItems: 'start' }}>
        <div>
          <div style={{ marginBottom: 12 }}>
            <button
              className={drawing ? 'btn btn--primary' : 'btn'}
              onClick={() => setDrawing((d) => !d)}
            >
              {drawing ? '그리기 취소' : '+ 새 구역 그리기'}
            </button>
          </div>

          <div
            ref={containerRef}
            onMouseDown={handleCanvasMouseDown}
            style={{
              position: 'relative',
              width: '100%',
              aspectRatio: '16 / 9',
              background: '#000',
              borderRadius: 'var(--radius-md)',
              overflow: 'hidden',
              cursor: drawing ? 'crosshair' : 'default',
            }}
          >
            <RoiOverlay roiList={roiList} selectedId={selectedId} onSelectRoi={setSelectedId}>
              {drawPreview}
              {selectedRoi &&
                selectedRoi.points.map(([x, y], i) => (
                  <circle
                    key={i}
                    cx={x}
                    cy={y}
                    r={7}
                    fill="#fff"
                    stroke="var(--accent)"
                    strokeWidth={2}
                    onMouseDown={(e) => handleVertexMouseDown(e, selectedRoi.id, i)}
                    style={{ cursor: 'grab' }}
                  />
                ))}
            </RoiOverlay>
          </div>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div className="sum-card" style={{ padding: 16 }}>
            <div style={{ fontWeight: 700, marginBottom: 10 }}>ROI 목록</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {roiList.map((roi) => (
                <button
                  key={roi.id}
                  onClick={() => setSelectedId(roi.id)}
                  className="btn"
                  style={{
                    textAlign: 'left',
                    justifyContent: 'flex-start',
                    borderColor: roi.id === selectedId ? 'var(--accent)' : 'var(--line)',
                    color: roi.id === selectedId ? 'var(--accent)' : 'var(--ink)',
                  }}
                >
                  ▸ {roi.name}
                  {roi.id === selectedId ? ' (선택)' : ''}
                </button>
              ))}
              {roiList.length === 0 && (
                <div style={{ fontSize: 13, color: 'var(--ink-soft)' }}>등록된 ROI가 없습니다.</div>
              )}
            </div>
          </div>

          <div className="sum-card" style={{ padding: 16 }}>
            {selectedRoi ? (
              <>
                <div style={{ fontWeight: 700, marginBottom: 12 }}>선택한 구역: {selectedRoi.name}</div>

                <label style={{ display: 'block', fontSize: 13, color: 'var(--ink-soft)', marginBottom: 4 }}>
                  구역명
                </label>
                <input
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                  style={{
                    width: '100%',
                    padding: '8px 10px',
                    border: '1px solid var(--line)',
                    borderRadius: 6,
                    marginBottom: 12,
                    fontFamily: 'inherit',
                    fontSize: 14,
                  }}
                />

                <label style={{ display: 'block', fontSize: 13, color: 'var(--ink-soft)', marginBottom: 4 }}>
                  음성 안내 문구
                </label>
                <input
                  value={formPrompt}
                  onChange={(e) => setFormPrompt(e.target.value)}
                  style={{
                    width: '100%',
                    padding: '8px 10px',
                    border: '1px solid var(--line)',
                    borderRadius: 6,
                    marginBottom: 12,
                    fontFamily: 'inherit',
                    fontSize: 14,
                  }}
                />

                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 24, marginBottom: 10 }}>
                  <span style={{ fontSize: 13, color: 'var(--ink-soft)' }}>활성 여부</span>
                  <button
                    className="btn"
                    onClick={() => setFormActive((a) => !a)}
                    style={{
                      color: formActive ? 'var(--good)' : 'var(--ink-soft)',
                      borderColor: formActive ? 'var(--good)' : 'var(--line)',
                    }}
                  >
                    {formActive ? '● 켜짐' : '○ 꺼짐'}
                  </button>
                </div>

                <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 10 }}>
                  <button className="btn btn--danger" onClick={handleDelete}>
                    삭제
                  </button>
                  <button className="btn btn--primary" onClick={() => setShowConfirm(true)}>
                    저장
                  </button>
                </div>
              </>
            ) : (
              <div style={{ fontSize: 13, color: 'var(--ink-soft)' }}>
                왼쪽 목록에서 ROI를 선택하거나 새 구역을 그려주세요.
              </div>
            )}
          </div>
        </div>
      </div>

      <ConfirmModal
        open={showConfirm}
        message="현재 설정을 덮어씁니다. 저장하시겠습니까?"
        onConfirm={handleSaveConfirm}
        onCancel={() => setShowConfirm(false)}
      />
    </div>
  );
}
