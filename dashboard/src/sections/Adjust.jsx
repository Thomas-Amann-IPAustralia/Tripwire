import React, { useState, useMemo, useCallback } from 'react';
import yaml from 'js-yaml';
import { useQueryClient } from '@tanstack/react-query';
import { useConfig as useConfigData } from '../hooks/useData.js';
import ConfigControl from '../components/ConfigControl.jsx';
import ThresholdSimulator from '../visualisations/ThresholdSimulator.jsx';

// ── Nested config helpers ─────────────────────────────────────────────────────
function getPath(obj, dotPath) {
  return dotPath.split('.').reduce((acc, k) => acc?.[k], obj);
}

function setPath(obj, dotPath, value) {
  const keys = dotPath.split('.');
  const clone = JSON.parse(JSON.stringify(obj ?? {}));
  let cur = clone;
  for (let i = 0; i < keys.length - 1; i++) {
    if (cur[keys[i]] == null) cur[keys[i]] = {};
    cur = cur[keys[i]];
  }
  cur[keys[keys.length - 1]] = value;
  return clone;
}

function mergeStaged(config, staged) {
  let result = JSON.parse(JSON.stringify(config ?? {}));
  for (const [path, val] of Object.entries(staged)) {
    result = setPath(result, path, val);
  }
  return result;
}

// ── YAML line diff: which lines changed? ─────────────────────────────────────
function getChangedLines(original, updated) {
  const origLines  = yaml.dump(original  ?? {}).split('\n');
  const mergedLines = yaml.dump(updated ?? {}).split('\n');
  const changed = new Set();
  mergedLines.forEach((line, i) => {
    if (line !== origLines[i]) changed.add(i);
  });
  return { lines: mergedLines, changed };
}

