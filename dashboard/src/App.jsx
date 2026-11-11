import React, { createContext, useContext, useState, useCallback } from 'react';
import { HashRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import NavRail from './components/NavRail.jsx';
import Topbar from './components/Topbar.jsx';
import Consider from './sections/Consider.jsx';
import Observe from './sections/Observe.jsx';
import Corpus from './sections/Corpus.jsx';
import Sources from './sections/Sources.jsx';
import Adjust from './sections/Adjust.jsx';
import Document from './sections/Document.jsx';
import Health from './sections/Health.jsx';
import { useFilters } from './hooks/useFilters.js';
import { Callout } from './components/ui.jsx';

export const DashboardContext = createContext(null);

export function useDashboard() {
  return useContext(DashboardContext);
}

class SectionErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="tw-page">
          <Callout tone="error" icon="alert" title="This page could not be displayed">
            {this.state.error?.message || 'Unknown error'}. Try refreshing; other pages are unaffected.
          </Callout>
        </div>
      );
    }
    return this.props.children;
  }
}

function AnimatedRoutes() {
  const location = useLocation();
  return (
    <main key={location.pathname} className="tw-route section-enter">
      <Routes location={location}>
        <Route path="/" element={<Navigate to="/consider" replace />} />
        <Route path="/consider" element={<SectionErrorBoundary><Consider /></SectionErrorBoundary>} />
        <Route path="/observe"  element={<SectionErrorBoundary><Observe /></SectionErrorBoundary>} />
        <Route path="/corpus"   element={<SectionErrorBoundary><Corpus /></SectionErrorBoundary>} />
        <Route path="/sources"  element={<SectionErrorBoundary><Sources /></SectionErrorBoundary>} />
        <Route path="/adjust"   element={<SectionErrorBoundary><Adjust /></SectionErrorBoundary>} />
        <Route path="/document" element={<SectionErrorBoundary><Document /></SectionErrorBoundary>} />
        <Route path="/health"   element={<SectionErrorBoundary><Health /></SectionErrorBoundary>} />
      </Routes>
    </main>
  );
}

function DashboardProvider({ children }) {
  const queryClient = useQueryClient();
  const { filters, setDatePreset, setDateRange, setSources, setStageMin, setVerdicts, toQueryParams } = useFilters();
  const [selectedRunId, setSelectedRunId] = useState(null);
  // A run_id covers every source checked in that pipeline run; the row id
  // pins the drawer to the specific source row the user clicked.
  const [selectedRowId, setSelectedRowId] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const refresh = useCallback(() => {
    queryClient.invalidateQueries();
  }, [queryClient]);

  const value = {
    filters,
    setDatePreset,
    setDateRange,
    setSources,
    setStageMin,
    setVerdicts,
    toQueryParams,
    selectedRunId,
    setSelectedRunId,
    selectedRowId,
    setSelectedRowId,
    drawerOpen,
    setDrawerOpen,
    refresh,
  };

  return (
    <DashboardContext.Provider value={value}>
      {children}
    </DashboardContext.Provider>
  );
}

const NAV_KEY = 'tripwire-nav-collapsed';

function AppLayout() {
  const [navCollapsed, setNavCollapsed] = useState(() => {
    try { return localStorage.getItem(NAV_KEY) === '1'; } catch { return false; }
  });
  const toggleNav = useCallback(() => {
    setNavCollapsed(c => {
      try { localStorage.setItem(NAV_KEY, c ? '0' : '1'); } catch { /* storage unavailable */ }
      return !c;
    });
  }, []);

  return (
    <div className="tw-shell">
      <NavRail collapsed={navCollapsed} onToggle={toggleNav} />
      <div className="tw-main">
        <Topbar />
        <AnimatedRoutes />
      </div>
    </div>
  );
}

export default function App() {
  return (
    <HashRouter>
      <DashboardProvider>
        <AppLayout />
      </DashboardProvider>
    </HashRouter>
  );
}
