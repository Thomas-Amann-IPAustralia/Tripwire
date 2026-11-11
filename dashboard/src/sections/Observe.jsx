import React from 'react';
import { useDashboard } from '../App.jsx';
import { useRuns, useRunsSummary, useSources } from '../hooks/useData.js';
import FilterBar from '../components/FilterBar.jsx';
import ErrorBanner from '../components/ErrorBanner.jsx';
import { PageHeader, Loading } from '../components/ui.jsx';
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
  // Server-side aggregation over the FULL filtered history — the funnel must
  // not undercount when the row-level list is large.
  const { data: runsSummary } = useRunsSummary(filters);
  const { data: sources } = useSources();

  return (
    <div className="tw-page">
      <PageHeader
        eyebrow="Monitor · Stages 1 to 7"
        title="Pipeline activity"
        description="How source checks moved through the pipeline, where alerts clustered and which sources are doing the work. Filters apply to every panel on this page."
      />

      <div style={{ position: 'sticky', top: 0, zIndex: 30, margin: '0 -4px 18px', padding: '0 4px' }}>
        <FilterBar />
      </div>

      <div className="tw-stack">
        {isLoading && <Loading>Loading pipeline runs…</Loading>}
        <ErrorBanner error={error} />

        <FunnelSummary
          runs={runs ?? []}
          summary={runsSummary}
          onStageClick={setStageMin}
        />

        <CalendarHeatmap
          runs={runs ?? []}
          onDayClick={date => setDateRange(date, date)}
        />

        <TimelineSwimLane
          runs={runs ?? []}
          sources={sources ?? []}
        />

        <div className="tw-grid-60-40">
          <SourceMatrix runs={runs ?? []} sources={sources ?? []} />
          <StageDonutGrid runs={runs ?? []} />
        </div>

        <PrecisionTracker />

        <TriggeredEventsTable runs={runs ?? []} />
      </div>

      <EventDrawer />
    </div>
  );
}
