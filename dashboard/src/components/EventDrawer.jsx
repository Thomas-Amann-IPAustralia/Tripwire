import React, { useEffect, useState, useCallback } from 'react';
import { useDashboard } from '../App.jsx';
import { useRun } from '../hooks/useData.js';
import { StageIndicator } from './StageIndicator.jsx';
import { formatRelativeTime, formatScore } from '../lib/dataUtils.js';

/* ─── helpers ──────────────────────────────────────────────────────────────── */

function buildStages(run) {
  const sr = run?.stage_reached ?? 0;
  return Array.from({ length: 9 }, (_, i) => {
    const n = i + 1;
    const reached = n <= sr;
    let status = 'skipped';
    if (reached) {
      if (n < sr) status = 'passed';
      else status = run?.outcome === 'error' ? 'error' : 'passed';
    }
    return { n, reached, status };
  });
}

function stageSuperscripts(run) {
  const sup = {};
  if (run?.fast_pass_triggered) sup[4] = (sup[4] ?? '') + 'F';
  if (run?.graph_propagated)    sup[6] = (sup[6] ?? '') + 'G';
  if (run?.significance === 'high') sup[2] = (sup[2] ?? '') + 'H';
  return sup;
}

function confidenceColor(n) {
  if (n == null) return 'var(--text-tertiary)';
  if (n >= 0.7) return 'var(--state-ok)';
  if (n >= 0.4) return 'var(--state-warn)';
  return 'var(--state-error)';
}

/* ─── sub-components ────────────────────────────────────────────────────────── */

function SectionHeader({ children }) {
  return (
    <div style={{
      fontFamily: 'var(--font-mono)', fontSize: '10px',
      color: 'var(--text-tertiary)', letterSpacing: '0.1em',
      marginBottom: '8px',
    }}>
      {children}
    </div>
  );
}

function Section({ children, style }) {
  return (
    <div style={{
      borderBottom: '1px solid var(--rule)', padding: '12px 16px',
      ...style,
    }}>
      {children}
    </div>
  );
}

function GraphTrace({ run }) {
  if (!run?.graph_propagated) return null;
  const pages = run?.details?.stages?.crossencoder?.scored_pages;
  if (!pages?.length) return null;

  const nodes = [
    { id: run.source_id ?? 'source', label: (run.source_id ?? 'source').slice(0, 18), score: null },
    ...pages.slice(0, 3).map(p => ({
      id:    p.page_id ?? '?',
      label: (p.page_id ?? '?').slice(0, 18),
      score: p.crossencoder_score ?? p.reranked_score ?? null,
    })),
  ];

  const NODE_W = 100;
  const NODE_H = 22;
  const GAP    = 60;
  const svgW   = nodes.length * NODE_W + (nodes.length - 1) * GAP;
  const svgH   = NODE_H + 30;
  const arrowId = 'drawer-arrow';

  return (
    <Section>
      <SectionHeader>GRAPH PROPAGATION TRACE</SectionHeader>
      <div style={{ overflowX: 'auto' }}>
        <svg width={svgW} height={svgH} style={{ display: 'block' }}>
          <defs>
            <marker id={arrowId} markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
              <path d="M0,0 L6,3 L0,6" fill="none" stroke="#4a4a40" strokeWidth={1} />
            </marker>
          </defs>
          {nodes.map((node, i) => {
            const x = i * (NODE_W + GAP);
            const nextScore = nodes[i + 1]?.score;
            const opacity   = nextScore != null ? Math.min(1, Math.max(0.2, nextScore + 0.2)) : 1;
            return (
              <g key={node.id}>
                <rect
                  x={x} y={4} width={NODE_W} height={NODE_H}
                  fill="#242420" stroke="#4a4a40" strokeWidth={1}
                />
                <text
                  x={x + NODE_W / 2} y={4 + NODE_H / 2 + 3}
                  textAnchor="middle"
                  style={{ fontFamily: 'DM Mono, monospace', fontSize: '9px', fill: '#9e9888' }}
                >
                  {node.label}
                </text>
                {i < nodes.length - 1 && (
                  <>
                    <line
                      x1={x + NODE_W} y1={4 + NODE_H / 2}
                      x2={x + NODE_W + GAP - 2} y2={4 + NODE_H / 2}
                      stroke="#4a4a40" strokeWidth={1}
                      opacity={opacity}
                      markerEnd={`url(#${arrowId})`}
                    />
                    {nextScore != null && (
                      <text
                        x={x + NODE_W + GAP / 2} y={4 + NODE_H / 2 - 4}
                        textAnchor="middle"
                        style={{ fontFamily: 'DM Mono, monospace', fontSize: '9px', fill: '#5c5a52' }}
                      >
                        {formatScore(nextScore)}
                      </text>
                    )}
                  </>
                )}
              </g>
            );
          })}
        </svg>
      </div>
    </Section>
  );
}