// ── Section definitions ────────────────────────────────────────────────────────
const SECTIONS = [
  {
    id: 'pipeline',
    title: 'PIPELINE BEHAVIOUR',
    params: [
      { key: 'pipeline.observation_mode', label: 'Observation Mode', controlType: 'toggle',
        infoText: 'When ON, the pipeline runs all stages but skips LLM calls and sends no alerts. Use during the initial calibration period.',
        docAnchor: 'doc-observation-mode' },
      { key: 'pipeline.run_frequency_hours', label: 'Run Frequency (hours)', controlType: 'number', min: 1, max: 168, step: 1,
        infoText: 'How often the pipeline runs, in hours. Default 24.',
        docAnchor: 'doc-run-frequency' },
      { key: 'pipeline.max_retries', label: 'Max Retries', controlType: 'number', min: 0, max: 5, step: 1,
        infoText: 'How many times a transient failure is retried before the source is skipped.',
        docAnchor: 'doc-retries' },
      { key: 'pipeline.retry_base_delay_seconds', label: 'Retry Base Delay (s)', controlType: 'number', min: 0.5, max: 30, step: 0.5,
        infoText: 'The base delay for the first retry. Each subsequent retry doubles this value plus random jitter.',
        docAnchor: 'doc-retry-backoff' },
      { key: 'pipeline.llm_temperature', label: 'LLM Temperature', controlType: 'slider', min: 0, max: 1, step: 0.05,
        infoText: 'Controls output randomness for the LLM assessment call. Default 0.2.',
        docAnchor: 'doc-llm-temperature' },
      { key: 'pipeline.llm_model', label: 'LLM Model', controlType: 'text',
        infoText: 'The model identifier passed to the LLM API, e.g. gpt-4o.',
        docAnchor: 'doc-llm-model' },
      { key: 'pipeline.deferred_trigger_max_age_days', label: 'Deferred Trigger Max Age (days)', controlType: 'number', min: 1, max: 30, step: 1,
        infoText: 'How long a deferred trigger is held before being discarded.',
        docAnchor: 'doc-deferred-triggers' },
    ],
  },
  {
    id: 'change_detection',
    title: 'CHANGE DETECTION — STAGE 2',
    params: [
      { key: 'change_detection.significance_fingerprint', label: 'Significance Fingerprint', controlType: 'toggle',
        infoText: 'When ON, Stage 2 uses spaCy and regex to classify changes as high or standard significance.',
        docAnchor: 'doc-significance-fingerprint' },
    ],
  },
  {
    id: 'relevance',
    title: 'RELEVANCE SCORING — STAGE 4',
    params: [
      { key: 'relevance_scoring.rrf_k', label: 'RRF K', controlType: 'number', min: 10, max: 200, step: 5,
        infoText: 'Smoothing constant in the Reciprocal Rank Fusion formula. Default 60.',
        docAnchor: 'doc-rrf-k' },
      { key: 'relevance_scoring.rrf_weight_bm25', label: 'RRF Weight BM25', controlType: 'number', min: 0, max: 5, step: 0.1,
        infoText: 'Weight of the BM25 keyword signal in RRF fusion.',
        docAnchor: 'doc-rrf-weights' },
      { key: 'relevance_scoring.rrf_weight_semantic', label: 'RRF Weight Semantic', controlType: 'number', min: 0, max: 5, step: 0.1,
        infoText: 'Weight of the bi-encoder semantic similarity signal in RRF fusion. Default 2.0.',
        docAnchor: 'doc-rrf-weights' },
      { key: 'relevance_scoring.top_n_candidates', label: 'Top N Candidates', controlType: 'number', min: 1, max: 20, step: 1,
        infoText: 'Minimum number of IPFR pages forwarded to semantic matching.',
        docAnchor: 'doc-top-n' },
      { key: 'relevance_scoring.min_score_threshold', label: 'Min Score Threshold', controlType: 'number', min: 0, max: 1, step: 0.01, nullable: true,
        infoText: 'Floor score for inclusion beyond the top-N. Null during calibration.',
        docAnchor: 'doc-min-score-threshold' },
      { key: 'relevance_scoring.source_importance_floor', label: 'Source Importance Floor', controlType: 'slider', min: 0, max: 1, step: 0.05,
        infoText: 'The minimum multiplier applied to any source, regardless of importance.',
        docAnchor: 'doc-importance-floor' },
      { key: 'relevance_scoring.fast_pass.source_importance_min', label: 'Fast-Pass Importance Min', controlType: 'slider', min: 0, max: 1, step: 0.05,
        infoText: 'Sources at or above this importance bypass Stage 4 fusion.',
        docAnchor: 'doc-fast-pass' },
      { key: 'relevance_scoring.yake.keyphrases_per_80_words', label: 'YAKE Keyphrases / 80 Words', controlType: 'number', min: 1, max: 5, step: 1,
        infoText: 'Rate of YAKE keyphrase extraction from diffs.',
        docAnchor: 'doc-yake' },
      { key: 'relevance_scoring.yake.min_keyphrases', label: 'YAKE Min Keyphrases', controlType: 'number', min: 1, max: 10, step: 1,
        infoText: 'Minimum keyphrases extracted.',
        docAnchor: 'doc-yake' },
      { key: 'relevance_scoring.yake.max_keyphrases', label: 'YAKE Max Keyphrases', controlType: 'number', min: 5, max: 30, step: 1,
        infoText: 'Maximum keyphrases extracted.',
        docAnchor: 'doc-yake' },
      { key: 'relevance_scoring.yake.short_diff_word_threshold', label: 'YAKE Short Diff Threshold', controlType: 'number', min: 10, max: 200, step: 5,
        infoText: 'Diffs shorter than this word count are supplemented with NER entities.',
        docAnchor: 'doc-yake' },
    ],
  },
  {
    id: 'semantic',
    title: 'SEMANTIC SCORING — STAGES 5–6',
    params: [
      { key: 'semantic_scoring.biencoder.model', label: 'Bi-Encoder Model', controlType: 'text', locked: true,
        lockWarning: 'Changing this model invalidates all stored embeddings and requires a full re-ingestion.',
        infoText: 'Hugging Face model ID for the bi-encoder. Changing this invalidates all stored embeddings.',
        docAnchor: 'doc-biencoder' },
      { key: 'semantic_scoring.biencoder.high_threshold', label: 'Bi-Encoder High Threshold', controlType: 'slider', min: 0, max: 1, step: 0.01,
        infoText: 'A single chunk scoring above this cosine similarity triggers the IPFR page.',
        docAnchor: 'doc-biencoder-thresholds' },
      { key: 'semantic_scoring.biencoder.low_medium_threshold', label: 'Bi-Encoder Low/Med Threshold', controlType: 'slider', min: 0, max: 1, step: 0.01,
        infoText: 'The lower threshold used in the multi-chunk candidate trigger rule.',
        docAnchor: 'doc-biencoder-thresholds' },
      { key: 'semantic_scoring.biencoder.low_medium_min_chunks', label: 'Bi-Encoder Low/Med Min Chunks', controlType: 'number', min: 1, max: 10, step: 1,
        infoText: 'Number of chunks that must exceed the low-medium threshold to trigger.',
        docAnchor: 'doc-biencoder-thresholds' },
      { key: 'semantic_scoring.crossencoder.model', label: 'Cross-Encoder Model', controlType: 'text', locked: true,
        lockWarning: 'Changing this model invalidates all stored embeddings and requires a full re-ingestion.',
        infoText: 'Hugging Face model ID for the cross-encoder reranker.',
        docAnchor: 'doc-crossencoder' },
      { key: 'semantic_scoring.crossencoder.threshold', label: 'Cross-Encoder Threshold', controlType: 'slider', min: 0, max: 1, step: 0.01,
        infoText: 'Minimum cross-encoder score for a candidate to proceed to LLM assessment.',
        docAnchor: 'doc-crossencoder-threshold' },
      { key: 'semantic_scoring.crossencoder.max_context_tokens', label: 'Cross-Encoder Max Context Tokens', controlType: 'number', min: 512, max: 16384, step: 512,
        infoText: 'Maximum combined token count passed to the cross-encoder.',
        docAnchor: 'doc-crossencoder-context' },
    ],
  },
  {
    id: 'graph',
    title: 'GRAPH PROPAGATION — STAGE 6',
    params: [
      { key: 'graph.enabled', label: 'Graph Enabled', controlType: 'toggle',
        infoText: 'Enables alert propagation through the quasi-graph.',
        docAnchor: 'doc-graph' },
      { key: 'graph.max_hops', label: 'Max Hops', controlType: 'number', min: 1, max: 5, step: 1,
        infoText: 'Maximum hops a propagated alert can travel.',
        docAnchor: 'doc-graph-hops' },
      { key: 'graph.decay_per_hop', label: 'Decay Per Hop', controlType: 'slider', min: 0, max: 1, step: 0.01,
        infoText: 'Signal fraction retained at each hop.',
        docAnchor: 'doc-graph-decay' },
      { key: 'graph.propagation_threshold', label: 'Propagation Threshold', controlType: 'slider', min: 0, max: 0.5, step: 0.005,
        infoText: 'Propagation stops when decayed signal falls below this floor.',
        docAnchor: 'doc-graph-threshold' },
      { key: 'graph.edge_types.embedding_similarity.enabled', label: 'Embedding Edges Enabled', controlType: 'toggle',
        infoText: 'Enable/disable embedding-similarity edges in the graph.',
        docAnchor: 'doc-graph-edges' },
      { key: 'graph.edge_types.embedding_similarity.weight', label: 'Embedding Edge Weight', controlType: 'slider', min: 0, max: 1, step: 0.05,
        infoText: 'Scaling factor applied to embedding-similarity edge weights.',
        docAnchor: 'doc-graph-edges' },
      { key: 'graph.edge_types.embedding_similarity.top_k', label: 'Embedding Edge Top K', controlType: 'number', min: 1, max: 20, step: 1,
        infoText: 'Each page retains edges to its top-K most similar neighbours.',
        docAnchor: 'doc-graph-edges' },
      { key: 'graph.edge_types.embedding_similarity.min_similarity', label: 'Embedding Min Similarity', controlType: 'slider', min: 0, max: 1, step: 0.01,
        infoText: 'Minimum cosine similarity for an embedding-similarity edge to be retained.',
        docAnchor: 'doc-graph-edges' },
      { key: 'graph.edge_types.entity_overlap.enabled', label: 'Entity Overlap Edges Enabled', controlType: 'toggle',
        infoText: 'Enable/disable entity-overlap edges.',
        docAnchor: 'doc-graph-edges' },
      { key: 'graph.edge_types.entity_overlap.weight', label: 'Entity Edge Weight', controlType: 'slider', min: 0, max: 1, step: 0.05,
        infoText: 'Scaling factor applied to entity-overlap edge weights.',
        docAnchor: 'doc-graph-edges' },
      { key: 'graph.edge_types.entity_overlap.min_jaccard', label: 'Entity Min Jaccard', controlType: 'slider', min: 0, max: 1, step: 0.01,
        infoText: 'Minimum Jaccard coefficient for an entity-overlap edge to be retained.',
        docAnchor: 'doc-graph-edges' },
      { key: 'graph.edge_types.internal_links.enabled', label: 'Internal Link Edges', controlType: 'toggle',
        disabled: true,
        infoText: 'Internal-link edges deferred pending link extraction implementation.',
        docAnchor: 'doc-graph-edges' },
    ],
  },
  {
    id: 'storage',
    title: 'STORAGE',
    params: [
      { key: 'storage.content_versions_retained', label: 'Content Versions Retained', controlType: 'number', min: 1, max: 20, step: 1,
        infoText: 'Number of previous snapshot versions retained per influencer source.',
        docAnchor: 'doc-snapshots' },
      { key: 'storage.sqlite_wal_mode', label: 'SQLite WAL Mode', controlType: 'toggle',
        disabled: true,
        infoText: 'SQLite Write-Ahead Logging. Required for concurrent access. Cannot be disabled.',
        docAnchor: 'doc-sqlite' },
      { key: 'storage.git_persistence.commit_snapshots', label: 'Git Commit Snapshots', controlType: 'toggle',
        infoText: 'Commit influencer snapshots to the repository after each run.',
        docAnchor: 'doc-git-persistence' },
      { key: 'storage.git_persistence.commit_database', label: 'Git Commit Database', controlType: 'toggle',
        infoText: 'Commit the IPFR SQLite database after each ingestion run.',
        docAnchor: 'doc-git-persistence' },
    ],
  },
  {
    id: 'notifications',
    title: 'NOTIFICATIONS',
    params: [
      { key: 'notifications.content_owner_email', label: 'Content Owner Email', controlType: 'email',
        infoText: 'Receives consolidated alert reports after each run.',
        docAnchor: 'doc-notifications' },
      { key: 'notifications.health_alert_email', label: 'Health Alert Email', controlType: 'email',
        infoText: 'Receives system health alerts.',
        docAnchor: 'doc-health-alerts' },
      { key: 'notifications.health_alert_conditions.error_rate_threshold', label: 'Error Rate Threshold', controlType: 'slider', min: 0, max: 1, step: 0.05,
        infoText: 'If the error fraction in a single run exceeds this, a health alert is dispatched.',
        docAnchor: 'doc-health-alerts' },
      { key: 'notifications.health_alert_conditions.consecutive_failures_threshold', label: 'Consecutive Failures Threshold', controlType: 'number', min: 1, max: 10, step: 1,
        infoText: 'A health alert is sent if the same source fails this many consecutive runs.',
        docAnchor: 'doc-health-alerts' },
      { key: 'notifications.health_alert_conditions.pipeline_timeout_minutes', label: 'Pipeline Timeout (minutes)', controlType: 'number', min: 10, max: 120, step: 5,
        infoText: 'The GitHub Actions timeout-minutes budget.',
        docAnchor: 'doc-timeout' },
    ],
  },
];

