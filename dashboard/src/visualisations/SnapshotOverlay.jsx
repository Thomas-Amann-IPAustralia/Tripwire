import React, { useMemo } from 'react';

// Identify changed lines from diff string ("- old line", "+ new line")
function buildChangedLineSet(diff) {
  if (!diff) return new Set();
  const changed = new Set();
  const lines = diff.split('\n');
  // Collect lines added in the new snapshot ("+") — these highlight in current text
  for (const line of lines) {
    if (line.startsWith('+ ')) changed.add(line.slice(2));
    if (line.startsWith('- ')) changed.add(line.slice(2));
  }
  return changed;
}

function TextPanel({ header, text, changedLines = new Set(), highlight = 'left-border' }) {
  const warnColor = getComputedStyle(document.documentElement).getPropertyValue('--state-warn').trim() || '#d4a820';

  const lines = useMemo(() => (text ?? '').split('\n'), [text]);

  return (
    <div style={{
      flex: 1,
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden',
      borderRight: '1px solid var(--rule)',
    }}>
      {/* Panel header */}
      <div style={{
        padding: '8px 12px',
        borderBottom: '1px solid var(--rule)',
        fontFamily: 'var(--font-mono)',
        fontSize: '10px',
        color: 'var(--text-secondary)',
        letterSpacing: '0.06em',
        flexShrink: 0,
        background: 'var(--bg-tertiary)',
      }}>
        {header}
      </div>

      {/* Scrollable body */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '0' }}>
        {!text && (
          <div style={{
            padding: '16px',
            fontFamily: 'var(--font-mono)', fontSize: '11px',
            color: 'var(--text-tertiary)',
          }}>
            NO DATA
          </div>
        )}
        {lines.map((line, i) => {
          const isChanged = changedLines.has(line) && line.trim() !== '';
          return (
            <div
              key={i}
              style={{
                fontFamily: '"DM Mono", monospace',
                fontSize: '11px',
                color: isChanged ? 'var(--text-primary)' : 'var(--text-secondary)',
                lineHeight: '1.6',
                padding: '0 12px',
                borderLeft: isChanged && highlight === 'left-border'
                  ? `3px solid ${warnColor}`
                  : '3px solid transparent',
                background: isChanged && highlight === 'bg'
                  ? `rgba(${hexToRgb(warnColor)}, 0.15)`
                  : 'transparent',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-all',
              }}
            >
              {line || ' '}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function hexToRgb(hex) {
  hex = (hex || '#d4a820').replace('#', '');
  if (hex.length === 3) hex = hex.split('').map(c => c + c).join('');
  return `${parseInt(hex.slice(0,2),16)},${parseInt(hex.slice(2,4),16)},${parseInt(hex.slice(4,6),16)}`;
}

function ChunkCard({ text, isMatch }) {
  const warnColor = getComputedStyle(document.documentElement).getPropertyValue('--state-warn').trim() || '#d4a820';
  return (
    <div style={{
      padding: '10px 12px',
      marginBottom: '8px',
      border: `1px solid ${isMatch ? warnColor : 'var(--rule)'}`,
      background: isMatch
        ? `rgba(${hexToRgb(warnColor)}, 0.15)`
        : 'var(--bg-tertiary)',
    }}>
      <div style={{
        fontFamily: 'Lora, serif',
        fontSize: '13px',
        color: 'var(--text-primary)',
        lineHeight: '1.7',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
      }}>
        {text}
      </div>
    </div>
  );
}

/**
 * SnapshotOverlay — used from the Sources section.
 *
 * Props:
 *   data  — response object from useSnapshot(sourceId):
 *           { source_id, snapshot_text, previous_snapshot_text, diff,
 *             best_match_page_id, top_chunk_ids, chunk_texts }
 *   onClose — callback to close the overlay
 */
export default function SnapshotOverlay({ data, onClose }) {
  const snapshot = data?.data ?? data ?? {};

  const {
    source_id,
    snapshot_text,
    previous_snapshot_text,
    diff,
    best_match_page_id,
    top_chunk_ids = [],
    chunk_texts   = [],
  } = snapshot;

  const changedLines = useMemo(() => buildChangedLineSet(diff), [diff]);

  return (
    <div style={{
      position: 'fixed', inset: 0,
      background: 'rgba(0,0,0,0.6)',
      zIndex: 200,
      display: 'flex',
      flexDirection: 'column',
    }}>
      {/* Header bar */}
      <div style={{
        height: '48px', minHeight: '48px',
        background: 'var(--bg-secondary)',
        borderBottom: '1px solid var(--rule)',
        display: 'flex', alignItems: 'center',
        padding: '0 16px',
        gap: '12px',
      }}>
        <div style={{
          fontFamily: 'var(--font-display)', fontSize: '18px',
          color: 'var(--text-primary)', letterSpacing: '0.06em',
        }}>
          SNAPSHOT
        </div>
        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: '11px',
          color: 'var(--text-tertiary)',
        }}>
          {source_id ?? '—'}
        </div>
        <div style={{ flex: 1 }} />
        <button onClick={onClose} style={{
          background: 'none', border: '1px solid var(--rule)',
          cursor: 'pointer', color: 'var(--text-secondary)',
          fontFamily: 'var(--font-mono)', fontSize: '11px',
          padding: '4px 12px', letterSpacing: '0.06em',
        }}>
          CLOSE
        </button>
      </div>

      {/* Two-panel body */}
      <div style={{
        flex: 1, display: 'flex', overflow: 'hidden',
        background: 'var(--bg-secondary)',
      }}>

        {/* Left panel — current snapshot with diff highlights */}
        <TextPanel
          header={`SOURCE: ${source_id ?? '—'}`}
          text={snapshot_text ?? previous_snapshot_text}
          changedLines={changedLines}
          highlight="left-border"
        />

        {/* Right panel — matching IPFR chunks */}
        <div style={{
          flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden',
        }}>
          {/* Panel header */}
          <div style={{
            padding: '8px 12px',
            borderBottom: '1px solid var(--rule)',
            fontFamily: 'var(--font-mono)',
            fontSize: '10px',
            color: 'var(--text-secondary)',
            letterSpacing: '0.06em',
            flexShrink: 0,
            background: 'var(--bg-tertiary)',
          }}>
            BEST MATCH PAGE: {best_match_page_id ?? '—'}
          </div>

          {/* Chunks */}
          <div style={{ flex: 1, overflowY: 'auto', padding: '12px' }}>
            {chunk_texts.length === 0 && (
              <div style={{
                fontFamily: 'var(--font-mono)', fontSize: '11px',
                color: 'var(--text-tertiary)', padding: '4px 0',
              }}>
                NO MATCHING CHUNKS
              </div>
            )}
            {chunk_texts.map((text, i) => (
              <ChunkCard
                key={i}
                text={text}
                isMatch={true}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
