import React, {
  useState, useEffect, useRef, useCallback, useMemo,
} from 'react';
import { useNavigate } from 'react-router-dom';
import { systemPlan } from '../lib/systemPlan.js';
import PipelineDiagram from '../components/PipelineDiagram.jsx';
import { PageHeader, Icon, Callout, SearchInput } from '../components/ui.jsx';

// ── SQL Console ───────────────────────────────────────────────────────────────
// Actual tables in ipfr.sqlite. Note the chunk table is named `chunks`
// (the system plan calls it `page_chunks`, but that name never existed in
// the database — queries against it fail with "no such table").
// Chip queries avoid BLOB embedding columns and huge text columns so the
// default click always renders a readable result.
const KNOWN_TABLES = {
  pages:             "SELECT page_id, url, title, status, last_ingested FROM pages LIMIT 20",
  chunks:            "SELECT chunk_id, page_id, chunk_index, section_heading, substr(chunk_text, 1, 120) AS chunk_text_preview FROM chunks LIMIT 20",
  entities:          "SELECT * FROM entities LIMIT 20",
  keyphrases:        "SELECT * FROM keyphrases LIMIT 20",
  graph_edges:       "SELECT * FROM graph_edges LIMIT 20",
  sections:          "SELECT * FROM sections LIMIT 20",
  pipeline_runs:     "SELECT id, run_id, source_id, timestamp, stage_reached, outcome, triggered_pages, duration_seconds FROM pipeline_runs ORDER BY timestamp DESC LIMIT 20",
  llm_assessments:   "SELECT id, run_id, ipfr_page_id, verdict, confidence, generated_at FROM llm_assessments ORDER BY generated_at DESC LIMIT 20",
  deferred_triggers: "SELECT * FROM deferred_triggers LIMIT 20",
  ingestion_runs:    "SELECT * FROM ingestion_runs ORDER BY timestamp DESC LIMIT 20",
};

const EXAMPLE_QUERY = 'SELECT page_id, url, title, status\nFROM pages\nLIMIT 20';

