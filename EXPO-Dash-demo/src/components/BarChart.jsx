export default function BarChart({ data }) {
  const sorted = [...data].sort((a, b) => b.value - a.value);
  const maxValue = Math.max(...sorted.map((d) => d.value), 1);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {sorted.map((row) => (
        <div key={row.name} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ width: 130, flexShrink: 0, fontSize: 13 }}>{row.name}</div>
          <div style={{ flex: 1, background: 'var(--good-soft)', borderRadius: 4, overflow: 'hidden' }}>
            <div
              style={{
                width: `${(row.value / maxValue) * 100}%`,
                height: 18,
                background: 'var(--good)',
                borderRadius: 4,
              }}
            />
          </div>
          <div style={{ width: 48, textAlign: 'right', fontSize: 13, fontWeight: 700 }}>{row.value}</div>
        </div>
      ))}
    </div>
  );
}
