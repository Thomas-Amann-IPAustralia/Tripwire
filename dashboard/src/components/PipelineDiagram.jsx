import React from 'react';

const STAGES = [
  {
    n: 1,
    label: 'STAGE 1 — METADATA PROBE',
    desc:  'Probe source headers and version IDs — cheap, runs on every source',
  },
  {
    n: 2,
    label: 'STAGE 2 — CHANGE DETECTION',
    desc:  'SHA-256 hash · word-level diff · significance fingerprint tagger',
  },
  {
    n: 3,
    label: 'STAGE 3 — DIFF GENERATION',
    desc:  'Produce .diff (webpage) · fetch ES document (FRL) · extract items (RSS)',
  },
  {
    n: 4,
    label: 'STAGE 4 — RELEVANCE SCORING',
    desc:  'YAKE-BM25 + bi-encoder cosine · weighted RRF fusion · importance multiplier',
  },
  {
    n: 5,
    label: 'STAGE 5 — BI-ENCODER MATCHING',
    desc:  'Chunk change doc · cosine vs IPFR chunk embeddings (BAAI/bge-base-en-v1.5)',
  },
  {
    n: 6,
    label: 'STAGE 6 — CROSS-ENCODER RERANKING',
    desc:  'Full-page cross-encoder · rerank with lexical + graph · propagate alerts',
  },
  {
    n: 7,
    label: 'STAGE 7 — TRIGGER AGGREGATION',
    desc:  'Group all triggers per IPFR page into a single bundle for LLM assessment',
  },
  {
    n: 8,
    label: 'STAGE 8 — LLM ASSESSMENT',
    desc:  'One LLM call per IPFR page — verdict: CHANGE_REQUIRED / NO_CHANGE / UNCERTAIN',
  },
  {
    n: 9,
    label: 'STAGE 9 — NOTIFICATION',
    desc:  'One consolidated email per run · structured feedback mailto links',
  },
];

// Box geometry
const BOX_W  = 420;
const BOX_H  = 58;
const BOX_X  = 20;
const STRIDE = 86;   // BOX_H + 28px gap
const ARROW_MID_X = BOX_X + BOX_W / 2;

// Source routing table geometry (below main flow)
const TBL_Y       = 20 + STAGES.length * STRIDE + 20;
const TBL_X       = BOX_X;
const TBL_W       = BOX_W;
const TBL_ROW_H   = 32;
const COL_WIDTHS  = [100, 105, 105, 110]; // source | stage2 | stage3 | stage4

const TOTAL_H = TBL_Y + TBL_ROW_H * 4 + 20;

function stageColor(n) {
  return `var(--stage-${n})`;
}

function StageBox({ stage, index }) {
  const y = 20 + index * STRIDE;
  return (
    <g>
      {/* Background */}
      <rect
        x={BOX_X} y={y} width={BOX_W} height={BOX_H}
        style={{ fill: 'var(--bg-tertiary)', stroke: 'var(--rule)' }}
        strokeWidth="1"
      />
      {/* Left accent border */}
      <rect
        x={BOX_X} y={y} width={4} height={BOX_H}
        style={{ fill: stageColor(stage.n) }}
      />
      {/* Stage name */}
      <text
        x={BOX_X + 14} y={y + 24}
        style={{
          fontFamily: "'Bebas Neue', sans-serif",
          fontSize: '15px',
          fill: 'var(--text-primary)',
          letterSpacing: '0.06em',
        }}
      >
        {stage.label}
      </text>
      {/* Description */}
      <text
        x={BOX_X + 14} y={y + 42}
        style={{
          fontFamily: "'DM Mono', monospace",
          fontSize: '9.5px',
          fill: 'var(--text-secondary)',
        }}
      >
        {stage.desc}
      </text>
      {/* Stage number badge */}
      <rect
        x={BOX_X + BOX_W - 30} y={y + 8} width={24} height={18}
        style={{ fill: stageColor(stage.n) }}
      />
      <text
        x={BOX_X + BOX_W - 18} y={y + 21}
        textAnchor="middle"
        style={{
          fontFamily: "'Bebas Neue', sans-serif",
          fontSize: '13px',
          fill: '#fff',
        }}
      >
        {stage.n}
      </text>
    </g>
  );
}

