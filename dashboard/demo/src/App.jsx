import TopBar from './components/TopBar';
import { AppStateProvider, useAppState } from './state/AppState';
import MonitoringPage from './pages/MonitoringPage';
import RoiEditPage from './pages/RoiEditPage';
import StatsPage from './pages/StatsPage';
import RecordingsPage from './pages/RecordingsPage';

function PageSwitch() {
  const { activeTab } = useAppState();
  switch (activeTab) {
    case 'roi':
      return <RoiEditPage />;
    case 'stats':
      return <StatsPage />;
    case 'recordings':
      return <RecordingsPage />;
    case 'monitoring':
    default:
      return <MonitoringPage />;
  }
}

function App() {
  return (
    <AppStateProvider>
      <div className="app-shell">
        <TopBar />
        <PageSwitch />
      </div>
    </AppStateProvider>
  );
}

export default App;
