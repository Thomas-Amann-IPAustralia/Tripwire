import React, { useState, useMemo } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip as RechartTooltip, ReferenceLine,
  ResponsiveContainer, Legend,
} from 'recharts';
import { useRuns } from '../hooks/useData.js';
import { Card, EmptyState, chartAxisTick, chartTooltipStyle } from '../components/ui.jsx';

const THRESHOLD_PARAMS = [
  {
    key: 'biencoder_high_threshold',
    label: 'Bi-encoder high',
    scoreField: 'biencoder_max',
    configPath: ['semantic_scoring', 'biencoder', 'high_threshold'],
  },
  {
    key: 'biencoder_low_medium_threshold',
    label: 'Bi-encoder low/medium',
    scoreField: 'biencoder_max',
    configPath: ['semantic_scoring', 'biencoder', 'low_medium_threshold'],
  },
  {
    key: 'crossencoder_threshold',
    label: 'Cross-encoder',
    scoreField: 'crossencoder_score',
    configPath: ['semantic_scoring', 'crossencoder', 'threshold'],
  },
];

function getByPath(obj, path) {
  return path.reduce((acc, k) => acc?.[k], obj);
}

function StatCard({ label, value, color, hint }) {
  return (
    <div className="tw-kv" title={hint}>
      <div className="tw-kv-label">{label}</div>
      <div className="tw-kv-value" style={{ color: color || 'var(--ink)' }}>{value}</div>
    </div>
  );
}