function Arrow({ index }) {
  const y1 = 20 + index * STRIDE + BOX_H;
  const y2 = 20 + (index + 1) * STRIDE;
  const mx  = ARROW_MID_X;
  const lineEnd = y2 - 8;
  return (
    <g>
      <line
        x1={mx} y1={y1} x2={mx} y2={lineEnd}
        stroke="var(--rule-accent)" strokeWidth="1.5"
      />
      <polygon
        points={`${mx},${y2} ${mx - 6},${lineEnd} ${mx + 6},${lineEnd}`}
        style={{ fill: 'var(--rule-accent)' }}
      />
    </g>
  );
}

// Simple SVG text that wraps at maxW characters
function SvgText({ x, y, text, style }) {
  return (
    <text x={x} y={y} style={style}>{text}</text>
  );
}

function RoutingTable() {
  const headers = ['SOURCE TYPE', 'STAGE 2', 'STAGE 3', 'STAGE 4'];
  const rows = [
    ['Webpage',  '3-pass detection',    '.diff file',       'Diff → BM25+emb'],
    ['FRL',      'Skipped (structured)','Change explainer',  'Explainer → BM25+emb'],
    ['RSS Feed', 'Skipped (new items)', 'Extract new items', 'Items → BM25+emb'],
  ];

  const totalColW = COL_WIDTHS.reduce((a, b) => a + b, 0);

  // column x offsets
  const colX = COL_WIDTHS.reduce((acc, w, i) => {
    acc.push(i === 0 ? TBL_X : acc[i - 1] + COL_WIDTHS[i - 1]);
    return acc;
  }, []);

  return (
    <g>
      {/* Section label above table */}
      <text
        x={TBL_X} y={TBL_Y - 10}
        style={{
          fontFamily: "'Bebas Neue', sans-serif",
          fontSize: '12px',
          fill: 'var(--text-secondary)',
          letterSpacing: '0.1em',
        }}
      >
        SOURCE-TYPE ROUTING
      </text>

      {/* Outer border */}
      <rect
        x={TBL_X} y={TBL_Y} width={totalColW} height={TBL_ROW_H * 4}
        fill="none" stroke="var(--rule)" strokeWidth="1"
      />

      {/* Header row background */}
      <rect
        x={TBL_X} y={TBL_Y} width={totalColW} height={TBL_ROW_H}
        style={{ fill: 'var(--bg-accent)' }}
      />

      {/* Column dividers */}
      {colX.slice(1).map((x, i) => (
        <line
          key={i}
          x1={x} y1={TBL_Y}
          x2={x} y2={TBL_Y + TBL_ROW_H * 4}
          stroke="var(--rule)" strokeWidth="1"
        />
      ))}

      {/* Row dividers */}
      {[1, 2, 3].map(r => (
        <line
          key={r}
          x1={TBL_X} y1={TBL_Y + r * TBL_ROW_H}
          x2={TBL_X + totalColW} y2={TBL_Y + r * TBL_ROW_H}
          stroke="var(--rule)" strokeWidth="1"
        />
      ))}

      {/* Header text */}
      {headers.map((h, i) => (
        <text
          key={i}
          x={colX[i] + 6} y={TBL_Y + 20}
          style={{
            fontFamily: "'Bebas Neue', sans-serif",
            fontSize: '10px',
            fill: 'var(--text-secondary)',
            letterSpacing: '0.08em',
          }}
        >
          {h}
        </text>
      ))}

      {/* Data rows */}
      {rows.map((row, ri) =>
        row.map((cell, ci) => (
          <text
            key={`${ri}-${ci}`}
            x={colX[ci] + 6}
            y={TBL_Y + (ri + 1) * TBL_ROW_H + 20}
            style={{
              fontFamily: "'DM Mono', monospace",
              fontSize: '9px',
              fill: ci === 0 ? 'var(--text-primary)' : 'var(--text-secondary)',
            }}
          >
            {cell}
          </text>
        ))
      )}
    </g>
  );
}

export default function PipelineDiagram() {
  return (
    <svg
      viewBox={`0 0 460 ${TOTAL_H}`}
      width="100%"
      style={{ display: 'block', maxWidth: '520px', margin: '0 auto' }}
      aria-label="Tripwire nine-stage pipeline diagram"
    >
      {STAGES.map((stage, i) => (
        <StageBox key={stage.n} stage={stage} index={i} />
      ))}
      {STAGES.slice(0, -1).map((_, i) => (
        <Arrow key={i} index={i} />
      ))}
      <RoutingTable />
    </svg>
  );
}
