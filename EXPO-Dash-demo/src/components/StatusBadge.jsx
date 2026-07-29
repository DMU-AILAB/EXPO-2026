export default function StatusBadge({ tone = 'good', children }) {
  return (
    <span className={`badge badge--${tone}`}>
      <span className="badge__dot" />
      {children}
    </span>
  );
}
