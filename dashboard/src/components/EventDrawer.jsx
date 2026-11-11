import React, { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useDashboard } from '../App.jsx';
import { useRun, usePageTitles } from '../hooks/useData.js';
import { StageIndicator } from './StageIndicator.jsx';
import { formatRelativeTime, formatScore } from '../lib/dataUtils.js';
import { Icon, Badge, VerdictBadge, Meter, Loading, Callout } from './ui.jsx';

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

/* ─── sub-components ────────────────────────────────────────────────────────── */

function SectionHeader({ children }) {
  return <h3 style={{ fontSize: '13px', fontWeight: 650, color: 'var(--ink)', marginBottom: '10px' }}>{children}</h3>;
}

function Section({ children, style }) {
  return (
    <div style={{ borderBottom: '1px solid var(--line-soft)', padding: '18px 24px', ...style }}>
      {children}
    </div>
  );
}

function GraphTrace({ run }) {
  const pageTitles = usePageTitles();
  if (!run?.graph_propagated) return null;
  const pages = run?.details?.stages?.crossencoder?.scored_pages;
  if (!pages?.length) return null;

  const nodes = [
    { id: run.source_id ?? 'source', label: (run.source_id ?? 'source').slice(0, 18), score: null },
    ...pages.slice(0, 3).map(p => ({
      id:    p.page_id ?? '?',
      label: (pageTitles.get(p.page_id) ?? p.page_id ?? '?').slice(0, 18),
      score: p.crossencoder_score ?? p.reranked_score ?? null,
    })),
  ];

  const NODE_W = 120;
  const NODE_H = 28;
  const GAP    = 56;
  const svgW   = nodes.length * NODE_W + (nodes.length - 1) * GAP;
  const svgH   = NODE_H + 30;
  const arrowId = 'drawer-arrow';

  return (
    <Section>
      <SectionHeader>Graph propagation trace</SectionHeader>
      <div style={{ overflowX: 'auto' }}>
        <svg width={svgW} height={svgH} style={{ display: 'block' }}>
          <defs>
            <marker id={arrowId} markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
              <path d="M0,0 L6,3 L0,6" fill="none" stroke="var(--muted)" strokeWidth={1.2} />
            </marker>
          </defs>
          {nodes.map((node, i) => {
            const x = i * (NODE_W + GAP);
            const nextScore = nodes[i + 1]?.score;
            const opacity   = nextScore != null ? Math.min(1, Math.max(0.2, nextScore + 0.2)) : 1;
            return (
              <g key={node.id}>
                <title>{pageTitles.get(node.id) ?? node.id}</title>
                <rect
                  x={x} y={4} width={NODE_W} height={NODE_H}
                  rx={7}
                  fill={i === 0 ? 'var(--accent-soft)' : 'var(--surface-subtle)'}
                  stroke={i === 0 ? 'var(--accent-line)' : 'var(--line-strong)'} strokeWidth={1}
                />
                <text
                  x={x + NODE_W / 2} y={4 + NODE_H / 2 + 3}
                  textAnchor="middle"
                  style={{ fontFamily: 'var(--font-sans)', fontSize: '10.5px', fill: 'var(--ink)' }}
                >
                  {node.label}
                </text>
                {i < nodes.length - 1 && (
                  <>
                    <line
                      x1={x + NODE_W} y1={4 + NODE_H / 2}
                      x2={x + NODE_W + GAP - 2} y2={4 + NODE_H / 2}
                      stroke="var(--muted)" strokeWidth={1.2}
                      opacity={opacity}
                      markerEnd={`url(#${arrowId})`}
                    />
                    {nextScore != null && (
                      <text
                        x={x + NODE_W + GAP / 2} y={4 + NODE_H / 2 - 4}
                        textAnchor="middle"
                        style={{ fontFamily: 'var(--font-sans)', fontSize: '10.5px', fontWeight: 600, fill: 'var(--muted)' }}
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
    { signal: 'RRF score',          raw: run?.scores?.rrf_score,       threshold: null  },
    { signal: 'Source importance',  raw: run?.scores?.source_importance, threshold: null },
    { signal: 'Bi-encoder maximum', raw: run?.biencoder_max,            threshold: 0.4  },
    { signal: 'Cross-encoder',      raw: run?.crossencoder_score,       threshold: 0.5  },
    { signal: 'Reranked score',     raw: run?.reranked_score,           threshold: null  },
  ].filter(r => r.raw != null);

  if (!rows.length) return null;

  return (
    <Section>
      <SectionHeader>Scores</SectionHeader>
      <div style={{ border: '1px solid var(--line)', borderRadius: 'var(--radius)', overflow: 'hidden' }}>
        <table className="tw-table is-compact">
          <thead>
            <tr>
              {['Signal', 'Score', 'Threshold', 'Result'].map(h => <th key={h}>{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map(({ signal, raw, threshold }) => {
              const pass = threshold != null ? Number(raw) >= threshold : null;
              return (
                <tr key={signal}>
                  <td className="tw-cell-strong">{signal}</td>
                  <td className="tabular">{formatScore(raw)}</td>
                  <td className="tabular">{threshold != null ? threshold.toFixed(2) : '—'}</td>
                  <td>
                    {pass === null ? <span className="tw-faint">—</span>
                      : pass ? <Badge tone="ok" dot>Pass</Badge>
                      : <Badge tone="alert" dot>Below threshold</Badge>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Section>
  );
}

function DiffPreview({ diffText }) {
  if (!diffText) return null;
  const lines = diffText.split('\n');
  return (
    <Section>
      <SectionHeader>What changed</SectionHeader>
      <pre style={{
        maxHeight: '320px', overflowY: 'auto',
        fontFamily: 'var(--font-code)', fontSize: '11.5px',
        color: 'var(--body)', margin: 0, padding: '8px 0',
        background: 'var(--surface-subtle)', border: '1px solid var(--line)', borderRadius: 'var(--radius)',
        lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
      }}>
        {lines.map((line, i) => {
          let bg = 'transparent';
          let color;
          if (line.startsWith('+')) { bg = 'var(--ok-soft)'; color = 'var(--ok)'; }
          if (line.startsWith('-')) { bg = 'var(--alert-soft)'; color = 'var(--alert)'; }
          return (
            <span key={i} style={{ display: 'block', background: bg, color, padding: '0 12px' }}>
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

  return (
    <Section>
      <SectionHeader>Language model assessment</SectionHeader>
      <div style={{ display: 'flex', alignItems: 'center', gap: '16px', marginBottom: '12px', flexWrap: 'wrap' }}>
        <VerdictBadge verdict={v} large />
        {conf != null && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span className="tw-small tw-muted">Confidence</span>
            <Meter value={conf} style={{ width: '110px' }} label="Model confidence" />
            <span className="tabular" style={{ fontSize: '12px', fontWeight: 650 }}>{conf.toFixed(2)}</span>
          </div>
        )}
      </div>

      {reason && (
        <p style={{ fontSize: '13.5px', color: 'var(--body)', lineHeight: 1.65, margin: '0 0 12px' }}>
          {reason}
        </p>
      )}

      {sug && sug.length > 0 && (
        <>
          <div style={{ fontSize: '12px', fontWeight: 650, color: 'var(--alert)', marginBottom: '6px' }}>
            Suggested changes
          </div>
          <ol style={{ margin: 0, paddingLeft: '20px' }}>
            {sug.map((s, i) => (
              <li key={i} style={{ fontSize: '13px', color: 'var(--body)', lineHeight: 1.55, marginBottom: '6px' }}>
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
    { key: 'not_significant', label: 'Not significant' },
    { key: 'wrong_amendment', label: 'Wrong amendment' },
    { key: 'wrong_page',      label: 'Wrong page'      },
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
      <SectionHeader>Was this alert helpful?</SectionHeader>
      {submitted ? (
        <Callout tone="ok" icon="check">
          Thanks, feedback recorded: <strong>{CATS.find(c => c.key === selected)?.label}</strong>
        </Callout>
      ) : (
        <>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            {CATS.map(({ key, label }) => (
              <button
                key={key}
                type="button"
                className={`tw-btn is-sm${key === 'useful' ? ' is-primary' : ''}`}
                disabled={posting}
                onClick={() => submit(key)}
                style={{ cursor: posting ? 'wait' : undefined }}
              >
                {key === 'useful' && <Icon name="check" />}
                {label}
              </button>
            ))}
          </div>
          {error && (
            <Callout tone="error" icon="alert" style={{ marginTop: '10px' }}>Could not save feedback: {error}</Callout>
          )}
        </>
      )}
    </Section>
  );
}

/* ─── main drawer ───────────────────────────────────────────────────────────── */

export function EventDrawer() {
  const { selectedRunId, selectedRowId, drawerOpen, setDrawerOpen } = useDashboard();
  const { data: rawResponse, isLoading } = useRun(selectedRunId);
  const pageTitles = usePageTitles();
  const navTo = useNavigate();

  // A run_id groups one row per source checked in that pipeline run — show
  // the row the user actually clicked, not an arbitrary first row.
  const rows = rawResponse?.data ?? [];
  const run = (selectedRowId != null && rows.find(r => r.id === selectedRowId)) || rows[0] || null;

  const handleViewInGraph = useCallback(() => {
    if (!run?.ipfr_page_id) return;
    const pageId = run.ipfr_page_id;
    setDrawerOpen(false);
    navTo('/corpus');
    setTimeout(() => {
      window.dispatchEvent(
        new CustomEvent('tripwire:highlight-graph-node', { detail: pageId })
      );
    }, 150);
  }, [run, navTo, setDrawerOpen]);

  useEffect(() => {
    if (!drawerOpen) return;
    const handler = e => { if (e.key === 'Escape') setDrawerOpen(false); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [drawerOpen, setDrawerOpen]);

  const close = useCallback(() => setDrawerOpen(false), [setDrawerOpen]);

  const stages   = buildStages(run);
  const superSup = stageSuperscripts(run);

  return (
    <>
      {drawerOpen && <div className="tw-scrim" style={{ zIndex: 199 }} onClick={close} />}

      <div
        role="dialog"
        aria-modal="true"
        aria-label="Run details"
        className="tw-drawer"
        style={{
          width: 'min(660px, 100vw)',
          zIndex: 200,
          transform: drawerOpen ? 'translateX(0)' : 'translateX(105%)',
          transition: 'transform 250ms ease-out',
          visibility: drawerOpen ? 'visible' : 'hidden',
        }}
      >
        <div className="tw-drawer-head">
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: '12px' }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="tw-overline" style={{ marginBottom: '6px' }}>Source check</div>
              {isLoading && !run ? (
                <Loading style={{ padding: 0 }} />
              ) : run ? (
                <>
                  <div className="tw-drawer-title">
                    {run.ipfr_page_id ? (pageTitles.get(run.ipfr_page_id) ?? run.ipfr_page_id) : (run.source_id ?? 'Run details')}
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 18px', marginTop: '10px', fontSize: '12px', color: 'var(--muted)' }}>
                    {[
                      { label: 'Run',    val: run.run_id, code: true },
                      { label: 'Source', val: run.source_id, code: true },
                      { label: 'When',   val: formatRelativeTime(run.timestamp ?? run.run_at) },
                    ].map(({ label, val, code }) => (
                      <span key={label}>
                        {label}{' '}
                        <span className={code ? 'tw-code' : undefined} style={{ color: 'var(--ink)', fontWeight: code ? 400 : 600, fontSize: code ? '11.5px' : undefined }}>
                          {val ?? '—'}
                        </span>
                      </span>
                    ))}
                  </div>
                </>
              ) : (
                <div className="tw-drawer-title">No run selected</div>
              )}
            </div>
            <button type="button" className="tw-icon-btn" onClick={close} aria-label="Close" style={{ background: 'var(--surface)' }}>
              <Icon name="close" />
            </button>
          </div>
        </div>

        <div style={{ flex: 1, overflowY: 'auto' }}>

          {run && (
            <Section>
              <SectionHeader>Stage journey</SectionHeader>
              <div style={{ display: 'flex', gap: '6px', alignItems: 'center', flexWrap: 'wrap' }}>
                {stages.map(({ n, reached, status }) => {
                  const sup = superSup[n];
                  return (
                    <div key={n} title={`Stage ${n}: ${reached ? status : 'not reached'}`} style={{
                      position: 'relative', flexShrink: 0,
                      display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '4px',
                      padding: '6px 8px', borderRadius: '8px',
                      background: reached ? 'var(--surface-subtle)' : 'transparent',
                      border: `1px solid ${reached ? 'var(--line)' : 'var(--line-soft)'}`,
                    }}>
                      <StageIndicator stages={[{ n, reached, status }]} size={1.25} />
                      <span style={{ fontSize: '10.5px', fontWeight: 600, color: reached ? 'var(--body)' : 'var(--faint)' }}>{n}</span>
                      {sup && (
                        <span style={{
                          position: 'absolute', top: -7, right: -6,
                          fontSize: '9.5px', fontWeight: 700, lineHeight: 1,
                          color: 'var(--accent-ink)', background: 'var(--accent-soft)',
                          border: '1px solid var(--accent-line)', borderRadius: '99px', padding: '2px 4px',
                        }}>
                          {sup}
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
              <div style={{ display: 'flex', gap: '14px', marginTop: '10px', flexWrap: 'wrap' }}>
                {[
                  { sym: 'F', label: 'Fast pass' },
                  { sym: 'G', label: 'Graph propagated' },
                  { sym: 'H', label: 'High significance' },
                ].map(({ sym, label }) => (
                  <span key={sym} style={{ fontSize: '11.5px', color: 'var(--muted)' }}>
                    <strong style={{ color: 'var(--accent-ink)' }}>{sym}</strong> {label}
                  </span>
                ))}
              </div>
            </Section>
          )}

          {run?.ipfr_page_id && (
            <Section>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px' }}>
                <div style={{ minWidth: 0 }}>
                  <div className="tw-small tw-muted" style={{ marginBottom: '2px' }}>Affected IPFR page</div>
                  <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--ink)' }}>
                    {pageTitles.get(run.ipfr_page_id) ?? run.ipfr_page_id}
                  </div>
                  {pageTitles.has(run.ipfr_page_id) && (
                    <div className="tw-code" style={{ fontSize: '11px', color: 'var(--faint)' }}>{run.ipfr_page_id}</div>
                  )}
                </div>
                <button type="button" className="tw-btn is-sm" onClick={handleViewInGraph}>
                  View in knowledge graph <Icon name="arrow" />
                </button>
              </div>
            </Section>
          )}

          {run && <GraphTrace run={run} />}
          {run && <ScorePanel run={run} />}
          {run?.diff_text && <DiffPreview diffText={run.diff_text} />}
          {run && <LLMAssessment run={run} />}
          {run && <FeedbackSection run={run} />}

          {!isLoading && !run && selectedRunId && (
            <div style={{ padding: '24px' }}>
              <Callout tone="warn" icon="help">Run <span className="tw-code">{selectedRunId}</span> was not found.</Callout>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
