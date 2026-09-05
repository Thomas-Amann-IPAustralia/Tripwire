import React, { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useSources, useRuns, useSnapshot } from '../hooks/useData.js';
import { formatRelativeTime } from '../lib/dataUtils.js';
import SnapshotOverlay from '../visualisations/SnapshotOverlay.jsx';
import ErrorBanner from '../components/ErrorBanner.jsx';

// ── Barcode SVG: vertical bars representing check frequency cadence ──────────
const CADENCE_BARS = {
  daily: 20, weekly: 7, fortnightly: 4, monthly: 2, quarterly: 1,
};

function BarcodeColumn({ frequency }) {
  const count = CADENCE_BARS[frequency] ?? 1;
  const width = 40;
  const height = 28;
  // Distribute bars evenly across width
  const gap = width / count;
  const barW = Math.max(1, gap * 0.55);

  return (
    <svg width={width} height={height} style={{ display: 'block', flexShrink: 0 }}>
      {Array.from({ length: count }, (_, i) => (
        <rect
          key={i}
          x={gap * i + (gap - barW) / 2}
          y={2}
          width={barW}
          height={height - 4}
          fill="var(--stage-4)"
          opacity={0.6}
        />
      ))}
    </svg>
  );
}

// ── Mini sparkline: last 90 days of check history ────────────────────────────
const OUTCOME_COLOR = { completed: '#3a6b3a', error: '#8b1a1a', no_change: '#5c5a52' };

function SparkLine({ history = [] }) {
  const w = 80, h = 24;
  if (!history.length) {
    return <svg width={w} height={h}><rect width={w} height={h} fill="none" /></svg>;
  }
  const n = history.length;
  const slotW = Math.max(1, w / n);
  return (
    <svg width={w} height={h} style={{ display: 'block' }}>
      {history.map((item, i) => (
        <rect
          key={i}
          x={slotW * i}
          y={0}
          width={Math.max(1, slotW - 1)}
          height={h}
          fill={OUTCOME_COLOR[item.outcome] ?? '#5c5a52'}
          opacity={0.85}
        />
      ))}
    </svg>
  );
}

// ── Type pill ────────────────────────────────────────────────────────────────
const TYPE_COLOR = { webpage: 'var(--stage-4)', frl: 'var(--stage-3)', rss: 'var(--stage-1)' };

function TypePill({ type }) {
  return (
    <span style={{
      fontFamily: 'var(--font-mono)', fontSize: '9px', letterSpacing: '0.05em',
      color: TYPE_COLOR[type] ?? 'var(--text-tertiary)',
      border: `1px solid ${TYPE_COLOR[type] ?? 'var(--text-tertiary)'}`,
      padding: '1px 5px', borderRadius: '2px',
      textTransform: 'uppercase',
    }}>
      {type ?? '—'}
    </span>
  );
}

// ── Importance bar (48px wide) ───────────────────────────────────────────────
function ImportanceBar({ value }) {
  const pct = Math.max(0, Math.min(1, value ?? 0)) * 100;
  return (
    <div style={{ width: 48, height: 6, background: 'var(--bg-accent)' }}>
      <div style={{ width: `${pct}%`, height: '100%', background: 'var(--stage-4)' }} />
    </div>
  );
}

// ── Status dot ───────────────────────────────────────────────────────────────
function StatusDot({ failures }) {
  const color = failures >= 2 ? 'var(--state-error)'
    : failures === 1 ? 'var(--state-warn)'
    : 'var(--state-ok)';
  return (
    <span style={{
      display: 'inline-block', width: 8, height: 8, borderRadius: '50%',
      background: color, flexShrink: 0,
    }} />
  );
}

