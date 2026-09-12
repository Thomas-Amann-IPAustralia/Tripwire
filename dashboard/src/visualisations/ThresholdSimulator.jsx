import React, { useState, useMemo } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip as RechartTooltip, ReferenceLine,
  ResponsiveContainer, Legend,
} from 'recharts';
import { useRuns } from '../hooks/useData.js';

const THRESHOLD_PARAMS = [
  {
    key: 'biencoder_high_threshold',
    label: 'Bi-Encoder High',
    scoreField: 'biencoder_max',
    configPath: ['semantic_scoring', 'biencoder', 'high_threshold'],
  },
  {
    key: 'biencoder_low_medium_threshold',
    label: 'Bi-Encoder Low/Med',
    scoreField: 'biencoder_max',
    configPath: ['semantic_scoring', 'biencoder', 'low_medium_threshold'],
  },
  {
    key: 'crossencoder_threshold',
    label: 'Cross-Encoder',
    scoreField: 'crossencoder_score',
    configPath: ['semantic_scoring', 'crossencoder', 'threshold'],
  },
];

function getByPath(obj, path) {
  return path.reduce((acc, k) => acc?.[k], obj);
}

function StatCard({ label, value, color }) {
  return (
    <div style={{
      background: 'var(--bg-tertiary)', border: '1px solid var(--rule)', padding: '8px 10px',
    }}>
      <div style={{
        fontFamily: 'var(--font-mono)', fontSize: '8px', color: 'var(--text-tertiary)',
        letterSpacing: '0.07em', marginBottom: '3px', textTransform: 'uppercase',
      }}>
        {label}
      </div>
      <div style={{
        fontFamily: 'var(--font-mono)', fontSize: '15px',
        color: color || 'var(--text-primary)',
      }}>
        {value}
      </div>
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

  const recallColor = stats.recall == null ? 'var(--text-secondary)'
    : stats.recall < 0.8 ? 'var(--state-warn)' : 'var(--state-ok)';
  const deltaSign = stats.deltaVolume > 0 ? '+' : '';
  const deltaColor = stats.deltaVolume === 0 ? 'var(--text-secondary)'
    : stats.deltaVolume > 0 ? 'var(--state-warn)' : 'var(--state-ok)';

  const refX = currentThreshold.toFixed(2);

  const dateInputStyle = {
    fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-primary)',
    background: 'var(--bg-tertiary)', border: '1px solid var(--rule-accent)',
    padding: '4px 6px', width: '100%', display: 'block', outline: 'none',
    marginTop: '4px',
  };

  return (
    <div style={{ background: 'var(--bg-secondary)', border: '1px solid var(--rule)' }}>
      <div style={{
        padding: '10px 16px', borderBottom: '1px solid var(--rule)',
        fontFamily: 'var(--font-display)', fontSize: '14px',
        letterSpacing: '0.08em', color: 'var(--text-primary)',
      }}>
        THRESHOLD SENSITIVITY SIMULATOR
      </div>

      <div style={{ display: 'flex', minHeight: '280px' }}>
        {/* ── Left: controls ── */}
        <div style={{
          width: '220px', flexShrink: 0, borderRight: '1px solid var(--rule)',
          padding: '16px', display: 'flex', flexDirection: 'column', gap: '16px',
        }}>
          <div>
            <div style={ctrlLabelStyle}>THRESHOLD PARAMETER</div>
            <select
              value={selectedKey}
              onChange={e => { setSelectedKey(e.target.value); setSimThreshold(null); }}
              style={{
                fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--text-primary)',
                background: 'var(--bg-tertiary)', border: '1px solid var(--rule-accent)',
                padding: '5px 8px', width: '100%', cursor: 'pointer', outline: 'none',
              }}
            >
              {THRESHOLD_PARAMS.map(p => (
                <option key={p.key} value={p.key}>{p.label}</option>
              ))}
            </select>
          </div>

          <div>
            <div style={ctrlLabelStyle}>
              SIMULATE AT: {displayThreshold.toFixed(2)}
            </div>
            <input
              type="range" min={0} max={1} step={0.01}
              value={displayThreshold}
              onChange={e => setSimThreshold(parseFloat(e.target.value))}
              style={{ width: '100%', accentColor: 'var(--stage-4)', marginTop: '4px' }}
            />
          </div>

          <div>
            <div style={ctrlLabelStyle}>DATE RANGE</div>
            <input
              type="date" value={dateFrom}
              onChange={e => setDateFrom(e.target.value)}
              style={dateInputStyle}
            />
            <input
              type="date" value={dateTo}
              onChange={e => setDateTo(e.target.value)}
              style={dateInputStyle}
            />
          </div>

          <div style={{ marginTop: 'auto', display: 'flex', flexDirection: 'column', gap: '6px' }}>
            <StatCard
              label="EST. PRECISION"
              value={stats.precision !== null ? (stats.precision * 100).toFixed(1) + '%' : '—'}
            />
            <StatCard
              label="EST. RECALL (REL.)"
              value={stats.recall !== null ? (stats.recall * 100).toFixed(1) + '%' : '—'}
              color={recallColor}
            />
            <StatCard
              label="Δ ALERT VOLUME"
              value={stats.deltaVolume !== null ? deltaSign + stats.deltaVolume : '—'}
              color={deltaColor}
            />
          </div>
        </div>

        {/* ── Right: chart ── */}
        <div style={{ flex: 1, padding: '16px 12px 12px 12px' }}>
          {filteredRuns.length === 0 ? (
            <div style={{
              fontFamily: 'var(--font-mono)', fontSize: '11px',
              color: 'var(--text-tertiary)', padding: '20px',
            }}>
              NO RUN DATA WITH SCORES FOR SELECTED PARAMETER
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={230}>
              <BarChart data={chartData} barGap={1} barCategoryGap={2}>
                <CartesianGrid
                  vertical={false}
                  stroke="var(--rule)"
                  strokeDasharray="2 4"
                />
                <XAxis
                  dataKey="threshold"
                  tick={{ fontFamily: 'var(--font-mono)', fontSize: 8, fill: 'var(--text-tertiary)' }}
                  interval={3} tickLine={false}
                  axisLine={{ stroke: 'var(--rule-accent)' }}
                />
                <YAxis
                  tick={{ fontFamily: 'var(--font-mono)', fontSize: 8, fill: 'var(--text-tertiary)' }}
                  tickLine={false} axisLine={false} width={28}
                />
                <RechartTooltip
                  contentStyle={{
                    background: 'var(--bg-secondary)', border: '1px solid var(--rule-accent)',
                    fontFamily: 'var(--font-mono)', fontSize: '10px',
                    color: 'var(--text-primary)', borderRadius: 0,
                  }}
                  labelStyle={{ color: 'var(--text-secondary)', marginBottom: '4px' }}
                  cursor={{ fill: 'rgba(255,255,255,0.03)' }}
                />
                <Legend
                  iconType="square" iconSize={8}
                  wrapperStyle={{
                    fontFamily: 'var(--font-mono)', fontSize: '9px',
                    color: 'var(--text-secondary)',
                  }}
                />
                <Bar dataKey="wouldAlert"    name="Would Alert"     fill="var(--state-alert)"  opacity={0.75} />
                <Bar dataKey="wouldNotAlert" name="Would Not Alert" fill="var(--bg-accent)"    opacity={1} />
                <ReferenceLine
                  x={refX}
                  stroke="var(--state-warn)"
                  strokeDasharray="4 2"
                  label={{
                    value: 'CURRENT', position: 'top',
                    fill: 'var(--state-warn)',
                    fontFamily: 'var(--font-mono)', fontSize: 9,
                  }}
                />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>
    </div>
  );
}

const ctrlLabelStyle = {
  fontFamily: 'var(--font-mono)', fontSize: '9px', color: 'var(--text-tertiary)',
  letterSpacing: '0.07em', textTransform: 'uppercase',
};
