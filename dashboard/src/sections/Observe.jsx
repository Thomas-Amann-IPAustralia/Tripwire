import React from 'react';
import { useDashboard } from '../App.jsx';
import { useRuns, useSources } from '../hooks/useData.js';
import FilterBar from '../components/FilterBar.jsx';
import ErrorBanner from '../components/ErrorBanner.jsx';
import { EventDrawer } from '../components/EventDrawer.jsx';
import FunnelSummary from '../visualisations/FunnelSummary.jsx';
import CalendarHeatmap from '../visualisations/CalendarHeatmap.jsx';
import TimelineSwimLane from '../visualisations/TimelineSwimLane.jsx';
import SourceMatrix from '../visualisations/SourceMatrix.jsx';
import StageDonutGrid from '../visualisations/StageDonutGrid.jsx';
import PrecisionTracker from '../visualisations/PrecisionTracker.jsx';
import TriggeredEventsTable from '../visualisations/TriggeredEventsTable.jsx';

export default function Observe() {
  const { filters, setStageMin, setDateRange } = useDashboard();
  const { data: runs,    isLoading, error } = useRuns(filters);
  const { data: sources } = useSources();

  return (
    <div style={{ height: '100%', overflowY: 'auto', display: 'flex', flexDirection: 'column' }}>

      {/* Section header band */}
      <div style={{
        height: '80px', minHeight: '80px',
        display: 'flex', alignItems: 'center',
        padding: '0 24px', borderBottom: '1px solid var(--rule)', gap: '16px',
      }}>
        <div>
          <div style={{
            fontFamily: 'var(--font-display)', fontSize: '42px',
            lineHeight: 1, color: 'var(--text-primary)',
          }}>
            OBSERVE
          </div>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: '11px',
            color: 'var(--text-tertiary)', letterSpacing: '0.06em',
          }}>
            Pipeline Analytics
          </div>
        </div>
        <div style={{ flex: 1 }} />
        <FilterBar />
      </div>

      {/* Main content */}
      <div style={{ flex: 1, padding: '16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>

        {isLoading && (
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--text-tertiary)', padding: '8px 0' }}>
            Loading…
          </div>
        )}
        <ErrorBanner error={error} />

        {/* Panel Row 1 — Funnel Summary */}
        <FunnelSummary
          runs={runs ?? []}
          onStageClick={setStageMin}
        />

        {/* Panel Row 2 — Calendar Heatmap */}
        <CalendarHeatmap
          runs={runs ?? []}
          onDayClick={date => setDateRange(date, date)}
        />

        {/* Panel Row 3 — Timeline Swim Lane */}
        <TimelineSwimLane
          runs={runs ?? []}
          sources={sources ?? []}
        />

        {/* Panel Row 4 — Source Matrix (60%) + Stage Donut Grid (40%) */}
        <div style={{ display: 'grid', gridTemplateColumns: '60% 40%', gap: '12px' }}>
          <SourceMatrix runs={runs ?? []} sources={sources ?? []} />
          <StageDonutGrid runs={runs ?? []} />
        </div>

        {/* Panel Row 5 — Precision Tracker */}
        <PrecisionTracker />

        {/* Panel Row 6 — Triggered Events Table */}
        <TriggeredEventsTable runs={runs ?? []} />

      </div>

      {/* Event Drawer — overlays the viewport */}
      <EventDrawer />
    </div>
  );
}
