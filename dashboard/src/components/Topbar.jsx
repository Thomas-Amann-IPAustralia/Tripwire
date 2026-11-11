import React from 'react';
import { useLocation } from 'react-router-dom';
import { useHealthSummary } from '../hooks/useData.js';
import { useDashboard } from '../App.jsx';
import { useTheme } from '../hooks/useTheme.js';
import { formatRelativeTime } from '../lib/dataUtils.js';
import { NAV_ITEMS } from './NavRail.jsx';
import { Icon, Badge } from './ui.jsx';

function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();
  const isDark = theme === 'dark';
  return (
    <button
      type="button"
      className="tw-btn is-sm"
      onClick={toggleTheme}
      aria-pressed={isDark}
      title={isDark ? 'Switch to the light theme' : 'Switch to the dark theme'}
    >
      <Icon name={isDark ? 'sun' : 'moon'} />
      <span className="tw-hide-sm">{isDark ? 'Light' : 'Dark'}</span>
    </button>
  );
}

function PipelineStatus({ status }) {
  const s = String(status || 'IDLE').toUpperCase();
  if (s === 'RUNNING') return <Badge tone="info" dot>Running</Badge>;
  if (s === 'ERROR')   return <Badge tone="error" dot>Error</Badge>;
  if (s === 'IDLE')    return <Badge tone="muted" dot>Idle</Badge>;
  return <Badge tone="ok" dot>{s.charAt(0) + s.slice(1).toLowerCase()}</Badge>;
}

function StageDots({ stagesCompleted }) {
  return (
    <span
      style={{ display: 'flex', gap: '4px', alignItems: 'center' }}
      title={`${stagesCompleted || 0} of 9 stages reached on the last run`}
      aria-label={`${stagesCompleted || 0} of 9 stages reached on the last run`}
    >
      {Array.from({ length: 9 }, (_, i) => {
        const n = i + 1;
        const done = stagesCompleted && stagesCompleted >= n;
        return (
          <span
            key={n}
            style={{
              width: '7px',
              height: '7px',
              borderRadius: '50%',
              background: done ? `var(--stage-${n})` : 'transparent',
              border: `1.5px solid ${done ? `var(--stage-${n})` : 'var(--line-strong)'}`,
              display: 'inline-block',
            }}
          />
        );
      })}
    </span>
  );
}

export default function Topbar() {
  const { refresh } = useDashboard();
  const { data: health } = useHealthSummary();
  const location = useLocation();

  const current = NAV_ITEMS.find(i => location.pathname.startsWith(i.path));

  const lastRunData = health?.data?.last_run;
  const lastRun = lastRunData?.timestamp;
  const pipelineStatus = lastRunData?.pipeline_status || 'IDLE';
  const stagesCompleted = lastRunData?.stages_completed ?? 0;

  return (
    <header className="tw-topbar">
      <div className="tw-crumbs">
        <span className="tw-crumb-root">Tripwire</span>
        <Icon name="chevron" />
        <strong>{current?.label ?? 'Dashboard'}</strong>
      </div>

      <div className="tw-topbar-right">
        <div className="tw-run-meta">
          Last pipeline run<br />
          <strong>{lastRun ? formatRelativeTime(lastRun) : 'No runs recorded'}</strong>
        </div>
        <PipelineStatus status={pipelineStatus} />
        <span className="tw-hide-sm"><StageDots stagesCompleted={stagesCompleted} /></span>
        <div className="tw-topbar-divider tw-hide-sm" />
        <ThemeToggle />
        <button type="button" className="tw-btn is-sm" onClick={refresh} title="Reload all data">
          <Icon name="refresh" />
          <span className="tw-hide-sm">Refresh</span>
        </button>
      </div>
    </header>
  );
}
