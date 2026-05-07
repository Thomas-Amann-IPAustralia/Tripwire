import React, { useState, useMemo } from 'react';
import { useLLMReports } from '../hooks/useData.js';
import ErrorBanner from '../components/ErrorBanner.jsx';

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmtDate(ts) {
  if (!ts) return '—';
  try {
    return new Date(ts).toLocaleDateString('en-AU', {
      day: '2-digit', month: 'short', year: 'numeric',
    });
  } catch { return String(ts); }
}

function fmtTime(ts) {
  if (!ts) return '';
  try {
    return new Date(ts).toLocaleTimeString('en-AU', {
      hour: '2-digit', minute: '2-digit', hour12: false,
    });
  } catch { return ''; }
}

function fmtPct(v) {
  if (v == null) return '—';
  return `${Math.round(Number(v) * 100)}%`;
}

// ── Verdict pill ─────────────────────────────────────────────────────────────

const VERDICT_CONFIG = {
  CHANGE_REQUIRED: { color: 'var(--state-alert)',    label: 'CHANGE REQUIRED' },
  UNCERTAIN:       { color: 'var(--state-warn)',     label: 'UNCERTAIN'       },
  NO_CHANGE:       { color: 'var(--state-ok)',       label: 'NO CHANGE'       },
};

function VerdictPill({ verdict, large = false }) {
  const cfg = VERDICT_CONFIG[verdict] ?? {
    color: 'var(--text-tertiary)',
    label: String(verdict ?? '—').toUpperCase().replace(/_/g, ' '),
  };
  return (
    <span style={{
      fontFamily: 'var(--font-mono)',
      fontSize: large ? '11px' : '9px',
      letterSpacing: '0.06em',
      color: cfg.color,
      border: `1px solid ${cfg.color}`,
      padding: large ? '3px 8px' : '1px 5px',
      whiteSpace: 'nowrap',
      flexShrink: 0,
    }}>
      {cfg.label}
    </span>
  );
}

// ── Confidence bar ────────────────────────────────────────────────────────────

function ConfidenceBar({ value }) {
  const pct = Math.round((value ?? 0) * 100);
  const color = pct >= 70 ? 'var(--state-alert)' : pct >= 50 ? 'var(--state-warn)' : 'var(--state-ok)';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 }}>
      <div style={{
        flex: 1, height: '3px', background: 'var(--rule)',
        position: 'relative', minWidth: '60px',
      }}>
        <div style={{
          position: 'absolute', left: 0, top: 0,
          height: '100%', width: `${pct}%`,
          background: color,
          transition: 'width 300ms ease',
        }} />
      </div>
      <span style={{
        fontFamily: 'var(--font-display)', fontSize: '14px',
        color, lineHeight: 1, flexShrink: 0, minWidth: '36px',
      }}>
        {pct}%
      </span>
    </div>
  );
}

// ── Stat strip ────────────────────────────────────────────────────────────────

