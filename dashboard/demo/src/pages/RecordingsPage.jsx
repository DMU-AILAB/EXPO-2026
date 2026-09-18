import { useEffect, useRef, useState } from 'react';
import { useAppState } from '../state/AppState';

function formatDuration(sec) {
  const m = Math.floor(sec / 60)
    .toString()
    .padStart(2, '0');
  const s = Math.floor(sec % 60)
    .toString()
    .padStart(2, '0');
  return `${m}:${s}`;
}

export default function RecordingsPage() {
  const { clips, setActiveTab } = useAppState();

  const dateOptions = [...new Set(clips.map((c) => c.date))].sort((a, b) => (a < b ? 1 : -1));
  const [selectedDate, setSelectedDate] = useState(dateOptions[0]);
  const [selectedClipId, setSelectedClipId] = useState(null);
  const [playing, setPlaying] = useState(false);
  const [currentSec, setCurrentSec] = useState(0);
  const [downloadMsg, setDownloadMsg] = useState(false);
  const progressTrackRef = useRef(null);

  const effectiveDate = dateOptions.includes(selectedDate) ? selectedDate : dateOptions[0];
  const filteredClips = clips.filter((c) => c.date === effectiveDate);
  const selectedClip = filteredClips.find((c) => c.id === selectedClipId) ?? filteredClips[0] ?? null;

  useEffect(() => {
    setCurrentSec(0);
    setPlaying(false);
  }, [selectedClip?.id]);

  useEffect(() => {
    if (!playing || !selectedClip) return undefined;
    const id = setInterval(() => {
      setCurrentSec((sec) => {
        if (sec + 1 >= selectedClip.durationSec) {
          setPlaying(false);
          return selectedClip.durationSec;
        }
        return sec + 1;
      });
    }, 1000);
    return () => clearInterval(id);
  }, [playing, selectedClip]);

  function handleSeek(e) {
    if (!selectedClip || !progressTrackRef.current) return;
    const rect = progressTrackRef.current.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    setCurrentSec(Math.round(ratio * selectedClip.durationSec));
  }

  function handleDownload() {
    setDownloadMsg(true);
    setTimeout(() => setDownloadMsg(false), 2500);
  }

  if (clips.length === 0) {
    return (
      <div className="page">
        <div
          className="sum-card"
          style={{
            padding: 48,
            textAlign: 'center',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: 14,
          }}
        >
          <div style={{ color: 'var(--ink-soft)' }}>
            아직 녹화된 영상이 없습니다 — 모니터링 화면에서 녹화를 시작하세요
          </div>
          <button className="btn btn--primary" onClick={() => setActiveTab('monitoring')}>
            모니터링으로 이동
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="page">
      <div style={{ display: 'grid', gridTemplateColumns: '32% 68%', gap: 16, alignItems: 'start' }}>
        <div>
          <div className="sum-card" style={{ padding: 12, marginBottom: 12 }}>
            <select
              value={effectiveDate}
              onChange={(e) => setSelectedDate(e.target.value)}
              style={{
                width: '100%',
                padding: '8px 10px',
                border: '1px solid var(--line)',
                borderRadius: 6,
                fontFamily: 'inherit',
                fontSize: 14,
              }}
            >
              {dateOptions.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {filteredClips.map((clip) => {
              const isSelected = selectedClip?.id === clip.id;
              return (
                <button
                  key={clip.id}
                  onClick={() => setSelectedClipId(clip.id)}
                  className="sum-card"
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 12,
                    padding: 10,
                    textAlign: 'left',
                    border: isSelected ? '2px solid var(--accent)' : '1px solid var(--line)',
                  }}
                >
                  <div
                    style={{
                      width: 64,
                      height: 40,
                      flexShrink: 0,
                      borderRadius: 6,
                      background: '#000',
                    }}
                  />
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 700 }}>
                      {clip.startedAt} · {clip.durationSec}s
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--ink-soft)' }}>{clip.label}</div>
                  </div>
                </button>
              );
            })}
            {filteredClips.length === 0 && (
              <div style={{ fontSize: 13, color: 'var(--ink-soft)' }}>
                선택한 날짜에 녹화된 클립이 없습니다.
              </div>
            )}
          </div>
        </div>

        <div className="sum-card" style={{ padding: 0, overflow: 'hidden' }}>
          <div
            style={{
              position: 'relative',
              width: '100%',
              aspectRatio: '16 / 9',
              background: '#000',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#8b8f99',
              fontSize: 13,
            }}
          >
            {selectedClip ? `클립 재생 화면 · ${selectedClip.startedAt}` : '선택된 클립 없음'}
          </div>

          {selectedClip && (
            <div style={{ padding: '12px 16px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <button
                  className="btn"
                  onClick={() => setPlaying((p) => !p)}
                  style={{ width: 36, flexShrink: 0 }}
                >
                  {playing ? '❚❚' : '▶'}
                </button>

                <div
                  ref={progressTrackRef}
                  onClick={handleSeek}
                  style={{
                    flex: 1,
                    height: 6,
                    background: 'var(--line)',
                    borderRadius: 3,
                    cursor: 'pointer',
                    position: 'relative',
                  }}
                >
                  <div
                    style={{
                      position: 'absolute',
                      inset: 0,
                      width: `${(currentSec / selectedClip.durationSec) * 100}%`,
                      background: 'var(--accent)',
                      borderRadius: 3,
                    }}
                  />
                </div>

                <div style={{ fontSize: 12, color: 'var(--ink-soft)', width: 96, textAlign: 'right' }}>
                  {formatDuration(currentSec)} / {formatDuration(selectedClip.durationSec)}
                </div>

                <button className="btn" onClick={handleDownload}>
                  다운로드
                </button>
              </div>
              {downloadMsg && (
                <div style={{ marginTop: 8, fontSize: 12, color: 'var(--amber)', textAlign: 'right' }}>
                  다운로드는 데모에서 지원되지 않습니다
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
