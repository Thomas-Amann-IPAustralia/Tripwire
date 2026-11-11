import React, { useState, useRef, useEffect, useCallback } from 'react';
import { useDashboard } from '../App.jsx';
import { useSources } from '../hooks/useData.js';
import { Segmented, Icon, VERDICTS } from './ui.jsx';

const DATE_PRESETS = [
  { value: '7D',  label: '7 days' },
  { value: '30D', label: '30 days' },
  { value: '90D', label: '90 days' },
  { value: 'ALL', label: 'All time' },
];

const STAGE_ENTRIES = [1, 2, 3, 4, 5, 6];

const VERDICT_ENTRIES = ['CHANGE_REQUIRED', 'UNCERTAIN', 'NO_CHANGE', 'ERROR'];

export default function FilterBar() {
  const { filters, setDatePreset, setDateRange, setSources, setStageMin, setVerdicts } = useDashboard();
  const { data: sourcesData } = useSources();
  const sources = sourcesData ?? [];

  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [search, setSearch] = useState('');
  const dropdownRef = useRef(null);

  useEffect(() => {
    function onDown(e) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) {
        setDropdownOpen(false);
      }
    }
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, []);

  const filteredSources = sources.filter(s => {
    const id = s.source_id || s.id || '';
    const label = s.label || id;
    const q = search.toLowerCase();
    return id.toLowerCase().includes(q) || label.toLowerCase().includes(q);
  });

  const toggleSource = useCallback((id) => {
    const next = filters.sources.includes(id)
      ? filters.sources.filter(s => s !== id)
      : [...filters.sources, id];
    setSources(next);
  }, [filters.sources, setSources]);

  const toggleVerdict = useCallback((key) => {
    const next = filters.verdicts.includes(key)
      ? filters.verdicts.filter(v => v !== key)
      : [...filters.verdicts, key];
    setVerdicts(next);
  }, [filters.verdicts, setVerdicts]);

  const reset = useCallback(() => {
    setDatePreset('30D');
    setSources([]);
    setStageMin(null);
    setVerdicts([]);
  }, [setDatePreset, setSources, setStageMin, setVerdicts]);

  const isFiltered = filters.sources.length > 0 || filters.stageMin != null || filters.verdicts.length > 0 || filters.datePreset !== '30D' || filters.from || filters.to;

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: '10px 14px', flexWrap: 'wrap',
      padding: '12px 14px', background: 'var(--surface)', border: '1px solid var(--line)',
      borderRadius: 'var(--radius-lg)', boxShadow: 'var(--shadow)',
    }}>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '12px', fontWeight: 650, color: 'var(--body)' }}>
        <Icon name="filter" size={15} style={{ color: 'var(--muted)' }} /> Filters
      </span>

      {/* Date presets + custom range */}
      <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
        <Segmented ariaLabel="Date range" options={DATE_PRESETS} value={filters.datePreset} onChange={setDatePreset} />
        <input
          type="date"
          className="tw-input"
          aria-label="From date"
          value={filters.from || ''}
          onChange={e => setDateRange(e.target.value || null, filters.to)}
          style={{ width: 'auto', height: '30px', fontSize: '12px', padding: '0 8px', color: filters.from ? 'var(--ink)' : 'var(--muted)' }}
        />
        <span className="tw-muted" style={{ fontSize: '12px' }}>to</span>
        <input
          type="date"
          className="tw-input"
          aria-label="To date"
          value={filters.to || ''}
          onChange={e => setDateRange(filters.from, e.target.value || null)}
          style={{ width: 'auto', height: '30px', fontSize: '12px', padding: '0 8px', color: filters.to ? 'var(--ink)' : 'var(--muted)' }}
        />
      </div>

      {/* Source multi-select dropdown */}
      <div ref={dropdownRef} style={{ position: 'relative' }}>
        <button
          type="button"
          className={`tw-chip${filters.sources.length ? ' active' : ''}`}
          onClick={() => setDropdownOpen(o => !o)}
          aria-expanded={dropdownOpen}
        >
          {filters.sources.length ? `Sources (${filters.sources.length})` : 'All sources'}
          <Icon name="chevronDown" size={13} />
        </button>
        {dropdownOpen && (
          <div style={{
            position: 'absolute',
            top: 'calc(100% + 6px)',
            left: 0,
            background: 'var(--surface)',
            border: '1px solid var(--line)',
            borderRadius: 'var(--radius)',
            boxShadow: 'var(--shadow-lg)',
            zIndex: 200,
            width: '280px',
            maxHeight: '320px',
            overflowY: 'auto',
            padding: '6px',
          }}>
            <input
              autoFocus
              className="tw-input"
              placeholder="Search sources"
              value={search}
              onChange={e => setSearch(e.target.value)}
              style={{ marginBottom: '4px' }}
            />
            {filteredSources.length === 0 && (
              <div style={{ padding: '10px', fontSize: '12px', color: 'var(--muted)' }}>
                No sources found
              </div>
            )}
            {filteredSources.map(s => {
              const id = s.source_id || s.id || s.label || '';
              const label = s.label || id;
              const selected = filters.sources.includes(id);
              return (
                <div
                  key={id}
                  role="option"
                  aria-selected={selected}
                  onClick={() => toggleSource(id)}
                  style={{
                    padding: '6px 8px',
                    cursor: 'pointer',
                    borderRadius: '6px',
                    background: selected ? 'var(--accent-soft)' : 'transparent',
                    color: selected ? 'var(--accent-ink)' : 'var(--body)',
                    fontSize: '12px',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                  }}
                  onMouseEnter={e => { if (!selected) e.currentTarget.style.background = 'var(--surface-hover)'; }}
                  onMouseLeave={e => { if (!selected) e.currentTarget.style.background = 'transparent'; }}
                >
                  <span style={{
                    width: '14px',
                    height: '14px',
                    borderRadius: '4px',
                    border: `1.5px solid ${selected ? 'var(--accent)' : 'var(--line-strong)'}`,
                    background: selected ? 'var(--accent)' : 'var(--surface)',
                    color: 'var(--on-accent)',
                    display: 'grid',
                    placeItems: 'center',
                    flexShrink: 0,
                  }}>
                    {selected && <Icon name="check" size={10} />}
                  </span>
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {label}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Minimum stage reached */}
      <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
        <span className="tw-small tw-muted">Reached stage</span>
        <Segmented
          ariaLabel="Minimum stage reached"
          value={filters.stageMin}
          onChange={n => setStageMin(filters.stageMin === n ? null : n)}
          options={STAGE_ENTRIES.map(n => ({ value: n, label: String(n), dot: `var(--stage-${n})`, title: `Reached stage ${n} or later` }))}
        />
      </div>

      {/* Verdict chips */}
      <div style={{ display: 'flex', gap: '6px', alignItems: 'center', flexWrap: 'wrap' }}>
        {VERDICT_ENTRIES.map(key => {
          const selected = filters.verdicts.includes(key);
          const cfg = VERDICTS[key];
          return (
            <button
              key={key}
              type="button"
              className={`tw-chip${selected ? ' active' : ''}`}
              aria-pressed={selected}
              onClick={() => toggleVerdict(key)}
              style={selected ? { background: `var(--${cfg.tone}-soft)`, borderColor: `var(--${cfg.tone}-line)`, color: cfg.color } : undefined}
            >
              <span className="tw-chip-dot" style={{ background: cfg.color }} />
              {cfg.label}
            </button>
          );
        })}
      </div>

      {/* Reset */}
      <button type="button" className="tw-btn is-quiet is-sm" onClick={reset} disabled={!isFiltered} style={{ marginLeft: 'auto' }}>
        <Icon name="reset" /> Reset
      </button>
    </div>
  );
}
