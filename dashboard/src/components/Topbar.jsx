import React, { useState } from 'react';
import { useHealthSummary } from '../hooks/useData.js';
import { useDashboard } from '../App.jsx';
import { formatRelativeTime } from '../lib/dataUtils.js';

function ThemeToggle() {
  const [isDark, setIsDark] = useState(() => localStorage.getItem('tw-theme') !== 'light');

  const toggle = () => {
    const next = !isDark;
    const theme = next ? 'dark' : 'light';
    localStorage.setItem('tw-theme', theme);
    if (theme === 'light') {
      document.documentElement.setAttribute('data-theme', 'light');
    } else {
      document.documentElement.removeAttribute('data-theme');
    }
    setIsDark(next);
  };

  return (
    <button
      onClick={toggle}
      title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
      style={{
        fontFamily: 'var(--font-mono)',
        fontSize: '14px',
        lineHeight: 1,
        color: 'var(--text-secondary)',
        background: 'none',
        border: '1px solid var(--rule-accent)',
        padding: '3px 8px',
        cursor: 'pointer',
        borderRadius: '2px',
      }}
      onMouseEnter={e => e.currentTarget.style.color = 'var(--text-primary)'}
      onMouseLeave={e => e.currentTarget.style.color = 'var(--text-secondary)'}
    >
      {isDark ? '☀' : '☽'}
    </button>
  );
}

function StatusPill({ status }) {
  const colour = status === 'RUNNING'
    ? 'var(--state-warn)'
    : status === 'ERROR'
      ? 'var(--state-error)'
      : 'var(--state-ok)';

  return (
    <span style={{
      fontFamily: 'var(--font-mono)',
      fontSize: '12px',
      letterSpacing: '0.08em',
      color: colour,
      border: `1px solid ${colour}`,
      padding: '2px 6px',
      borderRadius: '2px',
    }}>
      {status || 'IDLE'}
    </span>
  );
}

function StageDots({ stagesCompleted }) {
  return (
    <span style={{ display: 'flex', gap: '3px', alignItems: 'center' }}>
      {Array.from({ length: 9 }, (_, i) => {
        const n = i + 1;
        const done = stagesCompleted && stagesCompleted >= n;
        return (
          <span
            key={n}
            title={`Stage ${n}`}
            style={{
              width: '8px',
              height: '8px',
              borderRadius: '50%',
              background: done ? `var(--stage-${n})` : 'var(--bg-accent)',
              border: `1px solid ${done ? `var(--stage-${n})` : 'var(--rule-accent)'}`,
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

  const lastRunData = health?.data?.last_run;
  const lastRun = lastRunData?.timestamp;
  const pipelineStatus = lastRunData?.pipeline_status || 'IDLE';
  const stagesCompleted = lastRunData?.stages_completed ?? 0;

  return (
    <header style={{
      height: '48px',
      background: 'var(--bg-secondary)',
      borderBottom: '1px solid var(--rule)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      padding: '0 16px',
      flexShrink: 0,
      gap: '16px',
    }}>
      {/* Left — wordmark */}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '10px' }}>
        <span style={{
          fontFamily: 'var(--font-display)',
          fontSize: '22px',
          color: 'var(--text-primary)',
          letterSpacing: '0.05em',
        }}>
          ◈ TRIPWIRE
        </span>
        <span style={{
          fontFamily: 'var(--font-mono)',
          fontSize: '12px',
          color: 'var(--text-tertiary)',
          letterSpacing: '0.05em',
        }}>
          v2.0 · TW-DASHBOARD
        </span>
      </div>

      {/* Right — run info + controls */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
        {lastRun && (
          <span style={{
            fontFamily: 'var(--font-mono)',
            fontSize: '12px',
            color: 'var(--text-tertiary)',
          }}>
            {formatRelativeTime(lastRun)}
          </span>
        )}
        <StatusPill status={pipelineStatus} />
        <StageDots stagesCompleted={stagesCompleted} />
        <ThemeToggle />
        <button
          onClick={refresh}
          style={{
            fontFamily: 'var(--font-mono)',
            fontSize: '12px',
            letterSpacing: '0.1em',
            color: 'var(--text-secondary)',
            background: 'none',
            border: '1px solid var(--rule-accent)',
            padding: '3px 8px',
            cursor: 'pointer',
            borderRadius: '2px',
          }}
          onMouseEnter={e => e.currentTarget.style.color = 'var(--text-primary)'}
          onMouseLeave={e => e.currentTarget.style.color = 'var(--text-secondary)'}
        >
          REFRESH
        </button>
      </div>
    </header>
  );
}
