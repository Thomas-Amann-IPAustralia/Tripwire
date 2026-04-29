import React from 'react';
import { useDashboard } from '../App.jsx';
import { useRuns, useSources } from '../hooks/useData.js';
import FilterBar from '../components/FilterBar.jsx';
import FunnelSummary from '../visualisations/FunnelSummary.jsx';
import CalendarHeatmap from '../visualisations/CalendarHeatmap.jsx';
import TimelineSwimLane from '../visualisations/TimelineSwimLane.jsx';

export default function Observe() {
  const { filters, setStageMin, setDateRange } = useDashboard();
  const { data: runsResp,    isLoading, error } = useRuns(filters);
  const { data: sourcesResp } = useSources();
  const runs    = runsResp?.data    ?? [];
  const sources = sourcesResp?.data ?? [];

  return (
    <div style={{ height: '100%', overflowY: 'auto', display: 'flex', flexDirection: 'column' }}>

      {/* Section header band — 80px */}
      <div style={{
        height: '80px',
        minHeight: '80px',
        display: 'flex',
        alignItems: 'center',
        padding: '0 24px',
        borderBottom: '1px solid var(--rule)',
        gap: '16px',
      }}>
        <div>
          <div style={{
            fontFamily: 'var(--font-display)',
            fontSize: '42px',
            lineHeight: 1,
            color: 'var(--text-primary)',
          }}>
            OBSERVE
          </div>
          <div style={{
            fontFamily: 'var(--font-mono)',
            fontSize: '11px',
            color: 'var(--text-tertiary)',
            letterSpacing: '0.06em',
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
        {error && (
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--state-error)', padding: '8px 0' }}>
            {error.message}
          </div>
        )}

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

        {/* Panel Row 4 — stub (Session 4: SourceMatrix) */}
        <div className="panel" style={{
          background: 'var(--bg-secondary)',
          border: '1px solid var(--rule)',
          padding: '16px',
          minHeight: '64px',
          display: 'flex',
          alignItems: 'center',
        }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)', letterSpacing: '0.08em' }}>
            SOURCE MATRIX — Session 4
          </span>
        </div>

        {/* Panel Row 5 — stub (Session 4: StageDonutGrid + PrecisionTracker) */}
        <div className="panel" style={{
          background: 'var(--bg-secondary)',
          border: '1px solid var(--rule)',
          padding: '16px',
          minHeight: '64px',
          display: 'flex',
          alignItems: 'center',
        }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)', letterSpacing: '0.08em' }}>
            STAGE DONUT GRID · PRECISION TRACKER — Session 4
          </span>
        </div>

        {/* Panel Row 6 — stub (Session 4: TriggeredEventsTable) */}
        <div className="panel" style={{
          background: 'var(--bg-secondary)',
          border: '1px solid var(--rule)',
          padding: '16px',
          minHeight: '64px',
          display: 'flex',
          alignItems: 'center',
        }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)', letterSpacing: '0.08em' }}>
            TRIGGERED EVENTS TABLE — Session 4
          </span>
        </div>

        {/* EventDrawer placeholder (Session 4) */}
        <div id="event-drawer-stub" />
      </div>
    </div>
  );
}
