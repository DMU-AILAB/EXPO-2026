export const OVERLAY_WIDTH = 960;
export const OVERLAY_HEIGHT = 540;

function toPointsAttr(points) {
  return points.map(([x, y]) => `${x},${y}`).join(' ');
}

function polygonCenter(points) {
  const cx = points.reduce((sum, [x]) => sum + x, 0) / points.length;
  const cy = points.reduce((sum, [, y]) => sum + y, 0) / points.length;
  return [cx, cy];
}

export default function RoiOverlay({ roiList, selectedId, onSelectRoi, children }) {
  return (
    <svg
      viewBox={`0 0 ${OVERLAY_WIDTH} ${OVERLAY_HEIGHT}`}
      style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}
    >
      {roiList.map((roi) => {
        const isSelected = roi.id === selectedId;
        const [cx, cy] = polygonCenter(roi.points);
        return (
          <g
            key={roi.id}
            onClick={() => onSelectRoi?.(roi.id)}
            style={{ cursor: onSelectRoi ? 'pointer' : 'default' }}
          >
            <polygon
              points={toPointsAttr(roi.points)}
              fill={roi.active ? 'var(--accent-soft)' : 'rgba(107,111,122,0.10)'}
              stroke={roi.active ? 'var(--accent)' : 'var(--ink-soft)'}
              strokeWidth={isSelected ? 3 : 1.5}
            />
            <text
              x={cx}
              y={cy}
              textAnchor="middle"
              fontSize="14"
              fontWeight="600"
              fill={roi.active ? 'var(--accent)' : 'var(--ink-soft)'}
              style={{ paintOrder: 'stroke', stroke: '#fff', strokeWidth: 3 }}
            >
              {roi.name}
            </text>
          </g>
        );
      })}
      {children}
    </svg>
  );
}
