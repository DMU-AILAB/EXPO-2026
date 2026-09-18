import { useAppState } from '../state/AppState';

export default function RecordButton() {
  const { recording, elapsedLabel, startRecording, stopRecording } = useAppState();

  return (
    <button
      onClick={recording ? stopRecording : startRecording}
      style={{
        position: 'absolute',
        top: 14,
        left: 14,
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        border: 'none',
        borderRadius: 999,
        padding: '8px 14px',
        fontSize: 13,
        fontWeight: 700,
        color: recording ? '#fff' : '#1b1e27',
        background: recording ? 'var(--danger)' : 'rgba(255,255,255,0.9)',
        zIndex: 2,
      }}
    >
      <span
        style={{
          width: 9,
          height: 9,
          borderRadius: '50%',
          background: recording ? '#fff' : 'var(--ink-soft)',
          display: 'inline-block',
          animation: recording ? 'record-blink 1s infinite' : 'none',
        }}
      />
      {recording ? `■ 녹화 중지 ${elapsedLabel}` : '● 녹화 시작'}
    </button>
  );
}
