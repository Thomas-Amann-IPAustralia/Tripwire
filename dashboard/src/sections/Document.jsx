import React, {
  useState, useEffect, useRef, useCallback, useMemo,
} from 'react';

// ── SQL Console ───────────────────────────────────────────────────────────────
const KNOWN_TABLES = [
  'pages', 'page_chunks', 'entities', 'keyphrases',
  'graph_edges', 'sections', 'pipeline_runs', 'deferred_triggers',
];

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
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', padding: '20px 24px 16px' }}>

      {/* Table chips */}
      <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', alignItems: 'center', marginBottom: '12px' }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)', flexShrink: 0 }}>
          Tables:
        </span>
        {KNOWN_TABLES.map(t => (
          <code
            key={t}
            title={`SELECT * FROM ${t} LIMIT 20`}
            onClick={() => setSql(`SELECT * FROM ${t} LIMIT 20`)}
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '10px',
              color: 'var(--text-secondary)',
              background: 'var(--bg-tertiary)',
              border: '1px solid var(--rule)',
              padding: '2px 6px',
              cursor: 'pointer',
            }}
          >
            {t}
          </code>
        ))}
      </div>

      {/* Textarea */}
      <textarea
        value={sql}
        onChange={e => setSql(e.target.value)}
        onKeyDown={handleKeyDown}
        spellCheck={false}
        placeholder="SELECT …"
        style={{
          width: '100%',
          height: '120px',
          minHeight: '60px',
          resize: 'vertical',
          fontFamily: 'var(--font-mono)',
          fontSize: '13px',
          lineHeight: 1.5,
          background: 'var(--bg-tertiary)',
          border: '1px solid var(--rule)',
          color: 'var(--text-primary)',
          padding: '10px 12px',
          outline: 'none',
          boxSizing: 'border-box',
        }}
      />

      {/* Run bar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', margin: '10px 0 14px' }}>
        <button
          onClick={run}
          disabled={loading}
          style={{
            fontFamily: 'var(--font-display)',
            fontSize: '11px',
            letterSpacing: '0.08em',
            background: loading ? 'var(--bg-accent)' : 'var(--stage-4)',
            color: loading ? 'var(--text-tertiary)' : '#000',
            border: 'none',
            padding: '6px 18px',
            cursor: loading ? 'not-allowed' : 'pointer',
          }}
        >
          {loading ? 'RUNNING…' : 'RUN'}
        </button>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)' }}>
          Ctrl+Enter · SELECT only · read-only
        </span>
        {result && timing != null && (
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-secondary)', marginLeft: 'auto' }}>
            {result.rowCount.toLocaleString()} row{result.rowCount !== 1 ? 's' : ''} · {timing}ms
          </span>
        )}
      </div>

      {/* Row-count warning */}
      {result?.rowWarning && (
        <div style={{
          background: 'rgba(251,191,36,0.08)',
          border: '1px solid rgba(251,191,36,0.35)',
          color: 'var(--state-warn)',
          fontFamily: 'var(--font-mono)',
          fontSize: '11px',
          padding: '8px 12px',
          marginBottom: '12px',
        }}>
          ⚠ {result.rowWarning}
        </div>
      )}

      {/* Error */}
      {error && (
        <div style={{
          background: 'rgba(239,68,68,0.08)',
          border: '1px solid var(--state-error)',
          color: 'var(--state-error)',
          fontFamily: 'var(--font-mono)',
          fontSize: '12px',
          padding: '10px 12px',
          marginBottom: '12px',
          whiteSpace: 'pre-wrap',
        }}>
          {error}
        </div>
      )}

      {/* Results table */}
      {result && (
        <div style={{ flex: 1, overflowY: 'auto', overflowX: 'auto', border: '1px solid var(--rule)' }}>
          <table style={{
            borderCollapse: 'collapse',
            fontFamily: 'var(--font-mono)',
            fontSize: '12px',
            width: 'max-content',
            minWidth: '100%',
          }}>
            <thead>
              <tr>
                {result.columns.map((col, i) => (
                  <th key={i} style={{
                    fontFamily: 'var(--font-display)',
                    fontSize: '10px',
                    letterSpacing: '0.07em',
                    textTransform: 'uppercase',
                    color: 'var(--text-secondary)',
                    padding: '6px 10px',
                    borderBottom: '2px solid var(--rule)',
                    borderRight: i < result.columns.length - 1 ? '1px solid var(--rule)' : 'none',
                    background: 'var(--bg-secondary)',
                    textAlign: 'left',
                    whiteSpace: 'nowrap',
                    position: 'sticky',
                    top: 0,
                    zIndex: 1,
                  }}>
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.rows.length === 0 ? (
                <tr>
                  <td
                    colSpan={result.columns.length || 1}
                    style={{ padding: '24px', textAlign: 'center', color: 'var(--text-tertiary)', fontStyle: 'italic' }}
                  >
                    No rows returned
                  </td>
                </tr>
              ) : (
                result.rows.map((row, ri) => (
                  <tr key={ri} style={{ background: ri % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.015)' }}>
                    {row.map((cell, ci) => (
                      <td key={ci} style={{
                        padding: '5px 10px',
                        borderBottom: '1px solid var(--rule)',
                        borderRight: ci < row.length - 1 ? '1px solid var(--rule)' : 'none',
                        color: cell === null ? 'var(--text-tertiary)' : 'var(--text-primary)',
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
import { useNavigate } from 'react-router-dom';
import { systemPlan } from '../lib/systemPlan.js';
import PipelineDiagram from '../components/PipelineDiagram.jsx';

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
  string:  'var(--stage-9)',
  comment: 'var(--text-tertiary)',
  number:  'var(--stage-3)',
  key:     'var(--stage-5)',
  op:      'var(--text-secondary)',
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
              background: 'rgba(212,168,32,0.32)',
              color: 'inherit',
              borderRadius: '1px',
              padding: '0 1px',
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
          return <strong key={i} style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
            <HighlightText text={n.v} searchRx={searchRx} />
          </strong>;
        }
        if (n.type === 'code') {
          if (n.isConfigParam) {
            return (
              <code
                key={i}
                data-config-key={n.configKey}
                style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: '0.88em',
                  color: 'var(--stage-3)',
                  textDecoration: 'underline',
                  textDecorationStyle: 'dotted',
                  textUnderlineOffset: '3px',
                  cursor: 'default',
                  borderRadius: '2px',
                  padding: '0 2px',
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
                fontFamily: 'var(--font-mono)',
                fontSize: '0.88em',
                color: 'var(--text-primary)',
                background: 'var(--bg-tertiary)',
                borderRadius: '2px',
                padding: '1px 4px',
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
          color: 'var(--text-primary)',
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
          fontFamily: 'var(--font-mono)',
          fontSize: '12px',
          lineHeight: 1.6,
          background: 'var(--bg-tertiary)',
          border: '1px solid var(--rule)',
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
        <table style={{
          width: '100%',
          borderCollapse: 'collapse',
          border: '1px solid var(--rule)',
          fontFamily: 'var(--font-body)',
          fontSize: '14px',
        }}>
          <thead>
            <tr>
              {node.headers.map((h, i) => (
                <th key={i} style={{
                  fontFamily: 'var(--font-display)',
                  fontSize: '11px',
                  letterSpacing: '0.08em',
                  textTransform: 'uppercase',
                  color: 'var(--text-secondary)',
                  padding: '8px 12px',
                  borderBottom: '1px solid var(--rule)',
                  borderRight: i < node.headers.length - 1 ? '1px solid var(--rule)' : 'none',
                  background: 'var(--bg-secondary)',
                  textAlign: 'left',
                  whiteSpace: 'nowrap',
                }}>
                  <HighlightText text={h} searchRx={searchRx} />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {node.rows.map((row, ri) => (
              <tr key={ri} style={{
                background: ri % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.015)',
              }}>
                {row.map((cell, ci) => (
                  <td key={ci} style={{
                    padding: '8px 12px',
                    borderBottom: '1px solid var(--rule)',
                    borderRight: ci < row.length - 1 ? '1px solid var(--rule)' : 'none',
                    color: 'var(--text-primary)',
                    lineHeight: 1.5,
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
          color: 'var(--text-primary)',
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
            fontFamily: 'var(--font-display)',
            fontSize: '42px',
            lineHeight: 1,
            letterSpacing: '0.03em',
            color: 'var(--text-primary)',
            marginBottom: '20px',
            scrollMarginTop: '80px',
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
            fontFamily: 'var(--font-display)',
            fontSize: '28px',
            lineHeight: 1,
            letterSpacing: '0.03em',
            color: 'var(--text-primary)',
            borderLeft: `3px solid ${borderColor}`,
            paddingLeft: '14px',
            marginBottom: '16px',
            scrollMarginTop: '80px',
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
          fontFamily: 'var(--font-display)',
          fontSize: '20px',
          lineHeight: 1,
          letterSpacing: '0.03em',
          color: 'var(--text-secondary)',
          marginBottom: '12px',
          scrollMarginTop: '80px',
        }}
      >
        <HighlightText text={heading} searchRx={searchRx} />
      </h3>
    );
  })();

  const sectionMarginTop = level === 1 ? '48px' : level === 2 ? '32px' : '20px';

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
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          padding: `5px 12px 5px ${12 + depth * 12}px`,
          cursor: 'pointer',
          borderLeft: isActive ? '2px solid var(--stage-3)' : '2px solid transparent',
          background: isActive ? 'rgba(212,168,32,0.07)' : 'transparent',
          transition: 'background 120ms',
        }}
        onMouseEnter={e => {
          if (!isActive) e.currentTarget.style.background = 'rgba(255,255,255,0.03)';
        }}
        onMouseLeave={e => {
          if (!isActive) e.currentTarget.style.background = 'transparent';
        }}
      >
        {stageColor && (
          <span style={{
            display: 'inline-block',
            width: '6px',
            height: '6px',
            borderRadius: '1px',
            background: stageColor,
            flexShrink: 0,
          }} />
        )}
        <span style={{
          fontFamily: 'var(--font-mono)',
          fontSize: node.level === 1 ? '11px' : '10px',
          color: isActive ? 'var(--text-primary)' : node.level === 1 ? 'var(--text-secondary)' : 'var(--text-tertiary)',
          letterSpacing: node.level === 1 ? '0.05em' : '0.02em',
          lineHeight: 1.3,
          flex: 1,
          textTransform: node.level === 1 ? 'uppercase' : 'none',
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          transition: 'color 120ms',
        }}>
          {node.heading}
        </span>
        {isH2 && hasChildren && (
          <span style={{
            fontFamily: 'var(--font-mono)',
            fontSize: '9px',
            color: 'var(--text-tertiary)',
            transform: isCollapsed ? 'none' : 'rotate(90deg)',
            transition: 'transform 150ms',
            display: 'inline-block',
            flexShrink: 0,
          }}>›</span>
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
      width: '240px',
      flexShrink: 0,
      borderRight: '1px solid var(--rule)',
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      overflow: 'hidden',
    }}>
      {/* Search */}
      <div style={{
        padding: '10px 12px',
        borderBottom: '1px solid var(--rule)',
        flexShrink: 0,
      }}>
        <input
          type="text"
          value={searchQuery}
          onChange={e => onSearch(e.target.value)}
          placeholder="Search document…"
          style={{
            width: '100%',
            background: 'var(--bg-tertiary)',
            border: '1px solid var(--rule)',
            color: 'var(--text-primary)',
            fontFamily: 'var(--font-mono)',
            fontSize: '10px',
            padding: '6px 8px',
            outline: 'none',
            boxSizing: 'border-box',
          }}
        />
        {searchQuery.trim() && (
          <div style={{
            fontFamily: 'var(--font-mono)',
            fontSize: '9px',
            color: 'var(--text-tertiary)',
            marginTop: '5px',
            letterSpacing: '0.03em',
          }}>
            {matchInfo.total > 0
              ? `${matchInfo.total} match${matchInfo.total !== 1 ? 'es' : ''} in ${matchInfo.sections} section${matchInfo.sections !== 1 ? 's' : ''}`
              : 'No matches'}
          </div>
        )}
      </div>

      {/* Nav tree */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '8px 0' }}>
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
      </div>
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

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* ── Header band ── */}
      <div style={{
        height: '80px', minHeight: '80px',
        display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between',
        padding: '0 24px 0',
        borderBottom: '1px solid var(--rule)',
      }}>
        <div style={{ paddingBottom: '12px' }}>
          <div style={{
            fontFamily: 'var(--font-display)',
            fontSize: '42px',
            lineHeight: 1,
            letterSpacing: '0.04em',
          }}>
            DOCUMENT
          </div>
          <div style={{
            fontFamily: 'var(--font-mono)',
            fontSize: '11px',
            color: 'var(--text-secondary)',
            marginTop: '2px',
          }}>
            Tripwire System Plan
          </div>
        </div>

        {/* View tabs */}
        <div style={{ display: 'flex', alignSelf: 'flex-end' }}>
          {[
            { id: 'plan', label: 'SYSTEM PLAN' },
            { id: 'sql',  label: 'SQL CONSOLE' },
          ].map(tab => (
            <button
              key={tab.id}
              onClick={() => setView(tab.id)}
              style={{
                fontFamily: 'var(--font-display)',
                fontSize: '11px',
                letterSpacing: '0.07em',
                background: 'none',
                border: 'none',
                borderBottom: view === tab.id ? '2px solid var(--stage-4)' : '2px solid transparent',
                color: view === tab.id ? 'var(--text-primary)' : 'var(--text-tertiary)',
                padding: '8px 16px',
                cursor: 'pointer',
                transition: 'color 120ms, border-color 120ms',
              }}
              onMouseEnter={e => { if (view !== tab.id) e.currentTarget.style.color = 'var(--text-secondary)'; }}
              onMouseLeave={e => { if (view !== tab.id) e.currentTarget.style.color = 'var(--text-tertiary)'; }}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* ── Body ── */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
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
              style={{
                flex: 1,
                overflowY: 'auto',
                padding: '8px 52px 80px 48px',
                fontFamily: 'var(--font-body)',
                fontSize: '16px',
                color: 'var(--text-primary)',
                lineHeight: 1.75,
              }}
            >
              {systemPlan.map(section => (
                <SectionBlock
                  key={section.id}
                  section={section}
                  searchRx={searchRx}
                />
              ))}
            </div>
          </>
        )}

        {view === 'sql' && <SqlConsole />}
      </div>
    </div>
  );
}
