import React, { useMemo } from 'react';

// Build sets of added and deleted lines from diff string ("+ new", "- old")
function buildDiffSets(diff) {
  const added   = new Set();
  const deleted = new Set();
  if (!diff) return { added, deleted };
  for (const line of diff.split('\n')) {
    if (line.startsWith('+ ')) added.add(line.slice(2));
    else if (line.startsWith('- ')) deleted.add(line.slice(2));
  }
  return { added, deleted };
}

function TextPanel({ header, text, addedLines = new Set(), deletedLines = new Set() }) {
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
          const isAdded   = line.trim() !== '' && addedLines.has(line);
          const isDeleted = line.trim() !== '' && deletedLines.has(line);
          return (
            <div
              key={i}
              style={{
                fontFamily: '"DM Mono", monospace',
                fontSize: '11px',
                lineHeight: '1.6',
                padding: '0 12px',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-all',
                // Additions: underlined in --state-ok green
                // Deletions: struck-through in --state-alert red
                color: isAdded
                  ? 'var(--state-ok)'
                  : isDeleted
                    ? 'var(--state-alert)'
                    : 'var(--text-secondary)',
                textDecoration: isAdded
                  ? 'underline'
                  : isDeleted
                    ? 'line-through'
                    : 'none',
              }}
            >
              {line || ' '}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ChunkCard({ text, isMatch }) {
  return (
    <div style={{
      padding: '10px 12px',
      marginBottom: '8px',
      border: `1px solid ${isMatch ? 'var(--state-warn)' : 'var(--rule)'}`,
      background: isMatch ? 'rgba(212,168,32,0.1)' : 'var(--bg-tertiary)',
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

  const { added: addedLines, deleted: deletedLines } = useMemo(
    () => buildDiffSets(diff),
    [diff]
  );

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
        {/* Legend */}
        <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: '9px', color: 'var(--state-ok)',
            textDecoration: 'underline', letterSpacing: '0.04em' }}>
            ADDITIONS
          </span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: '9px', color: 'var(--state-alert)',
            textDecoration: 'line-through', letterSpacing: '0.04em' }}>
            DELETIONS
          </span>
        </div>
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
          addedLines={addedLines}
          deletedLines={deletedLines}
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
