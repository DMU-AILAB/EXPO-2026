const WIDTH = 720;
const HEIGHT = 220;
const PADDING = { top: 12, right: 16, bottom: 26, left: 36 };
const TICK_HOURS = [0, 3, 6, 9, 12, 15, 18, 21, 24];

export default function LineChart({ hourly }) {
  const innerW = WIDTH - PADDING.left - PADDING.right;
  const innerH = HEIGHT - PADDING.top - PADDING.bottom;
  const maxVal = Math.max(...hourly, 1) * 1.15;

  const points = hourly.map((v, i) => {
    const x = PADDING.left + (i / (hourly.length - 1)) * innerW;
    const y = PADDING.top + innerH - (v / maxVal) * innerH;
    return [x, y];
  });

  const linePath = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p[0]},${p[1]}`).join(' ');
  const baseline = PADDING.top + innerH;
  const areaPath = `${linePath} L ${points[points.length - 1][0]},${baseline} L ${points[0][0]},${baseline} Z`;

  return (
    <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} style={{ width: '100%', height: 'auto', display: 'block' }}>
      <defs>
        <linearGradient id="lineChartFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" style={{ stopColor: 'var(--accent)', stopOpacity: 0.22 }} />
          <stop offset="100%" style={{ stopColor: 'var(--accent)', stopOpacity: 0 }} />
        </linearGradient>
      </defs>

      <line
        x1={PADDING.left}
        y1={baseline}
        x2={WIDTH - PADDING.right}
        y2={baseline}
        stroke="var(--line)"
        strokeWidth={1}
      />

      <path d={areaPath} fill="url(#lineChartFill)" stroke="none" />
      <path d={linePath} fill="none" stroke="var(--accent)" strokeWidth={2} />

      {TICK_HOURS.map((h) => (
        <text
          key={h}
          x={points[h][0]}
          y={HEIGHT - 6}
          fontSize="11"
          fill="var(--ink-soft)"
          textAnchor="middle"
        >
          {h}
        </text>
      ))}
    </svg>
  );
}