function StatStrip({ data }) {
  if (!data) return null;
  const { all_count, verdict_counts } = data;
  const vc = verdict_counts ?? {};

  const items = [
    { label: 'Total Assessments',  value: all_count ?? 0,                      color: undefined },
    { label: 'Change Required',    value: vc.CHANGE_REQUIRED ?? 0,             color: 'var(--state-alert)' },
    { label: 'Uncertain',          value: vc.UNCERTAIN ?? 0,                   color: 'var(--state-warn)' },
    { label: 'No Change',          value: vc.NO_CHANGE ?? 0,                   color: 'var(--state-ok)' },
  ];

  return (
    <div style={{
      display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)',
      gap: '1px', background: 'var(--rule)',
      borderBottom: '1px solid var(--rule)',
      flexShrink: 0,
    }}>
      {items.map(({ label, value, color }) => (
        <div key={label} style={{
          background: 'var(--bg-secondary)',
          padding: '14px 18px',
          display: 'flex', flexDirection: 'column', gap: '6px',
        }}>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: '9px',
            color: 'var(--text-tertiary)', letterSpacing: '0.09em', textTransform: 'uppercase',
          }}>
            {label}
          </div>
          <div style={{
            fontFamily: 'var(--font-display)', fontSize: '32px',
            lineHeight: 1, color: color ?? 'var(--text-primary)',
          }}>
            {value}
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Filter bar ────────────────────────────────────────────────────────────────

const VERDICTS = ['ALL', 'CHANGE_REQUIRED', 'UNCERTAIN', 'NO_CHANGE'];

function FilterBar({ verdict, onVerdict, search, onSearch }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: '12px',
      padding: '10px 18px', borderBottom: '1px solid var(--rule)',
      flexShrink: 0, flexWrap: 'wrap',
    }}>
      <div style={{
        fontFamily: 'var(--font-mono)', fontSize: '9px',
        color: 'var(--text-tertiary)', letterSpacing: '0.08em',
        whiteSpace: 'nowrap',
      }}>
        FILTER
      </div>
      <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
        {VERDICTS.map(v => {
          const cfg = VERDICT_CONFIG[v];
          const isActive = verdict === v;
          const color = cfg?.color ?? 'var(--text-tertiary)';
          return (
            <button
              key={v}
              onClick={() => onVerdict(v)}
              style={{
                background: isActive ? color : 'transparent',
                border: `1px solid ${isActive ? color : 'var(--rule)'}`,
                color: isActive ? 'var(--bg-primary)' : color,
                fontFamily: 'var(--font-mono)', fontSize: '9px',
                letterSpacing: '0.06em', padding: '3px 10px',
                cursor: 'pointer', transition: 'all 150ms ease',
                whiteSpace: 'nowrap',
              }}
            >
              {v === 'ALL' ? 'ALL' : (cfg?.label ?? v)}
            </button>
          );
        })}
      </div>
      <input
        type="text"
        placeholder="Search page ID or reasoning…"
        value={search}
        onChange={e => onSearch(e.target.value)}
        style={{
          marginLeft: 'auto',
          background: 'var(--bg-secondary)',
          border: '1px solid var(--rule)',
          color: 'var(--text-primary)',
          fontFamily: 'var(--font-mono)', fontSize: '11px',
          padding: '5px 10px', width: '260px',
          outline: 'none',
        }}
      />
    </div>
  );
}

// ── Report card ───────────────────────────────────────────────────────────────

