import React from 'react';

const STAGES = [
  {
    n: 1,
    label: 'Metadata probe',
    desc:  'Probe source headers and version IDs; cheap, runs on every source',
  },
  {
    n: 2,
    label: 'Change detection',
    desc:  'SHA-256 hash · word-level diff · significance fingerprint tagger',
  },
  {
    n: 3,
    label: 'Diff generation',
    desc:  'Produce .diff (webpage) · fetch ES document (FRL) · extract items (RSS)',
  },
  {
    n: 4,
    label: 'Relevance scoring',
    desc:  'YAKE-BM25 + bi-encoder cosine · weighted RRF fusion · importance multiplier',
  },
  {
    n: 5,
    label: 'Bi-encoder matching',
    desc:  'Chunk change doc · cosine vs IPFR chunk embeddings (BAAI/bge-base-en-v1.5)',
  },
  {
    n: 6,
    label: 'Cross-encoder reranking',
    desc:  'Full-page cross-encoder · rerank with lexical + graph · propagate alerts',
  },
  {
    n: 7,
    label: 'Trigger aggregation',
    desc:  'Group all triggers per IPFR page into a single bundle for LLM assessment',
  },
  {
    n: 8,
    label: 'LLM assessment',
    desc:  'One LLM call per IPFR page; verdict is change required, no change or uncertain',
  },
  {
    n: 9,
    label: 'Notification',
    desc:  'One consolidated email per run · structured feedback mailto links',
  },
];

// Box geometry
const BOX_W  = 440;
const BOX_H  = 60;
const BOX_X  = 20;
const STRIDE = 86;   // BOX_H + 28px gap

// Source routing table geometry (below main flow)
const TBL_Y       = 20 + STAGES.length * STRIDE + 20;
const TBL_X       = BOX_X;
const TBL_W       = BOX_W;
const TBL_ROW_H   = 32;
const COL_WIDTHS  = [96, 112, 112, 120]; // source | stage2 | stage3 | stage4

const TOTAL_H = TBL_Y + TBL_ROW_H * 4 + 24;

function stageColor(n) {
  return `var(--stage-${n})`;
}

const SANS = 'var(--font-sans)';

function StageBox({ stage, index }) {
  const y = 20 + index * STRIDE;
  return (
    <g>
      <rect
        x={BOX_X} y={y} width={BOX_W} height={BOX_H} rx={10}
        style={{ fill: 'var(--surface)', stroke: 'var(--line)' }}
        strokeWidth="1"
      />
      {/* Stage number disc */}
      <circle cx={BOX_X + 26} cy={y + BOX_H / 2} r={14} style={{ fill: `color-mix(in srgb, ${stageColor(stage.n)} 16%, var(--surface))`, stroke: stageColor(stage.n) }} strokeWidth="1.5" />
      <text
        x={BOX_X + 26} y={y + BOX_H / 2 + 4.5}
        textAnchor="middle"
        style={{ fontFamily: SANS, fontSize: '13px', fontWeight: 700, fill: stageColor(stage.n) }}
      >
        {stage.n}
      </text>
      <text
        x={BOX_X + 52} y={y + 25}
        style={{ fontFamily: SANS, fontSize: '13.5px', fontWeight: 650, fill: 'var(--ink)' }}
      >
        <tspan style={{ fill: 'var(--muted)', fontWeight: 600 }}>Stage {stage.n} · </tspan>{stage.label}
      </text>
      <text
        x={BOX_X + 52} y={y + 43}
        style={{ fontFamily: SANS, fontSize: '10.5px', fill: 'var(--muted)' }}
      >
        {stage.desc}
      </text>
    </g>
  );
}

function Arrow({ index }) {
  const y1 = 20 + index * STRIDE + BOX_H;
  const y2 = 20 + (index + 1) * STRIDE;
  const mx  = BOX_X + 26;
  const lineEnd = y2 - 7;
  return (
    <g>
      <line
        x1={mx} y1={y1 + 2} x2={mx} y2={lineEnd}
        style={{ stroke: 'var(--line-strong)' }} strokeWidth="1.5" strokeDasharray="3 3"
      />
      <polygon
        points={`${mx},${y2 - 1} ${mx - 5},${lineEnd} ${mx + 5},${lineEnd}`}
        style={{ fill: 'var(--line-strong)' }}
      />
    </g>
  );
}

function RoutingTable() {
  const headers = ['Source type', 'Stage 2', 'Stage 3', 'Stage 4'];
  const rows = [
    ['Web page', '3-pass detection',     '.diff file',        'Diff → BM25 + emb.'],
    ['FRL',      'Skipped (structured)', 'Change explainer',  'Explainer → BM25 + emb.'],
    ['RSS feed', 'Skipped (new items)',  'Extract new items', 'Items → BM25 + emb.'],
  ];

  const totalColW = COL_WIDTHS.reduce((a, b) => a + b, 0);

  const colX = COL_WIDTHS.reduce((acc, w, i) => {
    acc.push(i === 0 ? TBL_X : acc[i - 1] + COL_WIDTHS[i - 1]);
    return acc;
  }, []);

  return (
    <g>
      <text x={TBL_X} y={TBL_Y - 10} style={{ fontFamily: SANS, fontSize: '12.5px', fontWeight: 650, fill: 'var(--ink)' }}>
        Source-type routing
      </text>

      <rect
        x={TBL_X} y={TBL_Y} width={totalColW} height={TBL_ROW_H * 4} rx={8}
        style={{ fill: 'var(--surface)', stroke: 'var(--line)' }} strokeWidth="1"
      />
      <path
        d={`M${TBL_X},${TBL_Y + TBL_ROW_H} V${TBL_Y + 8} a8,8 0 0 1 8,-8 H${TBL_X + totalColW - 8} a8,8 0 0 1 8,8 V${TBL_Y + TBL_ROW_H} Z`}
        style={{ fill: 'var(--surface-sunken)' }}
      />

      {[1, 2, 3].map(r => (
        <line
          key={r}
          x1={TBL_X} y1={TBL_Y + r * TBL_ROW_H}
          x2={TBL_X + totalColW} y2={TBL_Y + r * TBL_ROW_H}
          style={{ stroke: 'var(--line)' }} strokeWidth="1"
        />
      ))}

      {headers.map((h, i) => (
        <text key={i} x={colX[i] + 10} y={TBL_Y + 20}
          style={{ fontFamily: SANS, fontSize: '10.5px', fontWeight: 700, fill: 'var(--muted)' }}>
          {h}
        </text>
      ))}

      {rows.map((row, ri) =>
        row.map((cell, ci) => (
          <text
            key={`${ri}-${ci}`}
            x={colX[ci] + 10}
            y={TBL_Y + (ri + 1) * TBL_ROW_H + 20}
            style={{ fontFamily: SANS, fontSize: '10.5px', fontWeight: ci === 0 ? 650 : 400, fill: ci === 0 ? 'var(--ink)' : 'var(--body)' }}
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
      viewBox={`0 0 480 ${TOTAL_H}`}
      width="100%"
      style={{ display: 'block', maxWidth: '560px', margin: '0 auto' }}
      role="img"
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