function ScorePanel({ run }) {
  const rows = [
    { signal: 'RRF Score',          raw: run?.scores?.rrf_score,       threshold: null  },
    { signal: 'Source Importance',  raw: run?.scores?.source_importance, threshold: null },
    { signal: 'Bi-Encoder Max',     raw: run?.biencoder_max,            threshold: 0.4  },
    { signal: 'Cross-Encoder',      raw: run?.crossencoder_score,       threshold: 0.5  },
    { signal: 'Reranked Score',     raw: run?.reranked_score,           threshold: null  },
  ].filter(r => r.raw != null);

  if (!rows.length) return null;

  const th = {
    fontFamily: 'var(--font-mono)', fontSize: '11px',
    color: 'var(--text-tertiary)', textAlign: 'left',
    padding: '4px 8px', borderBottom: '1px solid var(--rule)',
  };
  const td = {
    fontFamily: 'var(--font-mono)', fontSize: '11px',
    color: 'var(--text-secondary)', padding: '4px 8px',
    borderBottom: '1px solid var(--rule)',
  };

  return (
    <Section>
      <SectionHeader>SCORE PANEL</SectionHeader>
      <table style={{ borderCollapse: 'collapse', width: '100%' }}>
        <thead>
          <tr>
            {['Signal', 'Raw Score', 'Threshold', 'Pass/Fail'].map(h => (
              <th key={h} style={th}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(({ signal, raw, threshold }) => {
            const pass = threshold != null ? Number(raw) >= threshold : null;
            return (
              <tr key={signal}>
                <td style={td}>{signal}</td>
                <td style={td}>{formatScore(raw)}</td>
                <td style={td}>{threshold != null ? threshold.toFixed(2) : '—'}</td>
                <td style={td}>
                  {pass === null ? '—'
                    : pass
                      ? <span style={{ color: 'var(--state-ok)' }}>PASS</span>
                      : <span style={{ color: 'var(--state-alert)' }}>FAIL</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Section>
  );
}

function DiffPreview({ diffText }) {
  if (!diffText) return null;
  const lines = diffText.split('\n');
  return (
    <Section>
      <SectionHeader>DIFF PREVIEW</SectionHeader>
      <pre style={{
        maxHeight: '300px', overflowY: 'auto',
        fontFamily: 'var(--font-mono)', fontSize: '11px',
        color: 'var(--text-secondary)', margin: 0,
        lineHeight: 1.5, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
      }}>
        {lines.map((line, i) => {
          let bg = 'transparent';
          if (line.startsWith('+')) bg = 'rgba(58,107,58,0.15)';
          if (line.startsWith('-')) bg = 'rgba(201,64,32,0.15)';
          return (
            <span key={i} style={{ display: 'block', background: bg }}>
              {line || ' '}
            </span>
          );
        })}
      </pre>
    </Section>
  );
}

function LLMAssessment({ run }) {
  if (!run?.verdict) return null;

  const v      = run.verdict.toUpperCase();
  const conf   = run.confidence != null ? Number(run.confidence) : null;
  const reason = run.reasoning;
  const sug    = Array.isArray(run.suggested_changes) ? run.suggested_changes : null;

  const VERDICT_S = {
    CHANGE_REQUIRED: { color: 'var(--state-alert)', border: '1px solid var(--state-alert)' },
    UNCERTAIN:       { color: 'var(--state-warn)',  border: '1px solid var(--state-warn)'  },
    NO_CHANGE:       { color: 'var(--state-ok)',    border: '1px solid var(--state-ok)'    },
  };
  const vs = VERDICT_S[v] ?? {};

  return (
    <Section>
      <SectionHeader>LLM ASSESSMENT</SectionHeader>
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '10px', flexWrap: 'wrap' }}>
        <span style={{
          ...vs, fontFamily: 'var(--font-mono)', fontSize: '11px',
          padding: '2px 8px', letterSpacing: '0.05em',
        }}>
          {v.replace(/_/g, ' ')}
        </span>
        {conf != null && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <div style={{ width: '100px', height: '5px', background: 'var(--bg-tertiary)' }}>
              <div style={{
                width: `${conf * 100}%`, height: '100%',
                background: confidenceColor(conf),
                transition: 'width 250ms ease',
              }} />
            </div>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: confidenceColor(conf) }}>
              {conf.toFixed(2)}
            </span>
          </div>
        )}
      </div>

      {reason && (
        <p style={{
          fontFamily: 'var(--font-body)', fontSize: '13px',
          color: 'var(--text-secondary)', lineHeight: 1.6,
          margin: '0 0 10px',
        }}>
          {reason}
        </p>
      )}

      {sug && sug.length > 0 && (
        <>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)', marginBottom: '6px' }}>
            SUGGESTED CHANGES
          </div>
          <ol style={{ margin: 0, paddingLeft: '20px' }}>
            {sug.map((s, i) => (
              <li key={i} style={{
                fontFamily: 'var(--font-body)', fontSize: '13px',
                color: 'var(--text-secondary)', lineHeight: 1.5, marginBottom: '4px',
              }}>
                {s}
              </li>
            ))}
          </ol>
        </>
      )}
    </Section>
  );
}

function FeedbackSection({ run }) {
  const [submitted, setSubmitted] = useState(false);
  const [selected,  setSelected]  = useState(null);
  const [posting,   setPosting]   = useState(false);
  const [error,     setError]     = useState(null);

  const CATS = [
    { key: 'useful',          label: 'Useful'          },
    { key: 'not_significant', label: 'Not Significant' },
    { key: 'wrong_amendment', label: 'Wrong Amendment' },
    { key: 'wrong_page',      label: 'Wrong Page'      },
  ];

  const submit = useCallback(async (category) => {
    if (!run?.run_id) return;
    setPosting(true);
    setError(null);
    try {
      const res = await fetch('/api/feedback/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          run_id:    run.run_id,
          page_id:   run.ipfr_page_id ?? null,
          source_id: run.source_id    ?? null,
          category,
          comment:   '',
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setSelected(category);
      setSubmitted(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setPosting(false);
    }
  }, [run]);

  return (
    <Section style={{ borderBottom: 'none' }}>
      <SectionHeader>FEEDBACK</SectionHeader>
      {submitted ? (
        <div style={{
          fontFamily: 'var(--font-body)', fontSize: '12px',
          color: 'var(--state-ok)', display: 'flex', alignItems: 'center', gap: '6px',
        }}>
          <span>✓</span>
          <span>
            Submitted: <strong>{CATS.find(c => c.key === selected)?.label}</strong>
          </span>
        </div>
      ) : (
        <>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            {CATS.map(({ key, label }) => (
              <button
                key={key}
                disabled={posting}
                onClick={() => submit(key)}
                style={{
                  fontFamily: 'var(--font-mono)', fontSize: '10px',
                  color: 'var(--text-secondary)',
                  background: 'var(--bg-tertiary)',
                  border: '1px solid var(--rule)',
                  padding: '5px 12px', cursor: posting ? 'wait' : 'pointer',
                  transition: 'border-color 150ms',
                }}
                onMouseEnter={e => { if (!posting) e.currentTarget.style.borderColor = 'var(--rule-accent)'; }}
                onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--rule)'; }}
              >
                {label}
              </button>
            ))}
          </div>
          {error && (
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--state-error)', marginTop: '6px' }}>
              Error: {error}
            </div>
          )}
        </>
      )}
    </Section>
  );
}

/* ─── main drawer ───────────────────────────────────────────────────────────── */

export function EventDrawer() {
  const { selectedRunId, drawerOpen, setDrawerOpen } = useDashboard();
  const { data: rawResponse, isLoading } = useRun(selectedRunId);

  const run = rawResponse?.data?.[0] ?? null;

  useEffect(() => {
    if (!drawerOpen) return;
    const handler = e => { if (e.key === 'Escape') setDrawerOpen(false); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [drawerOpen, setDrawerOpen]);

  const close = useCallback(() => setDrawerOpen(false), [setDrawerOpen]);

  const stages   = buildStages(run);
  const superSup = stageSuperscripts(run);

  const VERDICT_S = {
    CHANGE_REQUIRED: 'var(--state-alert)',
    UNCERTAIN:       'var(--state-warn)',
    NO_CHANGE:       'var(--state-ok)',
  };

  return (
    <>
      {/* Backdrop */}
      {drawerOpen && (
        <div
          style={{
            position: 'fixed', inset: 0,
            background: 'rgba(0,0,0,0.4)',
            zIndex: 199,
          }}
          onClick={close}
        />
      )}

      {/* Drawer panel */}
      <div
        role="dialog"
        aria-modal="true"
        style={{
          position: 'fixed',
          top: 0, right: 0,
          width: '640px', height: '100vh',
          background: 'var(--bg-primary)',
          borderLeft: '1px solid var(--rule-accent)',
          zIndex: 200,
          transform: drawerOpen ? 'translateX(0)' : 'translateX(100%)',
          transition: 'transform 250ms ease-out',
          display: 'flex', flexDirection: 'column',
          overflowY: 'hidden',
        }}
      >
        {/* Header strip */}
        <div style={{
          flexShrink: 0, display: 'flex', alignItems: 'flex-start',
          padding: '12px 16px', borderBottom: '1px solid var(--rule)',
          gap: '8px',
        }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            {isLoading && !run ? (
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--text-tertiary)' }}>
                Loading…
              </span>
            ) : run ? (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 16px' }}>
                {[
                  { label: 'RUN',    val: run.run_id },
                  { label: 'SOURCE', val: run.source_id },
                  { label: 'PAGE',   val: run.ipfr_page_id },
                  { label: 'TIME',   val: formatRelativeTime(run.timestamp ?? run.run_at) },
                ].map(({ label, val }) => (
                  <div key={label} style={{ display: 'flex', gap: '4px', alignItems: 'baseline' }}>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: '9px', color: 'var(--text-tertiary)', letterSpacing: '0.08em' }}>
                      {label}
                    </span>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--text-secondary)' }}>
                      {val ?? '—'}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--text-tertiary)' }}>
                No run selected
              </span>
            )}
          </div>
          <button
            onClick={close}
            style={{
              flexShrink: 0, background: 'none', border: 'none',
              color: 'var(--text-tertiary)', cursor: 'pointer',
              fontFamily: 'var(--font-mono)', fontSize: '14px', lineHeight: 1,
              padding: '0 2px',
            }}
            onMouseEnter={e => { e.currentTarget.style.color = 'var(--text-secondary)'; }}
            onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-tertiary)'; }}
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        {/* Scrollable body */}
        <div style={{ flex: 1, overflowY: 'auto' }}>

          {/* Stage Journey */}
          {run && (
            <Section>
              <SectionHeader>STAGE JOURNEY</SectionHeader>
              <div style={{ display: 'flex', gap: '4px', alignItems: 'center' }}>
                {stages.map(({ n, reached, status }) => {
                  const sup = superSup[n];
                  return (
                    <div key={n} style={{ position: 'relative', flexShrink: 0 }}>
                      <StageIndicator stages={[{ n, reached, status }]} size={1} />
                      {sup && (
                        <span style={{
                          position: 'absolute', top: -4, right: -4,
                          fontFamily: 'var(--font-mono)', fontSize: '7px',
                          color: 'var(--text-tertiary)', lineHeight: 1,
                          background: 'var(--bg-primary)',
                        }}>
                          {sup}
                        </span>
                      )}
                    </div>
                  );
                })}
                <div style={{ marginLeft: '8px', display: 'flex', gap: '10px' }}>
                  {[
                    { sym: 'F', label: 'fast-pass' },
                    { sym: 'G', label: 'graph-propagated' },
                    { sym: 'H', label: 'high-significance' },
                  ].map(({ sym, label }) => (
                    <span key={sym} style={{ fontFamily: 'var(--font-mono)', fontSize: '9px', color: 'var(--text-tertiary)' }}>
                      <sup>{sym}</sup> {label}
                    </span>
                  ))}
                </div>
              </div>
            </Section>
          )}

          {/* Graph Trace */}
          {run && <GraphTrace run={run} />}

          {/* Score Panel */}
          {run && <ScorePanel run={run} />}

          {/* Diff Preview */}
          {run?.diff_text && <DiffPreview diffText={run.diff_text} />}

          {/* LLM Assessment */}
          {run && <LLMAssessment run={run} />}

          {/* Feedback */}
          {run && <FeedbackSection run={run} />}

          {/* Empty state */}
          {!isLoading && !run && selectedRunId && (
            <div style={{ padding: '24px 16px', fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--text-tertiary)' }}>
              Run {selectedRunId} not found.
            </div>
          )}
        </div>
      </div>
    </>
  );
}