function ReportCard({ report }) {
  const [expanded, setExpanded] = useState(false);
  const hasSuggestions = Array.isArray(report.suggested_changes) && report.suggested_changes.length > 0;

  return (
    <div
      style={{
        borderBottom: '1px solid var(--rule)',
        background: expanded ? 'var(--bg-accent)' : 'transparent',
        transition: 'background 150ms ease',
      }}
    >
      {/* Card header — always visible */}
      <div
        onClick={() => setExpanded(e => !e)}
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr auto auto auto',
          gap: '16px',
          alignItems: 'center',
          padding: '14px 18px',
          cursor: 'pointer',
          userSelect: 'none',
        }}
        onMouseEnter={e => { if (!expanded) e.currentTarget.parentElement.style.background = 'rgba(255,255,255,0.02)'; }}
        onMouseLeave={e => { if (!expanded) e.currentTarget.parentElement.style.background = 'transparent'; }}
      >
        {/* Left: page + run info */}
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            <span style={{
              fontFamily: 'var(--font-display)', fontSize: '16px',
              color: 'var(--text-primary)', letterSpacing: '0.03em',
            }}>
              {report.ipfr_page_id || '—'}
            </span>
            <span style={{
              fontFamily: 'var(--font-mono)', fontSize: '9px',
              color: 'var(--text-tertiary)', letterSpacing: '0.05em',
            }}>
              {report.run_id}
            </span>
          </div>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: '10px',
            color: 'var(--text-secondary)', marginTop: '3px',
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          }}>
            {report.reasoning
              ? report.reasoning.slice(0, 120) + (report.reasoning.length > 120 ? '…' : '')
              : '—'}
          </div>
        </div>

        {/* Confidence */}
        <div style={{ width: '140px', flexShrink: 0 }}>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: '9px',
            color: 'var(--text-tertiary)', letterSpacing: '0.07em', marginBottom: '4px',
          }}>
            CONFIDENCE
          </div>
          <ConfidenceBar value={report.confidence} />
        </div>

        {/* Verdict */}
        <VerdictPill verdict={report.verdict} />

        {/* Expand chevron */}
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: '10px',
          color: 'var(--text-tertiary)', flexShrink: 0,
        }}>
          {expanded ? '▾' : '▸'}
        </span>
      </div>

      {/* Expanded body */}
      {expanded && (
        <div style={{
          padding: '0 18px 20px',
          display: 'flex', flexDirection: 'column', gap: '16px',
        }}>

          {/* Meta row */}
          <div style={{
            display: 'flex', gap: '24px', flexWrap: 'wrap',
            padding: '10px 14px',
            background: 'var(--bg-secondary)',
            border: '1px solid var(--rule)',
          }}>
            {[
              { label: 'Verdict',     value: <VerdictPill verdict={report.verdict} large /> },
              { label: 'Confidence',  value: fmtPct(report.confidence) },
              { label: 'Model',       value: report.model ?? '—' },
              { label: 'Prompt Tokens',     value: report.prompt_tokens ?? '—' },
              { label: 'Completion Tokens', value: report.completion_tokens ?? '—' },
              { label: 'Total Tokens',      value: report.total_tokens ?? '—' },
              { label: 'Processing Time',   value: report.processing_time_seconds != null ? `${report.processing_time_seconds.toFixed(2)}s` : '—' },
              { label: 'Retries',     value: report.retries ?? 0 },
              { label: 'Generated',   value: report.generated_at ? `${fmtDate(report.generated_at)} ${fmtTime(report.generated_at)}` : '—' },
            ].map(({ label, value }) => (
              <div key={label} style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
                <div style={{
                  fontFamily: 'var(--font-mono)', fontSize: '9px',
                  color: 'var(--text-tertiary)', letterSpacing: '0.07em',
                  textTransform: 'uppercase',
                }}>
                  {label}
                </div>
                <div style={{
                  fontFamily: typeof value === 'string' ? 'var(--font-display)' : undefined,
                  fontSize: '13px', color: 'var(--text-primary)',
                }}>
                  {value}
                </div>
              </div>
            ))}
          </div>

          {/* Reasoning */}
          <div>
            <div style={{
              fontFamily: 'var(--font-mono)', fontSize: '9px',
              color: 'var(--text-tertiary)', letterSpacing: '0.08em',
              textTransform: 'uppercase', marginBottom: '8px',
            }}>
              Reasoning
            </div>
            <div style={{
              fontFamily: 'var(--font-body)', fontSize: '14px',
              lineHeight: 1.65, color: 'var(--text-secondary)',
              padding: '12px 14px',
              background: 'var(--bg-secondary)',
              border: '1px solid var(--rule)',
            }}>
              {report.reasoning || '—'}
            </div>
          </div>

          {/* Suggested changes */}
          {hasSuggestions && (
            <div>
              <div style={{
                fontFamily: 'var(--font-mono)', fontSize: '9px',
                color: 'var(--state-alert)', letterSpacing: '0.08em',
                textTransform: 'uppercase', marginBottom: '8px',
              }}>
                Suggested Changes
              </div>
              <div style={{
                display: 'flex', flexDirection: 'column', gap: '6px',
              }}>
                {report.suggested_changes.map((change, i) => (
                  <div key={i} style={{
                    display: 'flex', gap: '12px', alignItems: 'flex-start',
                    padding: '10px 14px',
                    background: 'rgba(248,113,113,0.06)',
                    border: '1px solid rgba(248,113,113,0.25)',
                  }}>
                    <span style={{
                      fontFamily: 'var(--font-display)', fontSize: '14px',
                      color: 'var(--state-alert)', flexShrink: 0, lineHeight: 1.4,
                    }}>
                      {i + 1}.
                    </span>
                    <span style={{
                      fontFamily: 'var(--font-body)', fontSize: '13px',
                      lineHeight: 1.6, color: 'var(--text-primary)',
                    }}>
                      {change}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* No suggestions note for NO_CHANGE / UNCERTAIN */}
          {!hasSuggestions && report.verdict !== 'CHANGE_REQUIRED' && (
            <div style={{
              fontFamily: 'var(--font-mono)', fontSize: '10px',
              color: report.verdict === 'NO_CHANGE' ? 'var(--state-ok)' : 'var(--state-warn)',
              padding: '10px 14px',
              border: `1px solid ${report.verdict === 'NO_CHANGE' ? 'var(--state-ok)' : 'var(--state-warn)'}`,
              background: report.verdict === 'NO_CHANGE'
                ? 'rgba(74,222,128,0.05)'
                : 'rgba(251,191,36,0.05)',
            }}>
              {report.verdict === 'NO_CHANGE'
                ? 'No amendments required — IPFR page content remains accurate.'
                : 'Outcome is ambiguous — content owner should review manually.'}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Empty state ───────────────────────────────────────────────────────────────

function EmptyState({ hasFilter }) {
  return (
    <div style={{
      flex: 1, display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center',
      gap: '12px', padding: '60px 24px',
    }}>
      <div style={{
        fontFamily: 'var(--font-display)', fontSize: '32px',
        color: 'var(--text-tertiary)', letterSpacing: '0.06em',
        textAlign: 'center',
      }}>
        {hasFilter ? 'NO MATCHING REPORTS' : 'NO LLM REPORTS YET'}
      </div>
      <div style={{
        fontFamily: 'var(--font-mono)', fontSize: '11px',
        color: 'var(--text-tertiary)', textAlign: 'center', maxWidth: '420px',
        lineHeight: 1.6,
      }}>
        {hasFilter
          ? 'Try adjusting the verdict filter or clearing your search.'
          : 'LLM reports are generated when the pipeline runs in live mode and trigger bundles are sent for assessment. Reports are saved to data/LLM Reports/.'}
      </div>
    </div>
  );
}

// ── Main Consider section ─────────────────────────────────────────────────────

export default function Consider() {
  const [verdictFilter, setVerdictFilter] = useState('ALL');
  const [search, setSearch] = useState('');

  const apiVerdict = verdictFilter === 'ALL' ? undefined : verdictFilter;
  const { data: raw, error, isLoading } = useLLMReports({ verdict: apiVerdict });

  const allReports = useMemo(() => {
    const base = Array.isArray(raw?.data) ? raw.data : [];
    if (!search.trim()) return base;
    const q = search.toLowerCase();
    return base.filter(r =>
      (r.ipfr_page_id ?? '').toLowerCase().includes(q) ||
      (r.reasoning ?? '').toLowerCase().includes(q) ||
      (r.run_id ?? '').toLowerCase().includes(q) ||
      (r.suggested_changes ?? []).some(s => s.toLowerCase().includes(q))
    );
  }, [raw, search]);

  const hasFilter = verdictFilter !== 'ALL' || search.trim() !== '';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>

      {/* Section header */}
      <div style={{
        height: '80px', minHeight: '80px',
        display: 'flex', alignItems: 'flex-end',
        padding: '0 24px 12px',
        borderBottom: '1px solid var(--rule)',
        flexShrink: 0,
      }}>
        <div>
          <div style={{
            fontFamily: 'var(--font-display)', fontSize: '42px',
            lineHeight: 1, letterSpacing: '0.04em',
            color: 'var(--stage-8)',
          }}>
            CONSIDER
          </div>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: '11px',
            color: 'var(--text-secondary)', marginTop: '2px',
          }}>
            LLM Assessment Report Library — Stage 8
          </div>
        </div>
      </div>

      <ErrorBanner error={error} />

      {/* Stat strip */}
      <StatStrip data={raw} />

      {/* Filter bar */}
      <FilterBar
        verdict={verdictFilter}
        onVerdict={setVerdictFilter}
        search={search}
        onSearch={setSearch}
      />

      {/* Report list */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        {isLoading && (
          <div style={{
            padding: '24px 18px',
            fontFamily: 'var(--font-mono)', fontSize: '10px',
            color: 'var(--text-tertiary)', letterSpacing: '0.07em',
          }}>
            LOADING REPORTS…
          </div>
        )}

        {!isLoading && allReports.length === 0 && (
          <EmptyState hasFilter={hasFilter} />
        )}

        {!isLoading && allReports.length > 0 && (
          <div>
            <div style={{
              padding: '8px 18px', borderBottom: '1px solid var(--rule)',
              fontFamily: 'var(--font-mono)', fontSize: '9px',
              color: 'var(--text-tertiary)', letterSpacing: '0.07em',
            }}>
              {allReports.length} REPORT{allReports.length !== 1 ? 'S' : ''}
              {hasFilter ? ' (filtered)' : ''}
            </div>
            {allReports.map((report, i) => (
              <ReportCard key={report._filename ?? i} report={report} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
