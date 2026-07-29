export default function StatTile({ label, value, valueColor, children }) {
  return (
    <div className="stat-tile">
      <div className="stat-tile__label">{label}</div>
      {value !== undefined && (
        <div className="stat-tile__value" style={valueColor ? { color: valueColor } : undefined}>
          {value}
        </div>
      )}
      {children}
    </div>
  );
}
