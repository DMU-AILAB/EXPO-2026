import { useState } from 'react';
import StatTile from '../components/StatTile';
import LineChart from '../components/LineChart';
import BarChart from '../components/BarChart';
import { statsByPeriod } from '../mock/mockData';

const PERIODS = [
  { key: 'today', label: '오늘' },
  { key: 'week', label: '7일' },
  { key: 'month', label: '30일' },
];

export default function StatsPage() {
  const [period, setPeriod] = useState('today');
  const stats = statsByPeriod[period];

  return (
    <div className="page">
      <div style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
        {PERIODS.map((p) => {
          const active = p.key === period;
          return (
            <button
              key={p.key}
              onClick={() => setPeriod(p.key)}
              className="btn"
              style={{
                background: active ? 'var(--accent)' : 'var(--surface)',
                borderColor: active ? 'var(--accent)' : 'var(--line)',
                color: active ? '#fff' : 'var(--ink)',
              }}
            >
              {p.label}
            </button>
          );
        })}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16, marginBottom: 20 }}>
        <StatTile label="총 유동인구" value={`${stats.totalTraffic} 명`} />
        <StatTile label="지팡이 사용자 수" value={`${stats.caneUsers} 명`} />
        <StatTile label="지팡이 사용자 비율" value={`${stats.caneRatio} %`} />
      </div>

      <div className="sum-card" style={{ padding: 20, marginBottom: 20 }}>
        <div style={{ fontWeight: 700, marginBottom: 12 }}>시간대별 유동인구 (0시~24시)</div>
        <LineChart hourly={stats.hourly} />
      </div>

      <div className="sum-card" style={{ padding: 20 }}>
        <div style={{ fontWeight: 700, marginBottom: 16 }}>구역(ROI)별 집계</div>
        <BarChart data={stats.roiCounts} />
      </div>
    </div>
  );
}
