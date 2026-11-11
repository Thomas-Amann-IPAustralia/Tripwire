import React, { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useSources, useRuns, useSnapshot } from '../hooks/useData.js';
import { formatRelativeTime } from '../lib/dataUtils.js';
import SnapshotOverlay from '../visualisations/SnapshotOverlay.jsx';
import ErrorBanner from '../components/ErrorBanner.jsx';
import { PageHeader, Card, Badge, OutcomeBadge, VerdictBadge, Caret, Meter, Icon, Loading, EmptyState, Callout, SearchInput, MetricCard } from '../components/ui.jsx';

// ── Barcode SVG: vertical bars representing check frequency cadence ──────────
const CADENCE_BARS = {
  daily: 20, weekly: 7, fortnightly: 4, monthly: 2, quarterly: 1,
};

function BarcodeColumn({ frequency }) {
  const count = CADENCE_BARS[frequency] ?? 1;
  const width = 26;
  const height = 16;
  // Distribute bars evenly across width
  const gap = width / count;
  const barW = Math.max(1, gap * 0.55);

  return (
    <svg width={width} height={height} style={{ display: 'block', flexShrink: 0 }}>
      {Array.from({ length: count }, (_, i) => (
        <rect
          key={i}
          x={gap * i + (gap - barW) / 2}
          y={0}
          width={barW}
          height={height}
          rx={1}
          fill="var(--accent)"
          opacity={0.55}
        />
      ))}
    </svg>
  );
}

// ── Mini sparkline: last 90 days of check history ────────────────────────────
const OUTCOME_COLOR = { completed: 'var(--ok)', error: 'var(--error)', no_change: 'var(--line-strong)' };

function SparkLine({ history = [] }) {
  const w = 72, h = 16;
  if (!history.length) {
    return <span className="tw-faint tw-small">No history</span>;
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
          rx={1}
          fill={OUTCOME_COLOR[item.outcome] ?? 'var(--line-strong)'}
          opacity={0.9}
        />
      ))}
    </svg>
  );
}

// ── Type badge ───────────────────────────────────────────────────────────────
const TYPE_LABEL = { webpage: 'Web page', frl: 'Legislation', rss: 'RSS feed' };
const TYPE_TONE  = { webpage: 'info', frl: 'accent', rss: 'warn' };

function TypePill({ type }) {
  return <Badge tone={TYPE_TONE[type] ?? 'muted'}>{TYPE_LABEL[type] ?? type ?? '—'}</Badge>;
}

// ── Importance meter ─────────────────────────────────────────────────────────
function ImportanceBar({ value }) {
  const v = Math.max(0, Math.min(1, value ?? 0));
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }} title={`Importance ${v.toFixed(2)}`}>
      <Meter value={v} style={{ width: 52 }} label="Importance" />
      <span className="tabular tw-small tw-muted">{v.toFixed(1)}</span>
    </div>
  );
}

// ── Status dot ───────────────────────────────────────────────────────────────
function StatusDot({ failures }) {
  if (failures >= 2) return <Badge tone="error" dot>Failing</Badge>;
  if (failures === 1) return <Badge tone="warn" dot>1 failure</Badge>;
  return <Badge tone="ok" dot>Healthy</Badge>;
}

