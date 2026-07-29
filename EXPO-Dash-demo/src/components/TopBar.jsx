import { useAppState } from '../state/AppState';

const TABS = [
  { key: 'monitoring', label: '모니터링' },
  { key: 'roi', label: 'ROI 수정' },
  { key: 'stats', label: '통계' },
  { key: 'recordings', label: '녹화' },
];

export default function TopBar() {
  const { activeTab, setActiveTab } = useAppState();

  return (
    <header
      style={{
        height: 'var(--shell-height)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0 var(--content-pad-x)',
        borderBottom: '1px solid var(--line)',
        background: 'var(--surface)',
        flexShrink: 0,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span
          style={{
            width: 10,
            height: 10,
            borderRadius: '50%',
            background: 'var(--accent)',
            display: 'inline-block',
          }}
        />
        <span style={{ fontWeight: 700, fontSize: 15 }}>VisionGuide</span>
      </div>

      <nav style={{ display: 'flex', gap: 28 }}>
        {TABS.map((tab) => {
          const isActive = tab.key === activeTab;
          return (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              style={{
                background: 'none',
                border: 'none',
                padding: '4px 2px',
                fontSize: 14,
                fontWeight: isActive ? 700 : 500,
                color: isActive ? 'var(--ink)' : 'var(--ink-soft)',
                borderBottom: isActive ? '2px solid var(--accent)' : '2px solid transparent',
              }}
            >
              {tab.label}
            </button>
          );
        })}
      </nav>
    </header>
  );
}