// ── Expanded row: run records + snapshot ─────────────────────────────────────
function SourceExpandedDetail({ sourceId }) {
  const { data: runs = [] } = useRuns({ sources: [sourceId] });
  const { data: snapshotData, isLoading: snapLoading } = useSnapshot(sourceId);
  const hasSnapshot = !!snapshotData?.data?.snapshot_text;
  const [showSnapshot, setShowSnapshot] = useState(false);

  const last10 = runs.slice(0, 10);

  const thStyle = {
    fontFamily: 'var(--font-mono)', fontSize: '9px', color: 'var(--text-tertiary)',
    letterSpacing: '0.06em', textTransform: 'uppercase',
    padding: '6px 10px', textAlign: 'left', borderBottom: '1px solid var(--rule)',
    fontWeight: 400,
  };
  const tdStyle = {
    fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-secondary)',
    padding: '5px 10px', borderBottom: '1px solid var(--rule)',
  };

  return (
    <td colSpan={11} style={{ background: 'var(--bg-tertiary)', padding: '0' }}>
      <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--rule-accent)' }}>
        {/* Run records table */}
        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: '9px', color: 'var(--text-tertiary)',
          letterSpacing: '0.07em', marginBottom: '8px', textTransform: 'uppercase',
        }}>
          LAST {last10.length} PIPELINE RUNS
        </div>
        {last10.length === 0 ? (
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)', padding: '4px 0 12px' }}>
            NO RUNS RECORDED
          </div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: '12px', background: 'var(--bg-secondary)' }}>
            <thead>
              <tr>
                <th style={thStyle}>RUN ID</th>
                <th style={thStyle}>TIME</th>
                <th style={thStyle}>STAGE</th>
                <th style={thStyle}>OUTCOME</th>
                <th style={thStyle}>VERDICT</th>
              </tr>
            </thead>
            <tbody>
              {last10.map(run => (
                <tr key={run.id}>
                  <td style={tdStyle}>{run.run_id}</td>
                  <td style={tdStyle}>{formatRelativeTime(run.timestamp)}</td>
                  <td style={tdStyle}>S{run.stage_reached ?? '—'}</td>
                  <td style={{ ...tdStyle, color: run.outcome === 'error' ? 'var(--state-error)' : 'var(--text-secondary)' }}>
                    {run.outcome ?? '—'}
                  </td>
                  <td style={{
                    ...tdStyle,
                    color: run.verdict === 'CHANGE_REQUIRED' ? 'var(--state-alert)'
                      : run.verdict === 'UNCERTAIN' ? 'var(--state-warn)'
                      : run.verdict === 'NO_CHANGE' ? 'var(--state-ok)'
                      : 'var(--text-tertiary)',
                  }}>
                    {run.verdict ?? '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {/* Snapshot button */}
        <button
          onClick={() => setShowSnapshot(true)}
          disabled={snapLoading || !hasSnapshot}
          style={{
            fontFamily: 'var(--font-mono)', fontSize: '10px', letterSpacing: '0.07em',
            background: 'none', border: '1px solid var(--rule-accent)',
            color: hasSnapshot ? 'var(--text-secondary)' : 'var(--text-tertiary)',
            padding: '4px 12px', cursor: hasSnapshot ? 'pointer' : 'default',
          }}
        >
          {snapLoading ? 'LOADING SNAPSHOT…' : hasSnapshot ? 'VIEW SNAPSHOT ↗' : 'NO SNAPSHOT YET'}
        </button>
      </div>

      {showSnapshot && hasSnapshot && (
        <SnapshotOverlay data={snapshotData} onClose={() => setShowSnapshot(false)} />
      )}
    </td>
  );
}

// ── Add Source slide-in panel ────────────────────────────────────────────────
const FREQ_OPTIONS = ['daily', 'weekly', 'fortnightly', 'monthly', 'quarterly'];
const TYPE_OPTIONS = ['webpage', 'frl', 'rss'];

const EMPTY_FORM = {
  source_id: '', url: '', title: '', source_type: 'webpage',
  importance: 0.5, check_frequency: 'weekly', notes: '', force_selenium: false,
};

function AddSourcePanel({ open, onClose, onSuccess }) {
  const [form, setForm] = useState(EMPTY_FORM);
  const [errors, setErrors] = useState({});
  const [apiError, setApiError] = useState('');
  const [saving, setSaving] = useState(false);

  const set = (key, val) => {
    setForm(f => ({ ...f, [key]: val }));
    setErrors(e => ({ ...e, [key]: undefined }));
  };

  const validate = () => {
    const errs = {};
    if (!form.source_id.trim()) errs.source_id = 'Required';
    if (!form.url.trim())       errs.url       = 'Required';
    if (!form.title.trim())     errs.title     = 'Required';
    return errs;
  };

  const handleSubmit = async e => {
    e.preventDefault();
    const errs = validate();
    if (Object.keys(errs).length) { setErrors(errs); return; }

    setSaving(true);
    setApiError('');
    try {
      const res = await fetch('/api/sources', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      });
      const json = await res.json();
      if (!res.ok || json.error) throw new Error(json.error || `HTTP ${res.status}`);
      setForm(EMPTY_FORM);
      onSuccess();
    } catch (err) {
      setApiError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const inputStyle = {
    fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--text-primary)',
    background: 'var(--bg-tertiary)', border: '1px solid var(--rule-accent)',
    padding: '6px 8px', width: '100%', outline: 'none', display: 'block',
  };
  const labelStyle = {
    fontFamily: 'var(--font-mono)', fontSize: '9px', color: 'var(--text-tertiary)',
    letterSpacing: '0.07em', textTransform: 'uppercase', display: 'block', marginBottom: '4px',
  };
  const errStyle = {
    fontFamily: 'var(--font-mono)', fontSize: '9px', color: 'var(--state-error)',
    marginTop: '3px',
  };
  const fieldWrap = { marginBottom: '14px' };

  return (
    <>
      {/* Backdrop */}
      {open && (
        <div
          onClick={onClose}
          style={{
            position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', zIndex: 300,
          }}
        />
      )}
      {/* Slide-in panel */}
      <div style={{
        position: 'fixed', top: 0, right: 0, bottom: 0,
        width: '360px',
        background: 'var(--bg-secondary)',
        borderLeft: '1px solid var(--rule-accent)',
        zIndex: 301,
        transform: open ? 'translateX(0)' : 'translateX(100%)',
        transition: 'transform 250ms ease-out',
        display: 'flex', flexDirection: 'column',
        overflow: 'hidden',
      }}>
        {/* Header */}
        <div style={{
          height: '48px', display: 'flex', alignItems: 'center',
          padding: '0 16px', gap: '12px',
          borderBottom: '1px solid var(--rule)',
          flexShrink: 0,
        }}>
          <span style={{ fontFamily: 'var(--font-display)', fontSize: '18px', letterSpacing: '0.06em' }}>
            ADD SOURCE
          </span>
          <div style={{ flex: 1 }} />
          <button onClick={onClose} style={{
            background: 'none', border: '1px solid var(--rule)', cursor: 'pointer',
            color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)', fontSize: '11px',
            padding: '4px 12px',
          }}>
            CANCEL
          </button>
        </div>

        {/* Form body */}
        <form onSubmit={handleSubmit} style={{ flex: 1, overflowY: 'auto', padding: '20px 16px' }}>
          <div style={fieldWrap}>
            <label style={labelStyle}>Source ID *</label>
            <input value={form.source_id} onChange={e => set('source_id', e.target.value)} style={inputStyle} />
            {errors.source_id && <div style={errStyle}>{errors.source_id}</div>}
          </div>

          <div style={fieldWrap}>
            <label style={labelStyle}>URL *</label>
            <input type="url" value={form.url} onChange={e => set('url', e.target.value)} style={inputStyle} />
            {errors.url && <div style={errStyle}>{errors.url}</div>}
          </div>

          <div style={fieldWrap}>
            <label style={labelStyle}>Title *</label>
            <input value={form.title} onChange={e => set('title', e.target.value)} style={inputStyle} />
            {errors.title && <div style={errStyle}>{errors.title}</div>}
          </div>

          <div style={fieldWrap}>
            <label style={labelStyle}>Source Type</label>
            <select value={form.source_type} onChange={e => set('source_type', e.target.value)}
              style={{ ...inputStyle, cursor: 'pointer' }}>
              {TYPE_OPTIONS.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>

          <div style={fieldWrap}>
            <label style={labelStyle}>Importance (0.0 – 1.0)</label>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <input
                type="range" min={0} max={1} step={0.1} value={form.importance}
                onChange={e => set('importance', parseFloat(e.target.value))}
                style={{ flex: 1, accentColor: 'var(--stage-4)' }}
              />
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', color: 'var(--text-primary)', minWidth: '28px' }}>
                {form.importance.toFixed(1)}
              </span>
            </div>
          </div>

          <div style={fieldWrap}>
            <label style={labelStyle}>Check Frequency</label>
            <select value={form.check_frequency} onChange={e => set('check_frequency', e.target.value)}
              style={{ ...inputStyle, cursor: 'pointer' }}>
              {FREQ_OPTIONS.map(f => <option key={f} value={f}>{f}</option>)}
            </select>
          </div>

          <div style={fieldWrap}>
            <label style={labelStyle}>Notes</label>
            <textarea
              value={form.notes}
              onChange={e => set('notes', e.target.value)}
              rows={3}
              style={{ ...inputStyle, resize: 'vertical', lineHeight: '1.5' }}
            />
          </div>

          <div style={{ ...fieldWrap, display: 'flex', alignItems: 'center', gap: '12px' }}>
            <label style={{ ...labelStyle, marginBottom: 0 }}>Force Selenium</label>
            <button
              type="button"
              role="switch"
              aria-checked={form.force_selenium}
              onClick={() => set('force_selenium', !form.force_selenium)}
              style={{
                width: '36px', height: '20px', borderRadius: '10px', border: 'none',
                cursor: 'pointer', outline: 'none',
                background: form.force_selenium ? 'var(--stage-1)' : 'var(--bg-accent)',
                position: 'relative', transition: 'background 150ms', flexShrink: 0,
              }}
            >
              <span style={{
                position: 'absolute', top: '3px',
                left: form.force_selenium ? '19px' : '3px',
                width: '14px', height: '14px', borderRadius: '50%',
                background: 'var(--text-primary)', transition: 'left 150ms', display: 'block',
              }} />
            </button>
          </div>

          {apiError && (
            <div style={{
              fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--state-error)',
              border: '1px solid var(--state-error)', padding: '8px 10px', marginBottom: '12px',
            }}>
              {apiError}
            </div>
          )}

          <button
            type="submit"
            disabled={saving}
            style={{
              width: '100%', padding: '10px',
              fontFamily: 'var(--font-display)', fontSize: '14px', letterSpacing: '0.08em',
              background: saving ? 'var(--bg-accent)' : 'var(--stage-1)',
              color: 'var(--text-primary)', border: 'none', cursor: saving ? 'default' : 'pointer',
            }}
          >
            {saving ? 'SAVING…' : 'SAVE SOURCE'}
          </button>
        </form>
      </div>
    </>
  );
}

// ── Main Sources section ──────────────────────────────────────────────────────
const COL_HEADER = {
  fontFamily: 'var(--font-mono)', fontSize: '9px', color: 'var(--text-tertiary)',
  letterSpacing: '0.06em', textTransform: 'uppercase',
  padding: '6px 8px', textAlign: 'left', borderBottom: '1px solid var(--rule)',
  fontWeight: 400, whiteSpace: 'nowrap',
};

const CELL = {
  padding: '7px 8px', borderBottom: '1px solid var(--rule)',
  verticalAlign: 'middle',
};

export default function Sources() {
  const { data: sources = [], isLoading, error } = useSources();
  const [expandedId, setExpandedId] = useState(null);
  const [addOpen, setAddOpen] = useState(false);
  const queryClient = useQueryClient();

  const toggleRow = id => setExpandedId(cur => cur === id ? null : id);

  const handleAddSuccess = () => {
    queryClient.invalidateQueries({ queryKey: ['sources'] });
    setAddOpen(false);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* ── Section header ── */}
      <div style={{
        height: '80px', minHeight: '80px',
        display: 'flex', alignItems: 'flex-end',
        padding: '0 24px 12px',
        borderBottom: '1px solid var(--rule)',
        gap: '16px',
      }}>
        <div>
          <div style={{ fontFamily: 'var(--font-display)', fontSize: '42px', lineHeight: 1, letterSpacing: '0.04em' }}>
            SOURCES
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--text-secondary)', marginTop: '2px' }}>
            Influencer Source Registry
          </div>
        </div>
        <div style={{ flex: 1 }} />
        <button
          onClick={() => setAddOpen(true)}
          style={{
            fontFamily: 'var(--font-display)', fontSize: '14px', letterSpacing: '0.08em',
            background: 'none', border: '1px solid var(--rule-accent)',
            color: 'var(--text-secondary)', padding: '6px 16px', cursor: 'pointer',
          }}
          onMouseEnter={e => e.currentTarget.style.color = 'var(--text-primary)'}
          onMouseLeave={e => e.currentTarget.style.color = 'var(--text-secondary)'}
        >
          + ADD SOURCE
        </button>
      </div>

      <ErrorBanner error={error} />

      {/* ── Table ── */}
      <div style={{ flex: 1, overflowY: 'auto', overflowX: 'auto' }}>
        {isLoading ? (
          <div style={{
            padding: '24px', fontFamily: 'var(--font-mono)', fontSize: '11px',
            color: 'var(--text-tertiary)',
          }}>
            LOADING SOURCES…
          </div>
        ) : (
          <table style={{
            width: '100%', borderCollapse: 'collapse', tableLayout: 'auto',
            background: 'var(--bg-primary)',
          }}>
            <thead style={{ position: 'sticky', top: 0, background: 'var(--bg-secondary)', zIndex: 10 }}>
              <tr>
                <th style={{ ...COL_HEADER, width: 40 }} />
                <th style={COL_HEADER}>ID</th>
                <th style={COL_HEADER}>TITLE</th>
                <th style={COL_HEADER}>TYPE</th>
                <th style={{ ...COL_HEADER, width: 60 }}>IMPORTANCE</th>
                <th style={COL_HEADER}>FREQUENCY</th>
                <th style={COL_HEADER}>LAST CHECKED</th>
                <th style={COL_HEADER}>LAST CHANGED</th>
                <th style={COL_HEADER}>FAILURES</th>
                <th style={{ ...COL_HEADER, width: 90 }}>HISTORY</th>
                <th style={{ ...COL_HEADER, width: 20 }} />
              </tr>
            </thead>
            <tbody>
              {sources.map(src => {
                const isExpanded = expandedId === src.source_id;
                const failures = src.consecutive_failures ?? 0;
                return (
                  <React.Fragment key={src.source_id}>
                    <tr
                      onClick={() => toggleRow(src.source_id)}
                      style={{ cursor: 'pointer' }}
                      onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-secondary)'}
                      onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                    >
                      {/* Barcode */}
                      <td style={{ ...CELL, padding: '6px 4px', width: 40 }}>
                        <BarcodeColumn frequency={src.check_frequency} />
                      </td>

                      {/* source_id */}
                      <td style={{ ...CELL, fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
                        {src.source_id}
                      </td>

                      {/* Title */}
                      <td style={{
                        ...CELL,
                        fontFamily: 'var(--font-body)', fontSize: '12px', color: 'var(--text-primary)',
                        maxWidth: '220px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                      }}>
                        {src.title}
                      </td>

                      {/* Type */}
                      <td style={CELL}>
                        <TypePill type={src.source_type} />
                      </td>

                      {/* Importance */}
                      <td style={CELL}>
                        <ImportanceBar value={src.importance} />
                      </td>

                      {/* Check frequency */}
                      <td style={{ ...CELL, fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
                        {src.check_frequency ?? '—'}
                      </td>

                      {/* Last checked */}
                      <td style={{ ...CELL, fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}>
                        {src.last_checked ? formatRelativeTime(src.last_checked) : '—'}
                      </td>

                      {/* Last changed */}
                      <td style={{ ...CELL, fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}>
                        {src.last_changed ? formatRelativeTime(src.last_changed) : '—'}
                      </td>

                      {/* Consecutive failures */}
                      <td style={{
                        ...CELL,
                        fontFamily: 'var(--font-mono)', fontSize: '10px',
                        color: failures >= 2 ? 'var(--state-error)' : 'var(--text-secondary)',
                        textAlign: 'center',
                      }}>
                        {failures}
                      </td>

                      {/* Check history sparkline */}
                      <td style={{ ...CELL, padding: '4px 8px' }}>
                        <SparkLine history={src.check_history ?? []} />
                      </td>

                      {/* Status dot */}
                      <td style={{ ...CELL, textAlign: 'center' }}>
                        <StatusDot failures={failures} />
                      </td>
                    </tr>

                    {/* Expanded detail row */}
                    {isExpanded && (
                      <tr>
                        <SourceExpandedDetail sourceId={src.source_id} />
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
              {!sources.length && (
                <tr>
                  <td colSpan={11} style={{
                    padding: '24px', fontFamily: 'var(--font-mono)', fontSize: '11px',
                    color: 'var(--text-tertiary)', textAlign: 'center',
                  }}>
                    NO SOURCES FOUND
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </div>

      <AddSourcePanel
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onSuccess={handleAddSuccess}
      />
    </div>
  );
}
