import React, { useState, useRef, useEffect, useCallback } from 'react';
import { useDashboard } from '../App.jsx';
import { useSources } from '../hooks/useData.js';

const DATE_PRESETS = ['7D', '30D', '90D', 'ALL'];

const STAGE_ENTRIES = [1, 2, 3, 4, 5, 6, 7, 8, 9];

const VERDICT_ENTRIES = [
  { key: 'CHANGE_REQUIRED', color: 'var(--state-alert)' },
  { key: 'UNCERTAIN',       color: 'var(--state-warn)' },
  { key: 'NO_CHANGE',       color: 'var(--state-ok)' },
  { key: 'ERROR',           color: 'var(--state-error)' },
];

const chipBase = {
  fontFamily: 'var(--font-mono)',
  fontSize: '11px',
  padding: '3px 8px',
  border: '1px solid var(--rule-accent)',
  cursor: 'pointer',
  background: 'transparent',
  lineHeight: 1.4,
};

export default function FilterBar() {
  const { filters, setDatePreset, setDateRange, setSources, setStageMin, setVerdicts } = useDashboard();
  const { data: sourcesData } = useSources();
  const sources = sourcesData || [];

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

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>

      {/* Date presets + custom range */}
      <div style={{ display: 'flex', gap: '3px', alignItems: 'center' }}>
        {DATE_PRESETS.map(p => (
          <button
            key={p}
            onClick={() => setDatePreset(p)}
            style={{
              ...chipBase,
              background: filters.datePreset === p ? 'var(--bg-accent)' : 'transparent',
              color: filters.datePreset === p ? 'var(--text-primary)' : 'var(--text-tertiary)',
            }}
          >
            {p}
          </button>
        ))}
        <span style={{ color: 'var(--text-tertiary)', margin: '0 6px', fontFamily: 'var(--font-mono)', fontSize: '11px' }}>·</span>
        <input
          type="date"
          value={filters.from || ''}
          onChange={e => setDateRange(e.target.value || null, filters.to)}
          style={{
            fontFamily: 'var(--font-mono)',
            fontSize: '10px',
            background: 'var(--bg-tertiary)',
            border: '1px solid var(--rule)',
            color: filters.from ? 'var(--text-secondary)' : 'var(--text-tertiary)',
            padding: '2px 4px',
            colorScheme: 'dark',
          }}
        />
        <span style={{ color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)', fontSize: '11px', margin: '0 2px' }}>–</span>
        <input
          type="date"
          value={filters.to || ''}
          onChange={e => setDateRange(filters.from, e.target.value || null)}
          style={{
            fontFamily: 'var(--font-mono)',
            fontSize: '10px',
            background: 'var(--bg-tertiary)',
            border: '1px solid var(--rule)',
            color: filters.to ? 'var(--text-secondary)' : 'var(--text-tertiary)',
            padding: '2px 4px',
            colorScheme: 'dark',
          }}
        />
      </div>

      {/* Source multi-select dropdown */}
      <div ref={dropdownRef} style={{ position: 'relative' }}>
        <button
          onClick={() => setDropdownOpen(o => !o)}
          style={{
            ...chipBase,
            background: filters.sources.length ? 'var(--bg-accent)' : 'transparent',
            color: filters.sources.length ? 'var(--text-primary)' : 'var(--text-tertiary)',
          }}
        >
          {filters.sources.length ? `SOURCES (${filters.sources.length})` : 'SOURCES'}{' ▾'}
        </button>
        {dropdownOpen && (
          <div style={{
            position: 'absolute',
            top: 'calc(100% + 4px)',
            left: 0,
            background: 'var(--bg-secondary)',
            border: '1px solid var(--rule-accent)',
            zIndex: 200,
            width: '220px',
            maxHeight: '260px',
            overflowY: 'auto',
          }}>
            <input
              autoFocus
              placeholder="Search sources…"
              value={search}
              onChange={e => setSearch(e.target.value)}
              style={{
                width: '100%',
                padding: '6px 8px',
                background: 'var(--bg-tertiary)',
                border: 'none',
                borderBottom: '1px solid var(--rule)',
                color: 'var(--text-primary)',
                fontFamily: 'var(--font-mono)',
                fontSize: '11px',
                outline: 'none',
              }}
            />
            {filteredSources.length === 0 && (
              <div style={{ padding: '8px', fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)' }}>
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
                  onClick={() => toggleSource(id)}
                  style={{
                    padding: '5px 8px',
                    cursor: 'pointer',
                    background: selected ? 'var(--bg-accent)' : 'transparent',
                    color: selected ? 'var(--text-primary)' : 'var(--text-secondary)',
                    fontFamily: 'var(--font-mono)',
                    fontSize: '10px',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                  }}
                  onMouseEnter={e => { if (!selected) e.currentTarget.style.background = 'var(--bg-tertiary)'; }}
                  onMouseLeave={e => { if (!selected) e.currentTarget.style.background = 'transparent'; }}
                >
                  <span style={{
                    width: '8px',
                    height: '8px',
                    border: `1px solid ${selected ? 'var(--text-primary)' : 'var(--rule-accent)'}`,
                    background: selected ? 'var(--text-primary)' : 'transparent',
                    flexShrink: 0,
                  }} />
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {label}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Stage reached chips S1–S9 */}
      <div style={{ display: 'flex', gap: '3px', alignItems: 'center' }}>
        {STAGE_ENTRIES.map(n => (
          <button
            key={n}
            onClick={() => setStageMin(filters.stageMin === n ? null : n)}
            style={{
              ...chipBase,
              fontSize: '10px',
              padding: '3px 6px',
              background: filters.stageMin === n ? 'var(--bg-accent)' : 'transparent',
              color: filters.stageMin === n ? 'var(--text-primary)' : 'var(--text-tertiary)',
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
            }}
          >
            <span style={{
              width: '6px',
              height: '6px',
              borderRadius: '50%',
              background: `var(--stage-${n})`,
              flexShrink: 0,
            }} />
            S{n}
          </button>
        ))}
      </div>

      {/* Verdict chips */}
      <div style={{ display: 'flex', gap: '3px', alignItems: 'center' }}>
        {VERDICT_ENTRIES.map(({ key, color }) => {
          const selected = filters.verdicts.includes(key);
          return (
            <button
              key={key}
              onClick={() => toggleVerdict(key)}
              style={{
                ...chipBase,
                fontSize: '10px',
                padding: '3px 6px',
                border: `1px solid ${selected ? color : 'var(--rule-accent)'}`,
                background: selected ? color + '22' : 'transparent',
                color: selected ? color : 'var(--text-tertiary)',
              }}
            >
              {key.replace(/_/g, '​')}
            </button>
          );
        })}
      </div>

      {/* Reset */}
      <button
        onClick={reset}
        style={{
          fontFamily: 'var(--font-mono)',
          fontSize: '10px',
          color: 'var(--text-tertiary)',
          background: 'none',
          border: 'none',
          cursor: 'pointer',
          padding: 0,
          textDecoration: 'underline',
          textDecorationStyle: 'dotted',
        }}
        onMouseEnter={e => { e.currentTarget.style.color = 'var(--text-secondary)'; }}
        onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-tertiary)'; }}
      >
        RESET
      </button>
    </div>
  );
}