// ── Accordion Section ─────────────────────────────────────────────────────────
function AccordionSection({ section, config, staged, onStage }) {
  const [open, setOpen] = useState(true);

  const stagedCount = section.params.filter(p => staged[p.key] !== undefined).length;

  return (
    <div style={{ borderBottom: '1px solid var(--rule)' }}>
      {/* Header */}
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          width: '100%', display: 'flex', alignItems: 'center', gap: '10px',
          padding: '10px 12px',
          background: 'none', border: 'none', cursor: 'pointer', textAlign: 'left',
        }}
      >
        <span style={{
          fontFamily: 'var(--font-display)', fontSize: '14px', letterSpacing: '0.08em',
          color: 'var(--text-primary)', flex: 1,
        }}>
          {section.title}
        </span>
        {stagedCount > 0 && (
          <span style={{
            fontFamily: 'var(--font-mono)', fontSize: '9px', color: 'var(--state-warn)',
            border: '1px solid var(--state-warn)', padding: '1px 6px', borderRadius: '2px',
            letterSpacing: '0.05em',
          }}>
            {stagedCount} UNSAVED
          </span>
        )}
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)',
          transform: open ? 'rotate(90deg)' : 'none', transition: 'transform 150ms',
          display: 'inline-block',
        }}>
          ›
        </span>
      </button>

      {/* Params */}
      {open && (
        <div>
          {section.params.map(p => {
            const savedVal = getPath(config, p.key);
            const effectiveVal = staged[p.key] !== undefined ? staged[p.key] : savedVal;
            return (
              <ConfigControl
                key={p.key}
                paramKey={p.key}
                label={p.label}
                controlType={p.controlType}
                min={p.min}
                max={p.max}
                step={p.step}
                readOnly={p.readOnly}
                locked={p.locked}
                lockWarning={p.lockWarning}
                nullable={p.nullable}
                docAnchor={p.docAnchor}
                infoText={p.infoText}
                value={effectiveVal}
                onChange={val => onStage(p.key, val)}
                isStaged={staged[p.key] !== undefined}
                disabled={p.disabled ?? false}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── YAML Preview Panel ─────────────────────────────────────────────────────────
function YamlPreview({ config, staged, onCopy }) {
  const { lines, changed } = useMemo(() => {
    if (!config) return { lines: [], changed: new Set() };
    const merged = mergeStaged(config, staged);
    return getChangedLines(config, merged);
  }, [config, staged]);

  return (
    <div style={{
      position: 'sticky', top: 0, maxHeight: 'calc(100vh - 80px)',
      display: 'flex', flexDirection: 'column',
      background: 'var(--bg-secondary)', border: '1px solid var(--rule)',
      overflow: 'hidden',
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: '8px',
        padding: '8px 12px', borderBottom: '1px solid var(--rule)', flexShrink: 0,
      }}>
        <span style={{
          fontFamily: 'var(--font-display)', fontSize: '12px',
          letterSpacing: '0.07em', color: 'var(--text-secondary)', flex: 1,
        }}>
          YAML PREVIEW
        </span>
        <button
          onClick={onCopy}
          style={{
            background: 'none', border: '1px solid var(--rule)',
            fontFamily: 'var(--font-mono)', fontSize: '9px', color: 'var(--text-tertiary)',
            padding: '2px 8px', cursor: 'pointer', letterSpacing: '0.05em',
          }}
        >
          COPY
        </button>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: '0' }}>
        {lines.map((line, i) => (
          <div
            key={i}
            style={{
              fontFamily: 'var(--font-mono)', fontSize: '10px',
              color: changed.has(i) ? 'var(--text-primary)' : 'var(--text-secondary)',
              lineHeight: '1.6', padding: '0 10px',
              borderLeft: changed.has(i) ? '3px solid var(--state-warn)' : '3px solid transparent',
              background: changed.has(i) ? 'rgba(212,168,32,0.06)' : 'transparent',
              whiteSpace: 'pre',
            }}
          >
            {line || ' '}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Main Adjust section ────────────────────────────────────────────────────────
export default function Adjust() {
  const { data: config, isLoading, error: loadError } = useConfigData();
  const queryClient = useQueryClient();

  const [staged, setStaged]       = useState({});
  const [showYaml, setShowYaml]   = useState(false);
  const [saveState, setSaveState] = useState('idle'); // 'idle' | 'saving' | 'success' | 'error'
  const [saveError, setSaveError] = useState('');

  const isDirty = Object.keys(staged).length > 0;

  const stage = useCallback((key, val) => {
    setStaged(s => ({ ...s, [key]: val }));
  }, []);

  const handleSave = async () => {
    if (!isDirty || !config) return;
    setSaveState('saving');
    setSaveError('');
    try {
      const merged = mergeStaged(config, staged);
      const res = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(merged),
      });
      const json = await res.json();
      if (!res.ok || json.error) throw new Error(json.error || `HTTP ${res.status}`);
      setStaged({});
      queryClient.invalidateQueries({ queryKey: ['config'] });
      setSaveState('success');
      setTimeout(() => setSaveState('idle'), 2000);
    } catch (err) {
      setSaveError(err.message);
      setSaveState('error');
    }
  };

  const handleCopyYaml = () => {
    if (!config) return;
    const merged = mergeStaged(config, staged);
    navigator.clipboard?.writeText(yaml.dump(merged)).catch(() => {});
  };

  if (isLoading) {
    return (
      <div style={{ padding: '24px', fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--text-tertiary)' }}>
        LOADING CONFIG…
      </div>
    );
  }
  if (loadError) {
    return (
      <div style={{ padding: '24px', fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--state-error)' }}>
        FAILED TO LOAD CONFIG: {loadError.message}
      </div>
    );
  }

  const saveBackground = saveState === 'success' ? 'var(--state-ok)'
    : saveState === 'saving' ? 'var(--bg-accent)'
    : isDirty ? 'var(--state-ok)' : 'var(--bg-accent)';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* ── Section header ── */}
      <div style={{
        height: '80px', minHeight: '80px',
        display: 'flex', alignItems: 'flex-end',
        padding: '0 24px 12px', borderBottom: '1px solid var(--rule)', gap: '16px',
      }}>
        <div>
          <div style={{ fontFamily: 'var(--font-display)', fontSize: '42px', lineHeight: 1, letterSpacing: '0.04em' }}>
            ADJUST
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--text-secondary)', marginTop: '2px' }}>
            Pipeline Configuration
          </div>
        </div>
        <div style={{ flex: 1 }} />
        <button
          onClick={() => setShowYaml(v => !v)}
          style={{
            fontFamily: 'var(--font-mono)', fontSize: '10px', letterSpacing: '0.07em',
            background: showYaml ? 'var(--bg-accent)' : 'none',
            border: '1px solid var(--rule-accent)',
            color: showYaml ? 'var(--text-primary)' : 'var(--text-secondary)',
            padding: '6px 14px', cursor: 'pointer',
          }}
        >
          PREVIEW YAML
        </button>
      </div>

      {/* ── Body ── */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0' }}>
          {/* ── Left: accordion (70%) ── */}
          <div style={{ flex: '0 0 70%', minWidth: 0, borderRight: showYaml ? '1px solid var(--rule)' : 'none' }}>
            {SECTIONS.map(section => (
              <AccordionSection
                key={section.id}
                section={section}
                config={config}
                staged={staged}
                onStage={stage}
              />
            ))}

            {/* APPLY CHANGES button */}
            <div style={{ padding: '16px 12px' }}>
              <button
                onClick={handleSave}
                disabled={!isDirty || saveState === 'saving'}
                style={{
                  width: '100%', padding: '12px',
                  fontFamily: 'var(--font-display)', fontSize: '14px', letterSpacing: '0.08em',
                  background: saveBackground,
                  color: 'var(--text-primary)',
                  border: 'none',
                  cursor: isDirty && saveState !== 'saving' ? 'pointer' : 'default',
                  opacity: !isDirty && saveState === 'idle' ? 0.5 : 1,
                  transition: 'background 300ms',
                }}
              >
                {saveState === 'saving' ? 'SAVING…'
                  : saveState === 'success' ? 'SAVED ✓'
                  : 'APPLY CHANGES'}
              </button>
              {saveState === 'error' && saveError && (
                <div style={{
                  fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--state-error)',
                  marginTop: '8px', letterSpacing: '0.04em',
                }}>
                  {saveError}
                </div>
              )}
            </div>
          </div>

          {/* ── Right: YAML preview (30%) ── */}
          {showYaml && (
            <div style={{ flex: '0 0 30%', minWidth: 0 }}>
              <YamlPreview config={config} staged={staged} onCopy={handleCopyYaml} />
            </div>
          )}
        </div>

        {/* ── Threshold Simulator ── */}
        <div style={{ margin: '0', borderTop: '1px solid var(--rule)' }}>
          <ThresholdSimulator config={config} />
        </div>
      </div>
    </div>
  );
}
