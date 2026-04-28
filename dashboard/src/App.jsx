import React, { createContext, useContext, useState, useCallback, useRef } from 'react';
import { HashRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import NavRail from './components/NavRail.jsx';
import Topbar from './components/Topbar.jsx';
import Observe from './sections/Observe.jsx';
import Corpus from './sections/Corpus.jsx';
import Sources from './sections/Sources.jsx';
import Adjust from './sections/Adjust.jsx';
import Document from './sections/Document.jsx';
import Health from './sections/Health.jsx';
import { useFilters } from './hooks/useFilters.js';

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
        <div style={{
          padding: '2rem',
          color: 'var(--state-error)',
          fontFamily: 'var(--font-mono)',
          fontSize: '12px',
          border: '1px solid var(--state-error)',
          margin: '1rem',
        }}>
          <div style={{ fontFamily: 'var(--font-display)', fontSize: '18px', marginBottom: '0.5rem' }}>
            SECTION ERROR
          </div>
          {this.state.error?.message || 'Unknown error'}
        </div>
      );
    }
    return this.props.children;
  }
}

function AnimatedRoutes() {
  const location = useLocation();
  return (
    <div key={location.pathname} className="section-enter" style={{ flex: 1, overflow: 'auto' }}>
      <Routes location={location}>
        <Route path="/" element={<Navigate to="/observe" replace />} />
        <Route path="/observe"  element={<SectionErrorBoundary><Observe /></SectionErrorBoundary>} />
        <Route path="/corpus"   element={<SectionErrorBoundary><Corpus /></SectionErrorBoundary>} />
        <Route path="/sources"  element={<SectionErrorBoundary><Sources /></SectionErrorBoundary>} />
        <Route path="/adjust"   element={<SectionErrorBoundary><Adjust /></SectionErrorBoundary>} />
        <Route path="/document" element={<SectionErrorBoundary><Document /></SectionErrorBoundary>} />
        <Route path="/health"   element={<SectionErrorBoundary><Health /></SectionErrorBoundary>} />
      </Routes>
    </div>
  );
}

function DashboardProvider({ children }) {
  const queryClient = useQueryClient();
  const { filters, setDatePreset, setDateRange, setSources, setStageMin, setVerdicts, toQueryParams } = useFilters();
  const [selectedRunId, setSelectedRunId] = useState(null);
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

function AppLayout() {
  const [navExpanded, setNavExpanded] = useState(false);

  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden' }}>
      <NavRail expanded={navExpanded} onToggle={() => setNavExpanded(e => !e)} />
      <div style={{ display: 'flex', flexDirection: 'column', flex: 1, overflow: 'hidden' }}>
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