export default function ThresholdSimulator({ config }) {
  const [selectedKey, setSelectedKey] = useState(THRESHOLD_PARAMS[0].key);
  const [simThreshold, setSimThreshold] = useState(null);
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo]   = useState('');

  const { data: runs = [] } = useRuns();

  const paramDef = THRESHOLD_PARAMS.find(p => p.key === selectedKey);

  const currentThreshold = useMemo(() => {
    if (!config || !paramDef) return 0.5;
    return getByPath(config, paramDef.configPath) ?? 0.5;
  }, [config, paramDef]);

  const displayThreshold = simThreshold ?? currentThreshold;

  const filteredRuns = useMemo(() => {
    if (!paramDef) return [];
    return runs.filter(r => {
      const score = r[paramDef.scoreField];
      if (score == null) return false;
      if (dateFrom && r.timestamp < dateFrom) return false;
      if (dateTo   && r.timestamp > dateTo)   return false;
      return true;
    });
  }, [runs, paramDef, dateFrom, dateTo]);

  // Build bar chart data: 21 steps from 0.00 to 1.00 (step 0.05)
  const chartData = useMemo(() => {
    if (!paramDef) return [];
    return Array.from({ length: 21 }, (_, i) => {
      const t = Math.round(i * 5) / 100;
      let wouldAlert = 0, wouldNotAlert = 0;
      for (const run of filteredRuns) {
        if ((run[paramDef.scoreField] ?? 0) >= t) wouldAlert++;
        else wouldNotAlert++;
      }
      return { threshold: t.toFixed(2), wouldAlert, wouldNotAlert };
    });
  }, [filteredRuns, paramDef]);

  const stats = useMemo(() => {
    if (!filteredRuns.length || !paramDef) {
      return { precision: null, recall: null, deltaVolume: null };
    }
    const countAt = t => filteredRuns.filter(r => (r[paramDef.scoreField] ?? 0) >= t).length;
    const currentCount = countAt(currentThreshold);
    const simCount     = countAt(displayThreshold);
    const deltaVolume  = simCount - currentCount;
    const recall       = currentCount > 0 ? simCount / currentCount : null;

    const withFeedback = filteredRuns.filter(r => r.feedback === 'useful' || r.feedback === 'not_significant');
    let precision = null;
    if (withFeedback.length >= 3) {
      const alertedWithFeedback = withFeedback.filter(r => (r[paramDef.scoreField] ?? 0) >= displayThreshold);
      const tp = alertedWithFeedback.filter(r => r.feedback === 'useful').length;
      precision = alertedWithFeedback.length > 0 ? tp / alertedWithFeedback.length : null;
    }

    return { precision, recall, deltaVolume };
  }, [filteredRuns, paramDef, currentThreshold, displayThreshold]);

  const recallColor = stats.recall == null ? 'var(--body)'
    : stats.recall < 0.8 ? 'var(--warn)' : 'var(--ok)';
  const deltaSign = stats.deltaVolume > 0 ? '+' : '';
  const deltaColor = stats.deltaVolume === 0 ? 'var(--body)'
    : stats.deltaVolume > 0 ? 'var(--warn)' : 'var(--ok)';

  const refX = currentThreshold.toFixed(2);

  return (
    <Card
      noBody
      clip
      title="Threshold sensitivity simulator"
      subtitle="Replay recorded scores to see how many alerts a different threshold would have produced. Nothing here changes the configuration."
    >
      <div className="tw-sim" style={{ display: 'grid', gridTemplateColumns: '260px minmax(0, 1fr)', minHeight: '300px' }}>
        <div style={{
          borderRight: '1px solid var(--line-soft)', background: 'var(--surface-subtle)',
          padding: '18px 20px', display: 'flex', flexDirection: 'column', gap: '16px',
        }}>
          <div>
            <label className="tw-label" htmlFor="sim-param">Threshold</label>
            <select
              id="sim-param"
              className="tw-select"
              value={selectedKey}
              onChange={e => { setSelectedKey(e.target.value); setSimThreshold(null); }}
            >
              {THRESHOLD_PARAMS.map(p => (
                <option key={p.key} value={p.key}>{p.label}</option>
              ))}
            </select>
          </div>

          <div>
            <label className="tw-label" htmlFor="sim-at" style={{ display: 'flex', justifyContent: 'space-between' }}>
              Simulate at <span className="tabular" style={{ color: 'var(--accent-ink)' }}>{displayThreshold.toFixed(2)}</span>
            </label>
            <input
              id="sim-at"
              type="range" min={0} max={1} step={0.01}
              value={displayThreshold}
              onChange={e => setSimThreshold(parseFloat(e.target.value))}
              style={{ width: '100%' }}
            />
            <div className="tw-small tw-muted">Current setting {currentThreshold.toFixed(2)}</div>
          </div>

          <div>
            <span className="tw-label">Date range</span>
            <div style={{ display: 'grid', gap: '6px' }}>
              <input type="date" className="tw-input" aria-label="From date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} style={{ fontSize: '12px' }} />
              <input type="date" className="tw-input" aria-label="To date" value={dateTo} onChange={e => setDateTo(e.target.value)} style={{ fontSize: '12px' }} />
            </div>
          </div>

          <div style={{ marginTop: 'auto', display: 'grid', gap: '8px' }}>
            <StatCard
              label="Estimated precision"
              hint="Share of alerts rated useful, among alerts with feedback"
              value={stats.precision !== null ? (stats.precision * 100).toFixed(1) + '%' : '—'}
            />
            <StatCard
              label="Estimated recall (relative)"
              hint="Alerts at the simulated threshold as a share of alerts at the current threshold"
              value={stats.recall !== null ? (stats.recall * 100).toFixed(1) + '%' : '—'}
              color={recallColor}
            />
            <StatCard
              label="Change in alert volume"
              value={stats.deltaVolume !== null ? deltaSign + stats.deltaVolume : '—'}
              color={deltaColor}
            />
          </div>
        </div>

        <div style={{ padding: '18px 16px 14px', minWidth: 0 }}>
          {filteredRuns.length === 0 ? (
            <EmptyState icon="activity" title="No scored runs for this threshold">
              Runs need a recorded score for the selected threshold before they can be simulated.
            </EmptyState>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={chartData} barGap={1} barCategoryGap={3}>
                <CartesianGrid vertical={false} stroke="var(--line)" strokeDasharray="3 4" />
                <XAxis
                  dataKey="threshold"
                  tick={chartAxisTick}
                  interval={3} tickLine={false}
                  axisLine={{ stroke: 'var(--line-strong)' }}
                />
                <YAxis tick={chartAxisTick} tickLine={false} axisLine={false} width={34} />
                <RechartTooltip
                  contentStyle={chartTooltipStyle}
                  labelStyle={{ color: 'var(--ink)', fontWeight: 650, marginBottom: '4px' }}
                  labelFormatter={l => `Threshold ${l}`}
                  cursor={{ fill: 'var(--surface-hover)' }}
                />
                <Legend
                  iconType="circle" iconSize={8}
                  wrapperStyle={{ fontFamily: 'var(--font-sans)', fontSize: '12px', color: 'var(--muted)' }}
                />
                <Bar dataKey="wouldAlert"    name="Would alert"     fill="var(--alert)" opacity={0.8} radius={[3, 3, 0, 0]} />
                <Bar dataKey="wouldNotAlert" name="Would not alert" fill="var(--line-strong)" radius={[3, 3, 0, 0]} />
                <ReferenceLine
                  x={refX}
                  stroke="var(--ochre-ink)"
                  strokeDasharray="4 3"
                  label={{
                    value: 'Current', position: 'top',
                    fill: 'var(--ochre-ink)',
                    fontFamily: 'var(--font-sans)', fontSize: 11, fontWeight: 600,
                  }}
                />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>
      <style>{`@media (max-width: 900px) { .tw-sim { grid-template-columns: 1fr !important; } }`}</style>
    </Card>
  );
}
