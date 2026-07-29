import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { initialClips, initialEventLog, initialRoiList } from '../mock/mockData';

const AppStateContext = createContext(null);

function formatDuration(sec) {
  const m = Math.floor(sec / 60)
    .toString()
    .padStart(2, '0');
  const s = Math.floor(sec % 60)
    .toString()
    .padStart(2, '0');
  return `${m}:${s}`;
}

function nowLabel() {
  const d = new Date();
  return d.toTimeString().slice(0, 8);
}

export function AppStateProvider({ children }) {
  const [activeTab, setActiveTab] = useState('monitoring');

  const [roiList, setRoiList] = useState(initialRoiList);

  const [recording, setRecording] = useState(false);
  const [elapsedSec, setElapsedSec] = useState(0);
  const recordTimerRef = useRef(null);

  const [clips, setClips] = useState(initialClips);
  const [eventLog, setEventLog] = useState(initialEventLog);

  const startRecording = useCallback(() => {
    setRecording(true);
    setElapsedSec(0);
  }, []);

  const stopRecording = useCallback(() => {
    setRecording(false);
    setElapsedSec((sec) => {
      const newClip = {
        id: `clip-${Date.now()}`,
        date: new Date().toISOString().slice(0, 10),
        startedAt: nowLabel().slice(0, 5),
        durationSec: sec,
        label: '수동 녹화',
      };
      setClips((prev) => [newClip, ...prev]);
      return sec;
    });
  }, []);

  useEffect(() => {
    if (!recording) return undefined;
    recordTimerRef.current = setInterval(() => {
      setElapsedSec((sec) => sec + 1);
    }, 1000);
    return () => clearInterval(recordTimerRef.current);
  }, [recording]);

  const upsertRoi = useCallback((roi) => {
    setRoiList((prev) => {
      const exists = prev.some((r) => r.id === roi.id);
      return exists ? prev.map((r) => (r.id === roi.id ? roi : r)) : [...prev, roi];
    });
  }, []);

  const deleteRoi = useCallback((id) => {
    setRoiList((prev) => prev.filter((r) => r.id !== id));
  }, []);

  const pushEvent = useCallback((event) => {
    setEventLog((prev) => [event, ...prev].slice(0, 8));
  }, []);

  const value = {
    activeTab,
    setActiveTab,
    roiList,
    setRoiList,
    upsertRoi,
    deleteRoi,
    recording,
    elapsedSec,
    elapsedLabel: formatDuration(elapsedSec),
    startRecording,
    stopRecording,
    clips,
    eventLog,
    pushEvent,
  };

  return <AppStateContext.Provider value={value}>{children}</AppStateContext.Provider>;
}

export function useAppState() {
  const ctx = useContext(AppStateContext);
  if (!ctx) throw new Error('useAppState must be used within AppStateProvider');
  return ctx;
}
