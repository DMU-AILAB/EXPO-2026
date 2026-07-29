export const initialRoiList = [
  {
    id: 'roi-1',
    name: '계단 앞',
    voicePrompt: '계단 앞입니다',
    active: true,
    points: [
      [60, 260],
      [220, 260],
      [220, 380],
      [60, 380],
    ],
  },
  {
    id: 'roi-2',
    name: '3번 출구',
    voicePrompt: '3번 출구 앞입니다',
    active: true,
    points: [
      [420, 120],
      [600, 120],
      [600, 260],
      [420, 260],
    ],
  },
  {
    id: 'roi-3',
    name: '화장실 앞',
    voicePrompt: '화장실 앞입니다',
    active: false,
    points: [
      [700, 300],
      [860, 300],
      [860, 420],
      [700, 420],
    ],
  },
];

export const initialEventLog = [
  { time: '14:32:10', className: 'white_cane', roi: 'ROI-2 · 3번 출구' },
  { time: '14:31:47', className: 'person', roi: 'ROI-1 · 계단 앞' },
  { time: '14:30:58', className: 'white_cane', roi: 'ROI-3 · 화장실 앞' },
  { time: '14:29:32', className: 'person', roi: 'ROI-2 · 3번 출구' },
  { time: '14:28:05', className: 'person', roi: 'ROI-1 · 계단 앞' },
  { time: '14:26:41', className: 'white_cane', roi: 'ROI-1 · 계단 앞' },
  { time: '14:25:12', className: 'person', roi: 'ROI-3 · 화장실 앞' },
  { time: '14:23:58', className: 'person', roi: 'ROI-2 · 3번 출구' },
];

const TODAY = new Date().toISOString().slice(0, 10);

export const initialClips = [
  { id: 'clip-1', date: TODAY, startedAt: '13:58', durationSec: 9, label: '수동 녹화' },
  { id: 'clip-2', date: TODAY, startedAt: '13:41', durationSec: 15, label: '수동 녹화' },
];

function buildHourlySeries(peakHours, peakValue) {
  return Array.from({ length: 25 }, (_, hour) => {
    const noise = Math.sin(hour * 1.3) * 4;
    const bumps = peakHours.reduce((acc, [center, spread], i) => {
      const dist = Math.abs(hour - center);
      return acc + Math.max(0, peakValue[i] - dist * spread);
    }, 0);
    return Math.max(0, Math.round(bumps + noise + 3));
  });
}

export const statsByPeriod = {
  today: {
    label: '오늘',
    totalTraffic: 128,
    caneUsers: 7,
    caneRatio: 5.5,
    hourly: buildHourlySeries(
      [
        [8, 3],
        [18, 4],
      ],
      [30, 26]
    ),
    roiCounts: [
      { name: 'ROI-1 계단 앞', value: 58 },
      { name: 'ROI-2 3번 출구', value: 41 },
      { name: 'ROI-3 화장실 앞', value: 29 },
    ],
  },
  week: {
    label: '7일',
    totalTraffic: 812,
    caneUsers: 51,
    caneRatio: 6.3,
    hourly: buildHourlySeries(
      [
        [9, 2.5],
        [17, 3.5],
      ],
      [140, 120]
    ),
    roiCounts: [
      { name: 'ROI-1 계단 앞', value: 340 },
      { name: 'ROI-2 3번 출구', value: 288 },
      { name: 'ROI-3 화장실 앞', value: 184 },
    ],
  },
  month: {
    label: '30일',
    totalTraffic: 3421,
    caneUsers: 214,
    caneRatio: 6.3,
    hourly: buildHourlySeries(
      [
        [8, 2],
        [19, 3],
      ],
      [520, 470]
    ),
    roiCounts: [
      { name: 'ROI-1 계단 앞', value: 1450 },
      { name: 'ROI-2 3번 출구', value: 1210 },
      { name: 'ROI-3 화장실 앞', value: 761 },
    ],
  },
};

export const detectionClasses = ['white_cane', 'person'];

export const roiLabels = ['ROI-1 · 계단 앞', 'ROI-2 · 3번 출구', 'ROI-3 · 화장실 앞'];
