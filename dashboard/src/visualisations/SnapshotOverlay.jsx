import React, { useMemo, useEffect } from 'react';
import { usePageTitles } from '../hooks/useData.js';
import { Icon, EmptyState } from '../components/ui.jsx';

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

function PanelHeader({ children }) {
  return (
    <div style={{
      padding: '10px 16px',
      borderBottom: '1px solid var(--line)',
      fontSize: '12px',
      fontWeight: 600,
      color: 'var(--body)',
      flexShrink: 0,
      background: 'var(--surface-subtle)',
      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
    }}>
      {children}
    </div>
  );
}

function TextPanel({ header, text, addedLines = new Set(), deletedLines = new Set() }) {
  const lines = useMemo(() => (text ?? '').split('\n'), [text]);

  return (
    <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden', borderRight: '1px solid var(--line)' }}>
      <PanelHeader>{header}</PanelHeader>
      <div style={{ flex: 1, overflowY: 'auto', padding: '10px 0' }}>
        {!text && <EmptyState icon="file" compact title="No snapshot text" />}
        {lines.map((line, i) => {
          const isAdded   = line.trim() !== '' && addedLines.has(line);
          const isDeleted = line.trim() !== '' && deletedLines.has(line);
          return (
            <div
              key={i}
              style={{
                fontFamily: 'var(--font-code)',
                fontSize: '12px',
                lineHeight: '1.65',
                padding: '0 16px',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                background: isAdded ? 'var(--ok-soft)' : isDeleted ? 'var(--alert-soft)' : 'transparent',
                color: isAdded ? 'var(--ok)' : isDeleted ? 'var(--alert)' : 'var(--body)',
                textDecoration: isDeleted ? 'line-through' : 'none',
                boxShadow: isAdded ? 'inset 3px 0 0 var(--ok)' : isDeleted ? 'inset 3px 0 0 var(--alert)' : 'none',
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

function ChunkCard({ text }) {
  return (
    <div style={{
      padding: '12px 14px',
      marginBottom: '10px',
      borderRadius: 'var(--radius)',
      border: '1px solid var(--warn-line)',
      background: 'var(--warn-soft)',
      fontSize: '13.5px',
      color: 'var(--ink)',
      lineHeight: '1.7',
      whiteSpace: 'pre-wrap',
      wordBreak: 'break-word',
    }}>
      {text}
    </div>
  );
}

/**
 * SnapshotOverlay: used from the Sources section.
 *
 * Props:
 *   data  — response object from useSnapshot(sourceId):
 *           { source_id, snapshot_text, previous_snapshot_text, diff,
 *             best_match_page_id, top_chunk_ids, chunk_texts }
 *   onClose — callback to close the overlay
 */
export default function SnapshotOverlay({ data, onClose }) {
  const snapshot = data?.data ?? data ?? {};
  const pageTitles = usePageTitles();

  const {
    source_id,
    snapshot_text,
    previous_snapshot_text,
    diff,
    best_match_page_id,
    chunk_texts   = [],
  } = snapshot;

  const { added: addedLines, deleted: deletedLines } = useMemo(
    () => buildDiffSets(diff),
    [diff]
  );

  useEffect(() => {
    const onKey = e => { if (e.key === 'Escape') onClose?.(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <>
      <div className="tw-scrim" style={{ zIndex: 399 }} onClick={onClose} />
      <div className="tw-dialog" role="dialog" aria-modal="true" aria-label="Source snapshot" style={{ width: 'min(1280px, calc(100vw - 32px))', height: 'calc(100vh - 48px)' }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: '14px',
          padding: '16px 20px', borderBottom: '1px solid var(--line)', flexShrink: 0,
        }}>
          <div style={{ minWidth: 0 }}>
            <div className="tw-overline">Latest snapshot</div>
            <div className="tw-drawer-title" style={{ fontSize: '20px' }}>{source_id ?? '—'}</div>
          </div>
          <div style={{ flex: 1 }} />
          <div style={{ display: 'flex', gap: '14px', alignItems: 'center', fontSize: '12px' }}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', color: 'var(--ok)' }}>
              <span style={{ width: 10, height: 10, borderRadius: 2, background: 'var(--ok-soft)', boxShadow: 'inset 3px 0 0 var(--ok)' }} /> Added
            </span>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', color: 'var(--alert)' }}>
              <span style={{ width: 10, height: 10, borderRadius: 2, background: 'var(--alert-soft)', boxShadow: 'inset 3px 0 0 var(--alert)' }} /> Removed
            </span>
          </div>
          <button type="button" className="tw-icon-btn" onClick={onClose} aria-label="Close">
            <Icon name="close" />
          </button>
        </div>

        <div style={{ flex: 1, display: 'flex', overflow: 'hidden', minHeight: 0 }}>
          <TextPanel
            header={`Source text: ${source_id ?? '—'}`}
            text={snapshot_text ?? previous_snapshot_text}
            addedLines={addedLines}
            deletedLines={deletedLines}
          />

          <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
            <PanelHeader>
              Best matching IPFR page: {best_match_page_id
                ? `${pageTitles.get(best_match_page_id) ?? best_match_page_id}${pageTitles.has(best_match_page_id) ? ` (${best_match_page_id})` : ''}`
                : '—'}
            </PanelHeader>
            <div style={{ flex: 1, overflowY: 'auto', padding: '14px 16px' }}>
              {chunk_texts.length === 0 && <EmptyState icon="search" compact title="No matching chunks" />}
              {chunk_texts.map((text, i) => <ChunkCard key={i} text={text} />)}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