function SqlConsole() {
  const [sql, setSql]       = useState(EXAMPLE_QUERY);
  const [result, setResult] = useState(null);
  const [error, setError]   = useState(null);
  const [loading, setLoading] = useState(false);
  const [timing, setTiming] = useState(null);

  const run = useCallback(async () => {
    if (!sql.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    const t0 = Date.now();
    try {
      const res = await fetch('/api/sql', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sql }),
      });
      const data = await res.json();
      setTiming(Date.now() - t0);
      if (!res.ok) {
        setError(data.message || data.error || 'Query failed');
      } else {
        setResult(data);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [sql]);

  const handleKeyDown = useCallback((e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      run();
    }
  }, [run]);

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', padding: '20px 24px 18px', minHeight: 0 }}>

      <Callout icon="database" style={{ marginBottom: '14px' }}>
        Run read-only <strong>SELECT</strong> queries against the Tripwire database. Select a table to load an example query.
      </Callout>

      {/* Table chips */}
      <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', alignItems: 'center', marginBottom: '12px' }}>
        <span className="tw-small tw-muted" style={{ flexShrink: 0, marginRight: '2px' }}>Tables</span>
        {Object.entries(KNOWN_TABLES).map(([t, query]) => (
          <button
            key={t}
            type="button"
            title={query}
            onClick={() => setSql(query)}
            className="tw-chip"
            style={{ fontFamily: 'var(--font-code)', fontSize: '11.5px', padding: '3px 9px' }}
          >
            {t}
          </button>
        ))}
      </div>

      {/* Textarea */}
      <textarea
        className="tw-textarea"
        aria-label="SQL query"
        value={sql}
        onChange={e => setSql(e.target.value)}
        onKeyDown={handleKeyDown}
        spellCheck={false}
        placeholder="SELECT …"
        style={{
          height: '130px',
          minHeight: '60px',
          fontFamily: 'var(--font-code)',
          fontSize: '13px',
          lineHeight: 1.6,
          background: 'var(--surface-subtle)',
          flexShrink: 0,
        }}
      />

      {/* Run bar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', margin: '12px 0 14px', flexShrink: 0 }}>
        <button type="button" className="tw-btn is-primary" onClick={run} disabled={loading}>
          <Icon name="play" />
          {loading ? 'Running…' : 'Run query'}
        </button>
        <span className="tw-small tw-muted">
          <kbd style={{ border: '1px solid var(--line-strong)', borderRadius: 4, padding: '0 5px', background: 'var(--surface)' }}>Ctrl</kbd> + <kbd style={{ border: '1px solid var(--line-strong)', borderRadius: 4, padding: '0 5px', background: 'var(--surface)' }}>Enter</kbd> to run · SELECT only · read-only
        </span>
        {result && timing != null && (
          <span className="tw-small tabular" style={{ color: 'var(--body)', marginLeft: 'auto' }}>
            {result.rowCount.toLocaleString()} row{result.rowCount !== 1 ? 's' : ''} in {timing} ms
          </span>
        )}
      </div>

      {result?.rowWarning && (
        <Callout tone="warn" icon="alert" style={{ marginBottom: '12px' }}>{result.rowWarning}</Callout>
      )}

      {error && (
        <Callout tone="error" icon="alert" title="Query failed" style={{ marginBottom: '12px', whiteSpace: 'pre-wrap' }}>{error}</Callout>
      )}

      {/* Results table */}
      {result && (
        <div style={{ flex: 1, overflow: 'auto', border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', background: 'var(--surface)', minHeight: 0 }}>
          <table className="tw-table is-compact" style={{ width: 'max-content', minWidth: '100%', fontSize: '12px' }}>
            <thead>
              <tr>
                {result.columns.map((col, i) => (
                  <th key={i} style={{ fontFamily: 'var(--font-code)', textTransform: 'none', letterSpacing: 0, fontSize: '11.5px', color: 'var(--body)' }}>
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.rows.length === 0 ? (
                <tr>
                  <td colSpan={result.columns.length || 1} style={{ padding: '24px', textAlign: 'center', color: 'var(--muted)' }}>
                    No rows returned
                  </td>
                </tr>
              ) : (
                result.rows.map((row, ri) => (
                  <tr key={ri}>
                    {row.map((cell, ci) => (
                      <td key={ci} style={{
                        fontFamily: 'var(--font-code)',
                        fontSize: '11.5px',
                        color: cell === null ? 'var(--faint)' : 'var(--ink)',
                        fontStyle: cell === null ? 'italic' : 'normal',
                        whiteSpace: 'nowrap',
                        maxWidth: '480px',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        verticalAlign: 'top',
                      }}>
                        {cell === null ? 'NULL' : String(cell)}
                      </td>
                    ))}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── Syntax highlighting ───────────────────────────────────────────────────────
// Each highlighter returns an array of { k: 'text'|'keyword'|'string'|'comment'|'number'|'key'|'op', v: string }

function escRx(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function tokenise(code, rules) {
  // rules: [{ k: string, rx: RegExp }]  ordered by priority
  const combined = rules.map(r => `(${r.rx.source})`).join('|');
  const rx = new RegExp(combined, 'g');
  const tokens = [];
  let last = 0;
  let m;
  while ((m = rx.exec(code)) !== null) {
    if (m.index > last) tokens.push({ k: 'text', v: code.slice(last, m.index) });
    const ruleIdx = rules.findIndex((_, i) => m[i + 1] !== undefined);
    tokens.push({ k: rules[ruleIdx].k, v: m[0] });
    last = rx.lastIndex;
  }
  if (last < code.length) tokens.push({ k: 'text', v: code.slice(last) });
  return tokens;
}

const YAML_RULES = [
  { k: 'comment', rx: /#[^\n]*/ },
  { k: 'string',  rx: /"[^"]*"|'[^']*'/ },
  { k: 'keyword', rx: /\b(true|false|null|yes|no)\b/ },
  { k: 'number',  rx: /\b\d+\.?\d*\b/ },
  { k: 'key',     rx: /^[ \t]*[\w_][\w_.]*(?=\s*:)/m },
];

const SQL_RULES = [
  { k: 'comment', rx: /--[^\n]*/ },
  { k: 'string',  rx: /'[^']*'/ },
  { k: 'keyword', rx: /\b(CREATE|TABLE|INSERT|SELECT|FROM|WHERE|REFERENCES|PRIMARY|KEY|AUTOINCREMENT|NOT|NULL|UNIQUE|DEFAULT|TEXT|INTEGER|REAL|BLOB|INDEX|ON)\b/ },
  { k: 'number',  rx: /\b\d+\b/ },
];

const PYTHON_RULES = [
  { k: 'comment', rx: /#[^\n]*/ },
  { k: 'string',  rx: /"""[\s\S]*?"""|'''[\s\S]*?'''|"[^"\n]*"|'[^'\n]*'/ },
  { k: 'keyword', rx: /\b(def|class|import|from|return|if|else|elif|for|in|while|try|except|finally|with|as|pass|raise|and|or|not|True|False|None|lambda|yield|async|await)\b/ },
  { k: 'number',  rx: /\b\d+\.?\d*\b/ },
];

const JSON_RULES = [
  { k: 'key',     rx: /"[^"]+"\s*(?=:)/ },
  { k: 'string',  rx: /"[^"]*"/ },
  { k: 'number',  rx: /\b-?\d+\.?\d*([eE][+-]?\d+)?\b/ },
  { k: 'keyword', rx: /\b(true|false|null)\b/ },
];

function highlight(code, lang) {
  if (!code) return [];
  const rules = lang === 'yaml'   ? YAML_RULES
              : lang === 'sql'    ? SQL_RULES
              : lang === 'python' ? PYTHON_RULES
              : lang === 'json'   ? JSON_RULES
              : null;
  if (!rules) return [{ k: 'text', v: code }];
  try { return tokenise(code, rules); }
  catch { return [{ k: 'text', v: code }]; }
}

const SYN_COLORS = {
  keyword: 'var(--stage-4)',
  string:  'var(--ok)',
  comment: 'var(--faint)',
  number:  'var(--stage-6)',
  key:     'var(--stage-5)',
  op:      'var(--muted)',
  text:    'inherit',
};

// ── Inline string parser (backtick code spans in list/table strings) ──────────
function parseInlineString(str) {
  if (!str || !str.includes('`')) return [{ type: 'text', v: str || '' }];
  const parts = [];
  let last = 0;
  const rx = /`([^`]+)`/g;
  let m;
  while ((m = rx.exec(str)) !== null) {
    if (m.index > last) parts.push({ type: 'text', v: str.slice(last, m.index) });
    parts.push({ type: 'code', v: m[1], isConfigParam: false });
    last = rx.lastIndex;
  }
  if (last < str.length) parts.push({ type: 'text', v: str.slice(last) });
  return parts;
}

// ── Search helpers ────────────────────────────────────────────────────────────
function escapeRegex(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function getNodeText(node) {
  if (!node) return '';
  if (node.type === 'p') {
    if (node.text) return node.text;
    if (node.nodes) return node.nodes.map(n => n.v || '').join('');
  }
  if (node.type === 'codeblock') return node.code || '';
  if (node.type === 'list') return (node.items || []).map(item => Array.isArray(item) ? item.map(n => n.v || '').join('') : (item || '')).join('\n');
  if (node.type === 'table') {
    const rows = node.rows || [];
    return rows.map(r => r.join(' ')).join('\n');
  }
  return '';
}

function countMatches(query, plan) {
  if (!query.trim()) return { total: 0, sections: 0 };
  const rx = new RegExp(escapeRegex(query), 'gi');
  let total = 0;
  let sections = 0;
  for (const sec of plan) {
    let secHit = false;
    const hm = (sec.heading.match(rx) || []).length;
    if (hm) { total += hm; secHit = true; }
    for (const node of (sec.content || [])) {
      const text = getNodeText(node);
      const nm = (text.match(rx) || []).length;
      if (nm) { total += nm; secHit = true; }
    }
    if (secHit) sections++;
  }
  return { total, sections };
}

// ── Text highlighting (wraps matched runs in <mark>) ─────────────────────────
function HighlightText({ text, searchRx }) {
  if (!searchRx || !text) return <>{text}</>;
  const parts = text.split(searchRx);
  const hits  = text.match(searchRx) || [];
  return (
    <>
      {parts.map((part, i) => (
        <React.Fragment key={i}>
          {part}
          {i < hits.length && (
            <mark style={{
              background: 'color-mix(in srgb, var(--ochre) 45%, transparent)',
              color: 'inherit',
              borderRadius: '3px',
              padding: '0 2px',
            }}>
              {hits[i]}
            </mark>
          )}
        </React.Fragment>
      ))}
    </>
  );
}

// ── Inline nodes renderer ─────────────────────────────────────────────────────
function InlineNodes({ nodes, searchRx }) {
  return (
    <>
      {nodes.map((n, i) => {
        if (n.type === 'text') {
          return <HighlightText key={i} text={n.v} searchRx={searchRx} />;
        }
        if (n.type === 'strong') {
          return <strong key={i} style={{ fontWeight: 650, color: 'var(--ink)' }}>
            <HighlightText text={n.v} searchRx={searchRx} />
          </strong>;
        }
        if (n.type === 'code') {
          if (n.isConfigParam) {
            return (
              <code
                key={i}
                data-config-key={n.configKey}
                title="Open this setting in Configuration"
                style={{
                  fontFamily: 'var(--font-code)',
                  fontSize: '0.84em',
                  color: 'var(--accent-ink)',
                  background: 'var(--accent-soft)',
                  border: '1px solid var(--accent-line)',
                  textDecoration: 'underline',
                  textDecorationStyle: 'dotted',
                  textUnderlineOffset: '3px',
                  borderRadius: '4px',
                  padding: '1px 5px',
                }}
              >
                <HighlightText text={n.v} searchRx={searchRx} />
              </code>
            );
          }
          return (
            <code
              key={i}
              style={{
                fontFamily: 'var(--font-code)',
                fontSize: '0.84em',
                color: 'var(--ink)',
                background: 'var(--surface-sunken)',
                border: '1px solid var(--line)',
                borderRadius: '4px',
                padding: '1px 5px',
              }}
            >
              <HighlightText text={n.v} searchRx={searchRx} />
            </code>
          );
        }
        return null;
      })}
    </>
  );
}

// Render a mixed list item (string or InlineNode[])
function ListItem({ item, searchRx }) {
  if (typeof item === 'string') {
    const parts = parseInlineString(item);
    return <InlineNodes nodes={parts} searchRx={searchRx} />;
  }
  if (Array.isArray(item)) {
    return <InlineNodes nodes={item} searchRx={searchRx} />;
  }
  return null;
}

// ── Content node renderer ─────────────────────────────────────────────────────
function ContentNode({ node, searchRx }) {
  if (!node) return null;

  const anchor = node.anchor || null;
  const dataAnchor = anchor ? { 'data-anchor': anchor } : {};

  // Pipeline diagram
  if (node.type === 'pipeline-diagram') {
    return (
      <div style={{ margin: '24px 0' }}>
        <PipelineDiagram />
      </div>
    );
  }

  // Paragraph
  if (node.type === 'p') {
    return (
      <p
        {...dataAnchor}
        style={{
          margin: '0 0 16px 0',
          color: 'var(--body)',
          lineHeight: 1.75,
          scrollMarginTop: '80px',
        }}
      >
        {node.nodes
          ? <InlineNodes nodes={node.nodes} searchRx={searchRx} />
          : <HighlightText text={node.text} searchRx={searchRx} />
        }
      </p>
    );
  }

  // Code block
  if (node.type === 'codeblock') {
    const tokens = highlight(node.code, node.language);
    return (
      <pre
        {...dataAnchor}
        style={{
          fontFamily: 'var(--font-code)',
          fontSize: '12.5px',
          lineHeight: 1.65,
          background: 'var(--surface-subtle)',
          border: '1px solid var(--line)',
          borderRadius: 'var(--radius)',
          padding: '14px 16px',
          margin: '0 0 20px 0',
          overflowX: 'auto',
          whiteSpace: 'pre',
          scrollMarginTop: '80px',
        }}
      >
        <code>
          {tokens.map((tok, i) => (
            <span key={i} style={{ color: SYN_COLORS[tok.k] || 'inherit' }}>
              <HighlightText text={tok.v} searchRx={searchRx} />
            </span>
          ))}
        </code>
      </pre>
    );
  }

  // Table
  if (node.type === 'table') {
    return (
      <div
        {...dataAnchor}
        style={{ overflowX: 'auto', margin: '0 0 20px 0', scrollMarginTop: '80px' }}
      >
        <table className="tw-table" style={{
          border: '1px solid var(--line)',
          borderRadius: 'var(--radius)',
          borderCollapse: 'separate',
          borderSpacing: 0,
          overflow: 'hidden',
          fontSize: '13.5px',
          background: 'var(--surface)',
        }}>
          <thead>
            <tr>
              {node.headers.map((h, i) => (
                <th key={i} style={{ position: 'static' }}>
                  <HighlightText text={h} searchRx={searchRx} />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {node.rows.map((row, ri) => (
              <tr key={ri}>
                {row.map((cell, ci) => (
                  <td key={ci} style={{
                    color: ci === 0 ? 'var(--ink)' : 'var(--body)',
                    fontWeight: ci === 0 ? 600 : 400,
                    lineHeight: 1.55,
                    verticalAlign: 'top',
                  }}>
                    <HighlightText text={cell} searchRx={searchRx} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  // List
  if (node.type === 'list') {
    const Tag = node.ordered ? 'ol' : 'ul';
    return (
      <Tag
        {...dataAnchor}
        style={{
          margin: '0 0 16px 0',
          paddingLeft: '24px',
          lineHeight: 1.75,
          color: 'var(--body)',
          scrollMarginTop: '80px',
        }}
      >
        {node.items.map((item, i) => (
          <li key={i} style={{ marginBottom: '4px' }}>
            <ListItem item={item} searchRx={searchRx} />
          </li>
        ))}
      </Tag>
    );
  }

  return null;
}

// ── Section renderer ──────────────────────────────────────────────────────────
function SectionBlock({ section, searchRx }) {
  const { level, heading, content, stageRef, anchor } = section;
  const dataAnchor = anchor ? { 'data-anchor': anchor } : {};

  const borderColor = stageRef
    ? `var(--stage-${stageRef})`
    : 'var(--rule-accent)';

  const headingEl = (() => {
    if (level === 1) {
      return (
        <h1
          id={section.id}
          {...dataAnchor}
          style={{
            fontFamily: 'var(--font-serif)',
            fontWeight: 700,
            fontSize: '30px',
            lineHeight: 1.2,
            letterSpacing: '-0.02em',
            color: 'var(--ink)',
            marginBottom: '16px',
            paddingTop: '28px',
            borderTop: '1px solid var(--line)',
            scrollMarginTop: '24px',
          }}
        >
          <HighlightText text={heading} searchRx={searchRx} />
        </h1>
      );
    }
    if (level === 2) {
      return (
        <h2
          id={section.id}
          {...dataAnchor}
          style={{
            fontFamily: 'var(--font-sans)',
            fontWeight: 650,
            fontSize: '20px',
            lineHeight: 1.3,
            letterSpacing: '-0.015em',
            color: 'var(--ink)',
            borderLeft: `3px solid ${borderColor}`,
            paddingLeft: '12px',
            marginBottom: '12px',
            scrollMarginTop: '24px',
          }}
        >
          <HighlightText text={heading} searchRx={searchRx} />
        </h2>
      );
    }
    return (
      <h3
        id={section.id}
        {...dataAnchor}
        style={{
          fontFamily: 'var(--font-sans)',
          fontWeight: 650,
          fontSize: '16px',
          lineHeight: 1.35,
          color: 'var(--ink)',
          marginBottom: '10px',
          scrollMarginTop: '24px',
        }}
      >
        <HighlightText text={heading} searchRx={searchRx} />
      </h3>
    );
  })();

  const sectionMarginTop = level === 1 ? '40px' : level === 2 ? '30px' : '20px';

  return (
    <section style={{ marginTop: sectionMarginTop }}>
      {headingEl}
      {(content || []).map((node, i) => (
        <ContentNode key={i} node={node} searchRx={searchRx} />
      ))}
    </section>
  );
}

// ── Sidebar ───────────────────────────────────────────────────────────────────
// Build a tree structure: H1 nodes with H2 children, H2 nodes with H3 children
function buildNavTree(plan) {
  const roots = [];
  let curH1 = null;
  let curH2 = null;
  for (const sec of plan) {
    if (sec.level === 1) {
      curH1 = { ...sec, children: [] };
      curH2 = null;
      roots.push(curH1);
    } else if (sec.level === 2) {
      curH2 = { ...sec, children: [] };
      if (curH1) curH1.children.push(curH2);
    } else if (sec.level === 3) {
      const target = curH2 || curH1;
      if (target) target.children.push({ ...sec, children: [] });
    }
  }
  return roots;
}

function NavItem({ node, activeId, onNavigate, depth = 0, collapsedH2, toggleH2 }) {
  const isActive = activeId === node.id;
  const hasChildren = node.children && node.children.length > 0;
  const isH2 = node.level === 2;
  const isCollapsed = isH2 && collapsedH2.has(node.id);

  const stageColor = node.stageRef ? `var(--stage-${node.stageRef})` : null;

  return (
    <div>
      <div
        onClick={() => {
          if (isH2 && hasChildren) toggleH2(node.id);
          onNavigate(node.id);
        }}
        role="button"
        tabIndex={0}
        aria-current={isActive ? 'location' : undefined}
        onKeyDown={e => { if (e.key === 'Enter') { if (isH2 && hasChildren) toggleH2(node.id); onNavigate(node.id); } }}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          padding: `6px 10px 6px ${10 + depth * 12}px`,
          margin: '1px 8px',
          cursor: 'pointer',
          borderRadius: '6px',
          boxShadow: isActive ? 'inset 3px 0 0 var(--accent)' : 'none',
          background: isActive ? 'var(--surface-selected)' : 'transparent',
          transition: 'background 120ms',
        }}
        onMouseEnter={e => {
          if (!isActive) e.currentTarget.style.background = 'var(--surface-hover)';
        }}
        onMouseLeave={e => {
          if (!isActive) e.currentTarget.style.background = 'transparent';
        }}
      >
        {stageColor && (
          <span style={{
            display: 'inline-block',
            width: '7px',
            height: '7px',
            borderRadius: '50%',
            background: stageColor,
            flexShrink: 0,
          }} />
        )}
        <span style={{
          fontSize: node.level === 1 ? '12.5px' : '12px',
          fontWeight: node.level === 1 ? 650 : isActive ? 600 : 450,
          color: isActive ? 'var(--accent-ink)' : node.level === 1 ? 'var(--ink)' : 'var(--body)',
          lineHeight: 1.35,
          flex: 1,
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          transition: 'color 120ms',
        }}>
          {node.heading}
        </span>
        {isH2 && hasChildren && (
          <span className={`tw-caret${isCollapsed ? '' : ' is-open'}`} aria-hidden="true">
            <Icon name="chevron" />
          </span>
        )}
      </div>
      {hasChildren && !isCollapsed && (
        <div>
          {node.children.map(child => (
            <NavItem
              key={child.id}
              node={child}
              activeId={activeId}
              onNavigate={onNavigate}
              depth={depth + 1}
              collapsedH2={collapsedH2}
              toggleH2={toggleH2}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function Sidebar({ activeId, onNavigate, searchQuery, onSearch, matchInfo }) {
  const navTree = useMemo(() => buildNavTree(systemPlan), []);
  const [collapsedH2, setCollapsedH2] = useState(new Set());

  const toggleH2 = useCallback((id) => {
    setCollapsedH2(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  return (
    <div style={{
      width: '270px',
      flexShrink: 0,
      borderRight: '1px solid var(--line)',
      background: 'var(--surface-subtle)',
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      overflow: 'hidden',
    }}>
      <div style={{ padding: '14px 14px 10px', borderBottom: '1px solid var(--line-soft)', flexShrink: 0 }}>
        <SearchInput value={searchQuery} onChange={onSearch} placeholder="Search the system plan" width="100%" />
        {searchQuery.trim() && (
          <div className="tw-small tw-muted" style={{ marginTop: '6px' }}>
            {matchInfo.total > 0
              ? `${matchInfo.total} match${matchInfo.total !== 1 ? 'es' : ''} in ${matchInfo.sections} section${matchInfo.sections !== 1 ? 's' : ''}`
              : 'No matches'}
          </div>
        )}
      </div>

      <nav aria-label="System plan contents" style={{ flex: 1, overflowY: 'auto', padding: '8px 0 16px' }}>
        {navTree.map(node => (
          <NavItem
            key={node.id}
            node={node}
            activeId={activeId}
            onNavigate={onNavigate}
            collapsedH2={collapsedH2}
            toggleH2={toggleH2}
          />
        ))}
      </nav>
    </div>
  );
}

// ── Main Document component ───────────────────────────────────────────────────
export default function Document() {
  const [view, setView]                 = useState('plan');
  const [activeId, setActiveId]         = useState(systemPlan[0]?.id ?? null);
  const [searchQuery, setSearchQuery]   = useState('');
  const bodyRef                         = useRef(null);
  const observerRef                     = useRef(null);
  const navTo                           = useNavigate();

  // IntersectionObserver — update activeId as sections scroll into view
  useEffect(() => {
    const body = bodyRef.current;
    if (!body) return;

    observerRef.current?.disconnect();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setActiveId(entry.target.id);
            break;
          }
        }
      },
      {
        root: body,
        rootMargin: '-10% 0px -70% 0px',
        threshold: 0,
      }
    );
    observerRef.current = observer;

    const headings = body.querySelectorAll('h1[id], h2[id], h3[id]');
    headings.forEach(el => observer.observe(el));

    return () => observer.disconnect();
  }, []);

  // Listen for ADJUST → DOCUMENT anchor navigation
  useEffect(() => {
    const handler = (e) => {
      const anchor = e.detail;
      if (!anchor) return;
      requestAnimationFrame(() => {
        const body = bodyRef.current;
        if (!body) return;
        const el = body.querySelector(`[data-anchor="${anchor}"]`);
        if (!el) return;
        el.scrollIntoView({ behavior: 'smooth', block: 'start' });
        el.classList.add('anchor-arrival');
        setTimeout(() => el.classList.remove('anchor-arrival'), 1500);
      });
    };
    window.addEventListener('tripwire:navigate-doc', handler);
    return () => window.removeEventListener('tripwire:navigate-doc', handler);
  }, []);

  // DOCUMENT → ADJUST: click delegation on data-config-key elements
  const handleBodyClick = useCallback((e) => {
    const el = e.target.closest('[data-config-key]');
    if (!el) return;
    const configKey = el.getAttribute('data-config-key');
    if (!configKey) return;
    navTo('/adjust');
    setTimeout(() => {
      window.dispatchEvent(
        new CustomEvent('tripwire:highlight-control', { detail: configKey })
      );
    }, 100);
  }, [navTo]);

  const navigate = useCallback((id) => {
    const body = bodyRef.current;
    if (!body) return;
    const el = body.querySelector(`#${CSS.escape(id)}`);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, []);

  const searchRx = useMemo(() => {
    if (!searchQuery.trim()) return null;
    try { return new RegExp(`(${escapeRegex(searchQuery)})`, 'gi'); }
    catch { return null; }
  }, [searchQuery]);

  const matchInfo = useMemo(
    () => countMatches(searchQuery, systemPlan),
    [searchQuery]
  );

  const TABS = [
    { id: 'plan', label: 'System plan', icon: 'book' },
    { id: 'sql',  label: 'SQL console', icon: 'database' },
  ];

  return (
    <div className="tw-page is-full">
      <PageHeader
        eyebrow="Administration · Reference"
        title="How Tripwire works"
        description="The full system plan: every stage, threshold and design decision, with links into the live configuration. Use the SQL console to inspect the underlying data."
      />

      <div className="tw-card is-clip" style={{ flex: 1, minHeight: '560px', display: 'flex', flexDirection: 'column' }}>
        <div className="tw-tabs" role="tablist" style={{ padding: '0 12px', flexShrink: 0 }}>
          {TABS.map(tab => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={view === tab.id}
              className={`tw-tab${view === tab.id ? ' active' : ''}`}
              onClick={() => setView(tab.id)}
            >
              <Icon name={tab.icon} />
              {tab.label}
            </button>
          ))}
        </div>

        <div style={{ flex: 1, display: 'flex', overflow: 'hidden', minHeight: 0 }}>
          {view === 'plan' && (
            <>
              <Sidebar
                activeId={activeId}
                onNavigate={navigate}
                searchQuery={searchQuery}
                onSearch={setSearchQuery}
                matchInfo={matchInfo}
              />

              {/* Scrollable document body */}
              <div
                ref={bodyRef}
                onClick={handleBodyClick}
                className="tw-doc-body"
                style={{
                  flex: 1,
                  overflowY: 'auto',
                  padding: '0 56px 80px 48px',
                  fontSize: '15px',
                  color: 'var(--body)',
                  lineHeight: 1.75,
                }}
              >
                <div style={{ maxWidth: '860px' }}>
                  {systemPlan.map(section => (
                    <SectionBlock
                      key={section.id}
                      section={section}
                      searchRx={searchRx}
                    />
                  ))}
                </div>
              </div>
            </>
          )}

          {view === 'sql' && <SqlConsole />}
        </div>
      </div>
      <style>{`
        .tw-doc-body > div > section:first-child h1 { border-top: 0 !important; }
        .tw-doc-body [data-config-key]:hover { background: var(--accent-line) !important; }
      `}</style>
    </div>
  );
}
