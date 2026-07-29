import { useEffect, useState } from 'react';
import { useAppState } from '../state/AppState';
import RoiOverlay from '../components/RoiOverlay';
import RecordButton from '../components/RecordButton';
import StatusBadge from '../components/StatusBadge';
import StatTile from '../components/StatTile';
import { statsByPeriod } from '../mock/mockData';

const BOX_COLOR = {
  white_cane: '#e2483d',
  person: '#2f6fed',
};

const BOX_LABEL = {
  white_cane: 'white_cane',
  person: 'person',
};

// 좌표는 RoiOverlay의 960x540 기준 좌표계와 동일하게 맞춘다.
const DETECTION_FRAMES = [
  [{ id: 'b1', cls: 'person', conf: 92, x: 90, y: 280, w: 70, h: 150 }],
  [{ id: 'b2', cls: 'white_cane', conf: 88, x: 460, y: 150, w: 55, h: 140 }],
  [
    { id: 'b3', cls: 'person', conf: 95, x: 505, y: 160, w: 65, h: 130 },
    { id: 'b4', cls: 'white_cane', conf: 90, x: 100, y: 270, w: 50, h: 150 },
  ],
  [],
];

const EVENT_POOL = [
  { className: 'white_cane', roi: 'ROI-2 · 3번 출구' },
  { className: 'person', roi: 'ROI-1 · 계단 앞' },
  { className: 'white_cane', roi: 'ROI-1 · 계단 앞' },
  { className: 'person', roi: 'ROI-2 · 3번 출구' },
  { className: 'person', roi: 'ROI-3 · 화장실 앞' },
];

function nowLabel() {
  return new Date().toTimeString().slice(0, 8);
}

export default function MonitoringPage() {
  const { roiList, eventLog, pushEvent } = useAppState();
  const [clock, setClock] = useState(nowLabel());
  const [frameIndex, setFrameIndex] = useState(0);
  const [totalTraffic, setTotalTraffic] = useState(statsByPeriod.today.totalTraffic);
  const [caneUsers, setCaneUsers] = useState(statsByPeriod.today.caneUsers);
  const [voicePlaying, setVoicePlaying] = useState(false);

  useEffect(() => {
    const id = setInterval(() => setClock(nowLabel()), 1000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    const id = setInterval(() => {
      setFrameIndex((i) => (i + 1) % DETECTION_FRAMES.length);
    }, 2200);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    let poolIndex = 0;
    const id = setInterval(() => {
      const next = EVENT_POOL[poolIndex % EVENT_POOL.length];
      poolIndex += 1;
      pushEvent({ time: nowLabel(), className: next.className, roi: next.roi });
      setTotalTraffic((n) => n + 1);
      if (next.className === 'white_cane') {
        setCaneUsers((n) => n + 1);
      }
    }, 5000);
    return () => clearInterval(id);
  }, [pushEvent]);

  const handleVoiceTest = () => {
    if (voicePlaying) return;
    setVoicePlaying(true);
    setTimeout(() => setVoicePlaying(false), 2000);
  };

  const boxes = DETECTION_FRAMES[frameIndex];

  return (
    <div className="page">
      <div style={{ display: 'grid', gridTemplateColumns: '68% 32%', gap: 16, alignItems: 'start' }}>
        <div
          style={{
            position: 'relative',
            width: '100%',
            aspectRatio: '16 / 9',
            background: '#000',
            borderRadius: 'var(--radius-md)',
            overflow: 'hidden',
          }}
        >
          <RecordButton />
          <RoiOverlay roiList={roiList}>
            {boxes.map((box) => (
              <g key={box.id}>
                <rect
                  x={box.x}
                  y={box.y}
                  width={box.w}
                  height={box.h}
                  fill="none"
                  stroke={BOX_COLOR[box.cls]}
                  strokeWidth={2.5}
                />
                <text
                  x={box.x}
                  y={box.y - 6}
                  fontSize="13"
                  fontWeight="700"
                  fill={BOX_COLOR[box.cls]}
                >
                  {BOX_LABEL[box.cls]} {box.conf}%
                </text>
              </g>
            ))}
          </RoiOverlay>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <StatTile label="카메라 상태">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <StatusBadge tone="good">정상</StatusBadge>
              <span style={{ fontSize: 13, color: 'var(--ink-soft)' }}>{clock}</span>
            </div>
          </StatTile>

          <StatTile label="오늘 총 유동인구" value={`${totalTraffic} 명`} />
          <StatTile label="지팡이 사용자 감지" value={`${caneUsers} 명`} />

          <div className="stat-tile" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div className="stat-tile__label">안내 음성 테스트</div>
            <button
              className="btn btn--primary"
              onClick={handleVoiceTest}
              disabled={voicePlaying}
              style={{ opacity: voicePlaying ? 0.7 : 1 }}
            >
              {voicePlaying ? '재생 중…' : '재생 버튼'}
            </button>
          </div>
        </div>
      </div>

      <div className="sum-card" style={{ marginTop: 16, padding: '16px 20px' }}>
        <div style={{ fontWeight: 700, marginBottom: 10 }}>최근 감지 이벤트</div>
        <div style={{ height: 232, overflowY: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ textAlign: 'left', color: 'var(--ink-soft)' }}>
                <th style={{ padding: '6px 8px', fontWeight: 600, width: 100 }}>시각</th>
                <th style={{ padding: '6px 8px', fontWeight: 600, width: 140 }}>클래스</th>
                <th style={{ padding: '6px 8px', fontWeight: 600 }}>구역</th>
              </tr>
            </thead>
            <tbody>
              {eventLog.map((ev, i) => (
                <tr key={`${ev.time}-${i}`} style={{ borderTop: '1px solid var(--line)' }}>
                  <td style={{ padding: '6px 8px' }}>{ev.time}</td>
                  <td style={{ padding: '6px 8px', color: BOX_COLOR[ev.className] || 'var(--ink)' }}>
                    {ev.className}
                  </td>
                  <td style={{ padding: '6px 8px' }}>{ev.roi}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