// ── Expanded row: run records + snapshot ─────────────────────────────────────
function SourceExpandedDetail({ sourceId }) {
  const { data: runs = [] } = useRuns({ sources: [sourceId] });
  const { data: snapshotData, isLoading: snapLoading } = useSnapshot(sourceId);
  const hasSnapshot = !!snapshotData?.data?.snapshot_text;
  const [showSnapshot, setShowSnapshot] = useState(false);

  const last10 = runs.slice(0, 10);

  return (
    <td colSpan={10} style={{ background: "var(--surface-subtle)", padding: 0 }}>
      <div style={{ padding: '16px 20px 18px', borderBottom: '1px solid var(--line)' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px', marginBottom: '10px' }}>
          <h3 style={{ fontSize: '13px' }}>Last {last10.length} pipeline run{last10.length !== 1 ? 's' : ''}</h3>
          <button
            type="button"
            className="tw-btn is-sm"
            onClick={() => setShowSnapshot(true)}
            disabled={snapLoading || !hasSnapshot}
          >
            <Icon name="file" />
            {snapLoading ? 'Loading snapshot…' : hasSnapshot ? 'View snapshot' : 'No snapshot yet'}
          </button>
        </div>
        {last10.length === 0 ? (
          <div className="tw-small tw-muted">No runs recorded for this source.</div>
        ) : (
          <div style={{ border: '1px solid var(--line)', borderRadius: 'var(--radius)', overflow: 'hidden', background: 'var(--surface)' }}>
            <table className="tw-table is-compact">
              <thead>
                <tr>
                  <th>Run ID</th>
                  <th>When</th>
                  <th>Stage reached</th>
                  <th>Outcome</th>
                  <th>Verdict</th>
                </tr>
              </thead>
              <tbody>
                {last10.map(run => (
                  <tr key={run.id}>
                    <td><span className="tw-code" style={{ fontSize: '11px' }}>{run.run_id}</span></td>
                    <td>{formatRelativeTime(run.timestamp)}</td>
                    <td className="tabular">{run.stage_reached ?? '—'}</td>
                    <td><OutcomeBadge outcome={run.outcome} /></td>
                    <td>{run.verdict ? <VerdictBadge verdict={run.verdict} /> : <span className="tw-faint">—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
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

  const errStyle = { fontSize: '11.5px', color: 'var(--error)', marginTop: '4px' };
  const fieldWrap = { marginBottom: '16px' };

  return (
    <>
      {open && <div className="tw-scrim" onClick={onClose} />}
      <div
        className="tw-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="Add a source"
        style={{
          width: 'min(420px, 100vw)',
          transform: open ? 'translateX(0)' : 'translateX(105%)',
          transition: 'transform 250ms ease-out',
          visibility: open ? 'visible' : 'hidden',
        }}
      >
        <div className="tw-drawer-head" style={{ display: 'flex', alignItems: 'flex-start', gap: '12px' }}>
          <div style={{ flex: 1 }}>
            <div className="tw-overline" style={{ marginBottom: '6px' }}>Source registry</div>
            <div className="tw-drawer-title">Add a source</div>
            <p className="tw-small tw-muted" style={{ marginTop: '6px' }}>New sources are checked from the next scheduled pipeline run.</p>
          </div>
          <button type="button" className="tw-icon-btn" onClick={onClose} aria-label="Close" style={{ background: 'var(--surface)' }}>
            <Icon name="close" />
          </button>
        </div>

        <form onSubmit={handleSubmit} style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <div className="tw-drawer-body">
            <div style={fieldWrap}>
              <label className="tw-label" htmlFor="src-id">Source ID <span style={{ color: 'var(--alert)' }}>*</span></label>
              <input id="src-id" className="tw-input" value={form.source_id} onChange={e => set('source_id', e.target.value)} aria-invalid={!!errors.source_id} />
              {errors.source_id && <div style={errStyle}>{errors.source_id}</div>}
            </div>

            <div style={fieldWrap}>
              <label className="tw-label" htmlFor="src-url">URL <span style={{ color: 'var(--alert)' }}>*</span></label>
              <input id="src-url" className="tw-input" type="url" value={form.url} onChange={e => set('url', e.target.value)} aria-invalid={!!errors.url} />
              {errors.url && <div style={errStyle}>{errors.url}</div>}
            </div>

            <div style={fieldWrap}>
              <label className="tw-label" htmlFor="src-title">Title <span style={{ color: 'var(--alert)' }}>*</span></label>
              <input id="src-title" className="tw-input" value={form.title} onChange={e => set('title', e.target.value)} aria-invalid={!!errors.title} />
              {errors.title && <div style={errStyle}>{errors.title}</div>}
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
              <div style={fieldWrap}>
                <label className="tw-label" htmlFor="src-type">Source type</label>
                <select id="src-type" className="tw-select" value={form.source_type} onChange={e => set('source_type', e.target.value)}>
                  {TYPE_OPTIONS.map(t => <option key={t} value={t}>{TYPE_LABEL[t] ?? t}</option>)}
                </select>
              </div>
              <div style={fieldWrap}>
                <label className="tw-label" htmlFor="src-freq">Check frequency</label>
                <select id="src-freq" className="tw-select" value={form.check_frequency} onChange={e => set('check_frequency', e.target.value)}>
                  {FREQ_OPTIONS.map(f => <option key={f} value={f}>{f.charAt(0).toUpperCase() + f.slice(1)}</option>)}
                </select>
              </div>
            </div>

            <div style={fieldWrap}>
              <label className="tw-label" htmlFor="src-imp">Importance <span className="tw-muted" style={{ fontWeight: 400 }}>(0.0 to 1.0)</span></label>
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                <input
                  id="src-imp"
                  type="range" min={0} max={1} step={0.1} value={form.importance}
                  onChange={e => set('importance', parseFloat(e.target.value))}
                  style={{ flex: 1 }}
                />
                <span className="tabular" style={{ fontSize: '13px', fontWeight: 650, minWidth: '28px' }}>
                  {form.importance.toFixed(1)}
                </span>
              </div>
            </div>

            <div style={fieldWrap}>
              <label className="tw-label" htmlFor="src-notes">Notes</label>
              <textarea id="src-notes" className="tw-textarea" value={form.notes} onChange={e => set('notes', e.target.value)} rows={3} />
            </div>

            <div style={{ ...fieldWrap, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px', padding: '12px 14px', border: '1px solid var(--line)', borderRadius: 'var(--radius)', background: 'var(--surface-subtle)' }}>
              <div>
                <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--ink)' }}>Force Selenium</div>
                <div className="tw-small tw-muted">Use a full browser for pages that need JavaScript to render.</div>
              </div>
              <button
                type="button"
                role="switch"
                className="tw-switch"
                aria-checked={form.force_selenium}
                aria-label="Force Selenium"
                onClick={() => set('force_selenium', !form.force_selenium)}
              />
            </div>

            {apiError && <Callout tone="error" icon="alert" title="Could not save the source">{apiError}</Callout>}
          </div>

          <div className="tw-drawer-foot">
            <button type="button" className="tw-btn" onClick={onClose}>Cancel</button>
            <button type="submit" className="tw-btn is-primary" disabled={saving}>
              {saving ? 'Saving…' : 'Save source'}
            </button>
          </div>
        </form>
      </div>
    </>
  );
}

// ── Main Sources section ──────────────────────────────────────────────────────
const FREQ_LABEL = f => f ? f.charAt(0).toUpperCase() + f.slice(1) : '—';

export default function Sources() {
  const { data: sources = [], isLoading, error } = useSources();
  const [expandedId, setExpandedId] = useState(null);
  const [addOpen, setAddOpen] = useState(false);
  const [query, setQuery] = useState('');
  const queryClient = useQueryClient();

  const toggleRow = id => setExpandedId(cur => cur === id ? null : id);

  const handleAddSuccess = () => {
    queryClient.invalidateQueries({ queryKey: ['sources'] });
    setAddOpen(false);
  };

  const q = query.trim().toLowerCase();
  const visible = q
    ? sources.filter(s => (s.source_id ?? '').toLowerCase().includes(q) || (s.title ?? '').toLowerCase().includes(q))
    : sources;

  const failing = sources.filter(s => (s.consecutive_failures ?? 0) >= 2).length;
  const warning = sources.filter(s => (s.consecutive_failures ?? 0) === 1).length;

  return (
    <div className="tw-page">
      <PageHeader
        eyebrow="Monitor · Influencer source registry"
        title="Monitored sources"
        description="The authoritative pages, legislation and feeds Tripwire checks for changes. Select a source to see its recent runs and latest snapshot."
        actions={
          <button type="button" className="tw-btn is-primary" onClick={() => setAddOpen(true)}>
            <Icon name="plus" /> Add source
          </button>
        }
      />

      <ErrorBanner error={error} />

      {!isLoading && sources.length > 0 && (
        <div className="tw-metrics" style={{ marginBottom: '18px' }}>
          <MetricCard label="Sources monitored" value={sources.length} icon="source" />
          <MetricCard label="Healthy" value={sources.length - failing - warning} tone="ok" context="No recent fetch failures" />
          <MetricCard label="One recent failure" value={warning} tone={warning ? 'warn' : undefined} context="Will retry on the next run" />
          <MetricCard label="Failing" value={failing} tone={failing ? 'alert' : undefined} context="Two or more consecutive failures" />
        </div>
      )}

      <Card
        noBody
        clip
        title="Source registry"
        subtitle={`${visible.length} of ${sources.length} sources`}
        actions={<SearchInput value={query} onChange={setQuery} placeholder="Search by ID or title" width={260} />}
      >
        {isLoading ? (
          <div style={{ padding: '8px 20px' }}><Loading>Loading sources…</Loading></div>
        ) : (
          <div className="tw-table-wrap">
            <table className="tw-table">
              <thead>
                <tr>
                  <th style={{ width: 28 }} />
                  <th>Source</th>
                  <th>Type</th>
                  <th>Importance</th>
                  <th>Frequency</th>
                  <th>Last checked</th>
                  <th>Last changed</th>
                  <th style={{ textAlign: 'center' }}>Failures</th>
                  <th>Recent history</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {visible.map(src => {
                  const isExpanded = expandedId === src.source_id;
                  const failures = src.consecutive_failures ?? 0;
                  return (
                    <React.Fragment key={src.source_id}>
                      <tr
                        className={`is-clickable${isExpanded ? ' is-expanded' : ''}`}
                        onClick={() => toggleRow(src.source_id)}
                        aria-expanded={isExpanded}
                      >
                        <td style={{ paddingRight: 0 }}><Caret open={isExpanded} /></td>

                        <td style={{ maxWidth: '300px' }}>
                          <div className="tw-cell-strong" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={src.title}>
                            {src.title}
                          </div>
                          <div className="tw-code" style={{ fontSize: '11px', color: 'var(--faint)', marginTop: '2px' }}>{src.source_id}</div>
                        </td>

                        <td><TypePill type={src.source_type} /></td>

                        <td><ImportanceBar value={src.importance} /></td>

                        <td style={{ whiteSpace: 'nowrap' }}>
                          <span style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}>
                            <BarcodeColumn frequency={src.check_frequency} />
                            {FREQ_LABEL(src.check_frequency)}
                          </span>
                        </td>

                        <td style={{ whiteSpace: 'nowrap' }}>{src.last_checked ? formatRelativeTime(src.last_checked) : '—'}</td>

                        <td style={{ whiteSpace: 'nowrap' }}>{src.last_changed ? formatRelativeTime(src.last_changed) : '—'}</td>

                        <td className="tabular" style={{ textAlign: 'center', fontWeight: failures ? 700 : 400, color: failures >= 2 ? 'var(--error)' : failures ? 'var(--warn)' : 'var(--muted)' }}>
                          {failures}
                        </td>

                        <td><SparkLine history={src.check_history ?? []} /></td>

                        <td><StatusDot failures={failures} /></td>
                      </tr>

                      {isExpanded && (
                        <tr>
                          <SourceExpandedDetail sourceId={src.source_id} />
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
                {!visible.length && (
                  <tr>
                    <td colSpan={10} style={{ padding: 0 }}>
                      <EmptyState icon="source" compact title={sources.length ? 'No sources match your search' : 'No sources found'} />
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <AddSourcePanel
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onSuccess={handleAddSuccess}
      />
    </div>
  );
}
