import React, { useState, useMemo, useCallback, useEffect, useRef } from 'react';
import yaml from 'js-yaml';
import { useQueryClient } from '@tanstack/react-query';
import { useConfig as useConfigData } from '../hooks/useData.js';
import ConfigControl from '../components/ConfigControl.jsx';
import ErrorBanner from '../components/ErrorBanner.jsx';
import ThresholdSimulator from '../visualisations/ThresholdSimulator.jsx';
import { PageHeader, Card, Badge, Caret, Icon, Loading, Callout } from '../components/ui.jsx';

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
    title: 'Pipeline behaviour', stage: 'All stages',
    params: [
      { key: 'pipeline.observation_mode', label: 'Observation mode', controlType: 'toggle',
        infoText: 'When ON, the pipeline runs all stages but skips LLM calls and sends no alerts. Use during the initial calibration period.',
        docAnchor: 'doc-observation-mode' },
      { key: 'pipeline.run_frequency_hours', label: 'Run frequency (hours)', controlType: 'number', min: 1, max: 168, step: 1,
        infoText: 'How often the pipeline runs, in hours. Default 24.',
        docAnchor: 'doc-run-frequency' },
      { key: 'pipeline.max_retries', label: 'Max retries', controlType: 'number', min: 0, max: 5, step: 1,
        infoText: 'How many times a transient failure is retried before the source is skipped.',
        docAnchor: 'doc-retries' },
      { key: 'pipeline.retry_base_delay_seconds', label: 'Retry base delay (s)', controlType: 'number', min: 0.5, max: 30, step: 0.5,
        infoText: 'The base delay for the first retry. Each subsequent retry doubles this value plus random jitter.',
        docAnchor: 'doc-retry-backoff' },
      { key: 'pipeline.llm_temperature', label: 'LLM temperature', controlType: 'slider', min: 0, max: 1, step: 0.05,
        infoText: 'Controls output randomness for the LLM assessment call. Default 0.2.',
        docAnchor: 'doc-llm-temperature' },
      { key: 'pipeline.llm_model', label: 'LLM model', controlType: 'text',
        infoText: 'The model identifier passed to the LLM API, e.g. gpt-4o.',
        docAnchor: 'doc-llm-model' },
      { key: 'pipeline.deferred_trigger_max_age_days', label: 'Deferred trigger max age (days)', controlType: 'number', min: 1, max: 30, step: 1,
        infoText: 'How long a deferred trigger is held before being discarded.',
        docAnchor: 'doc-deferred-triggers' },
    ],
  },
  {
    id: 'change_detection',
    title: 'Change detection', stage: 'Stage 2',
    params: [
      { key: 'change_detection.significance_fingerprint', label: 'Significance fingerprint', controlType: 'toggle',
        infoText: 'When ON, Stage 2 uses spaCy and regex to classify changes as high or standard significance.',
        docAnchor: 'doc-significance-fingerprint' },
    ],
  },
  {
    id: 'relevance',
    title: 'Relevance scoring', stage: 'Stage 4',
    params: [
      { key: 'relevance_scoring.rrf_k', label: 'RRF K', controlType: 'number', min: 10, max: 200, step: 5,
        infoText: 'Smoothing constant in the Reciprocal Rank Fusion formula. Default 60.',
        docAnchor: 'doc-rrf-k' },
      { key: 'relevance_scoring.rrf_weight_bm25', label: 'RRF weight BM25', controlType: 'number', min: 0, max: 5, step: 0.1,
        infoText: 'Weight of the BM25 keyword signal in RRF fusion.',
        docAnchor: 'doc-rrf-weights' },
      { key: 'relevance_scoring.rrf_weight_semantic', label: 'RRF weight semantic', controlType: 'number', min: 0, max: 5, step: 0.1,
        infoText: 'Weight of the bi-encoder semantic similarity signal in RRF fusion. Default 2.0.',
        docAnchor: 'doc-rrf-weights' },
      { key: 'relevance_scoring.top_n_candidates', label: 'Top N candidates', controlType: 'number', min: 1, max: 20, step: 1,
        infoText: 'Minimum number of IPFR pages forwarded to semantic matching.',
        docAnchor: 'doc-top-n' },
      { key: 'relevance_scoring.min_score_threshold', label: 'Min score threshold', controlType: 'number', min: 0, max: 1, step: 0.01, nullable: true,
        infoText: 'Floor score for inclusion beyond the top-N. Null during calibration.',
        docAnchor: 'doc-min-score-threshold' },
      { key: 'relevance_scoring.source_importance_floor', label: 'Source importance floor', controlType: 'slider', min: 0, max: 1, step: 0.05,
        infoText: 'The minimum multiplier applied to any source, regardless of importance.',
        docAnchor: 'doc-importance-floor' },
      { key: 'relevance_scoring.fast_pass.source_importance_min', label: 'Fast-pass importance min', controlType: 'slider', min: 0, max: 1, step: 0.05,
        infoText: 'Sources at or above this importance bypass Stage 4 fusion.',
        docAnchor: 'doc-fast-pass' },
      { key: 'relevance_scoring.yake.keyphrases_per_80_words', label: 'YAKE keyphrases / 80 words', controlType: 'number', min: 1, max: 5, step: 1,
        infoText: 'Rate of YAKE keyphrase extraction from diffs.',
        docAnchor: 'doc-yake' },
      { key: 'relevance_scoring.yake.min_keyphrases', label: 'YAKE min keyphrases', controlType: 'number', min: 1, max: 10, step: 1,
        infoText: 'Minimum keyphrases extracted.',
        docAnchor: 'doc-yake' },
      { key: 'relevance_scoring.yake.max_keyphrases', label: 'YAKE max keyphrases', controlType: 'number', min: 5, max: 30, step: 1,
        infoText: 'Maximum keyphrases extracted.',
        docAnchor: 'doc-yake' },
      { key: 'relevance_scoring.yake.short_diff_word_threshold', label: 'YAKE short diff threshold', controlType: 'number', min: 10, max: 200, step: 5,
        infoText: 'Diffs shorter than this word count are supplemented with NER entities.',
        docAnchor: 'doc-yake' },
    ],
  },
  {
    id: 'semantic',
    title: 'Semantic scoring', stage: 'Stages 5 and 6',
    params: [
      { key: 'semantic_scoring.biencoder.model', label: 'Bi-encoder model', controlType: 'text', locked: true,
        lockWarning: 'Changing this model invalidates all stored embeddings and requires a full re-ingestion.',
        infoText: 'Hugging Face model ID for the bi-encoder. Changing this invalidates all stored embeddings.',
        docAnchor: 'doc-biencoder' },
      { key: 'semantic_scoring.biencoder.high_threshold', label: 'Bi-encoder high threshold', controlType: 'slider', min: 0, max: 1, step: 0.01,
        infoText: 'A single chunk scoring above this cosine similarity triggers the IPFR page.',
        docAnchor: 'doc-biencoder-thresholds' },
      { key: 'semantic_scoring.biencoder.low_medium_threshold', label: 'Bi-encoder low/med threshold', controlType: 'slider', min: 0, max: 1, step: 0.01,
        infoText: 'The lower threshold used in the multi-chunk candidate trigger rule.',
        docAnchor: 'doc-biencoder-thresholds' },
      { key: 'semantic_scoring.biencoder.low_medium_min_chunks', label: 'Bi-encoder low/med min chunks', controlType: 'number', min: 1, max: 10, step: 1,
        infoText: 'Number of chunks that must exceed the low-medium threshold to trigger.',
        docAnchor: 'doc-biencoder-thresholds' },
      { key: 'semantic_scoring.crossencoder.model', label: 'Cross-encoder model', controlType: 'text', locked: true,
        lockWarning: 'Changing this model invalidates all stored embeddings and requires a full re-ingestion.',
        infoText: 'Hugging Face model ID for the cross-encoder reranker.',
        docAnchor: 'doc-crossencoder' },
      { key: 'semantic_scoring.crossencoder.threshold', label: 'Cross-encoder threshold', controlType: 'slider', min: 0, max: 1, step: 0.01,
        infoText: 'Minimum cross-encoder score for a candidate to proceed to LLM assessment.',
        docAnchor: 'doc-crossencoder-threshold' },
      { key: 'semantic_scoring.crossencoder.max_context_tokens', label: 'Cross-encoder max context tokens', controlType: 'number', min: 512, max: 16384, step: 512,
        infoText: 'Maximum combined token count passed to the cross-encoder.',
        docAnchor: 'doc-crossencoder-context' },
    ],
  },
  {
    id: 'graph',
    title: 'Graph propagation', stage: 'Stage 6',
    params: [
      { key: 'graph.enabled', label: 'Graph enabled', controlType: 'toggle',
        infoText: 'Enables alert propagation through the quasi-graph.',
        docAnchor: 'doc-graph' },
      { key: 'graph.max_hops', label: 'Max hops', controlType: 'number', min: 1, max: 5, step: 1,
        infoText: 'Maximum hops a propagated alert can travel.',
        docAnchor: 'doc-graph-hops' },
      { key: 'graph.decay_per_hop', label: 'Decay per hop', controlType: 'slider', min: 0, max: 1, step: 0.01,
        infoText: 'Signal fraction retained at each hop.',
        docAnchor: 'doc-graph-decay' },
      { key: 'graph.propagation_threshold', label: 'Propagation threshold', controlType: 'slider', min: 0, max: 0.5, step: 0.005,
        infoText: 'Propagation stops when decayed signal falls below this floor.',
        docAnchor: 'doc-graph-threshold' },
      { key: 'graph.edge_types.embedding_similarity.enabled', label: 'Embedding edges enabled', controlType: 'toggle',
        infoText: 'Enable/disable embedding-similarity edges in the graph.',
        docAnchor: 'doc-graph-edges' },
      { key: 'graph.edge_types.embedding_similarity.weight', label: 'Embedding edge weight', controlType: 'slider', min: 0, max: 1, step: 0.05,
        infoText: 'Scaling factor applied to embedding-similarity edge weights.',
        docAnchor: 'doc-graph-edges' },
      { key: 'graph.edge_types.embedding_similarity.top_k', label: 'Embedding edge top K', controlType: 'number', min: 1, max: 20, step: 1,
        infoText: 'Each page retains edges to its top-K most similar neighbours.',
        docAnchor: 'doc-graph-edges' },
      { key: 'graph.edge_types.embedding_similarity.min_similarity', label: 'Embedding min similarity', controlType: 'slider', min: 0, max: 1, step: 0.01,
        infoText: 'Minimum cosine similarity for an embedding-similarity edge to be retained.',
        docAnchor: 'doc-graph-edges' },
      { key: 'graph.edge_types.entity_overlap.enabled', label: 'Entity overlap edges enabled', controlType: 'toggle',
        infoText: 'Enable/disable entity-overlap edges.',
        docAnchor: 'doc-graph-edges' },
      { key: 'graph.edge_types.entity_overlap.weight', label: 'Entity edge weight', controlType: 'slider', min: 0, max: 1, step: 0.05,
        infoText: 'Scaling factor applied to entity-overlap edge weights.',
        docAnchor: 'doc-graph-edges' },
      { key: 'graph.edge_types.entity_overlap.min_jaccard', label: 'Entity min Jaccard', controlType: 'slider', min: 0, max: 1, step: 0.01,
        infoText: 'Minimum Jaccard coefficient for an entity-overlap edge to be retained.',
        docAnchor: 'doc-graph-edges' },
      { key: 'graph.edge_types.internal_links.enabled', label: 'Internal link edges', controlType: 'toggle',
        disabled: true,
        infoText: 'Internal-link edges deferred pending link extraction implementation.',
        docAnchor: 'doc-graph-edges' },
    ],
  },
  {
    id: 'storage',
    title: 'Storage', stage: 'Persistence',
    params: [
      { key: 'storage.content_versions_retained', label: 'Content versions retained', controlType: 'number', min: 1, max: 20, step: 1,
        infoText: 'Number of previous snapshot versions retained per influencer source.',
        docAnchor: 'doc-snapshots' },
      { key: 'storage.sqlite_wal_mode', label: 'SQLite WAL mode', controlType: 'toggle',
        disabled: true,
        infoText: 'SQLite Write-Ahead Logging. Required for concurrent access. Cannot be disabled.',
        docAnchor: 'doc-sqlite' },
      { key: 'storage.git_persistence.commit_snapshots', label: 'Git commit snapshots', controlType: 'toggle',
        infoText: 'Commit influencer snapshots to the repository after each run.',
        docAnchor: 'doc-git-persistence' },
      { key: 'storage.git_persistence.commit_database', label: 'Git commit database', controlType: 'toggle',
        infoText: 'Commit the IPFR SQLite database after each ingestion run.',
        docAnchor: 'doc-git-persistence' },
    ],
  },
  {
    id: 'notifications',
    title: 'Notifications', stage: 'Stage 9',
    params: [
      { key: 'notifications.content_owner_email', label: 'Content owner email', controlType: 'email',
        infoText: 'Receives consolidated alert reports after each run.',
        docAnchor: 'doc-notifications' },
      { key: 'notifications.health_alert_email', label: 'Health alert email', controlType: 'email',
        infoText: 'Receives system health alerts.',
        docAnchor: 'doc-health-alerts' },
      { key: 'notifications.health_alert_conditions.error_rate_threshold', label: 'Error rate threshold', controlType: 'slider', min: 0, max: 1, step: 0.05,
        infoText: 'If the error fraction in a single run exceeds this, a health alert is dispatched.',
        docAnchor: 'doc-health-alerts' },
      { key: 'notifications.health_alert_conditions.consecutive_failures_threshold', label: 'Consecutive failures threshold', controlType: 'number', min: 1, max: 10, step: 1,
        infoText: 'A health alert is sent if the same source fails this many consecutive runs.',
        docAnchor: 'doc-health-alerts' },
      { key: 'notifications.health_alert_conditions.pipeline_timeout_minutes', label: 'Pipeline timeout (minutes)', controlType: 'number', min: 10, max: 120, step: 5,
        infoText: 'The GitHub Actions timeout-minutes budget.',
        docAnchor: 'doc-timeout' },
    ],
  },
];

// ── Accordion Section ─────────────────────────────────────────────────────────
function AccordionSection({ section, config, staged, onStage, open, onToggle }) {
  const stagedCount = section.params.filter(p => staged[p.key] !== undefined).length;

  return (
    <section className="tw-card is-clip">
      <button
        type="button"
        onClick={() => onToggle(section.id)}
        aria-expanded={open}
        style={{
          width: '100%', display: 'flex', alignItems: 'center', gap: '12px',
          padding: '16px 20px', borderRadius: 0,
          background: 'none', border: 'none', textAlign: 'left',
          borderBottom: open ? '1px solid var(--line-soft)' : 'none',
        }}
      >
        <Caret open={open} />
        <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ display: 'block', fontSize: '15px', fontWeight: 650, color: 'var(--ink)' }}>{section.title}</span>
          <span style={{ display: 'block', fontSize: '12px', color: 'var(--muted)', marginTop: '2px' }}>
            {section.stage} · {section.params.length} setting{section.params.length !== 1 ? 's' : ''}
          </span>
        </span>
        {stagedCount > 0 && <Badge tone="warn" dot>{stagedCount} unsaved</Badge>}
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
    </section>
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
    <div className="tw-card is-clip" style={{
      position: 'sticky', top: '16px', maxHeight: 'calc(100vh - 120px)',
      display: 'flex', flexDirection: 'column',
    }}>
      <div className="tw-card-head" style={{ alignItems: 'center' }}>
        <div>
          <h2 className="tw-card-title">YAML preview</h2>
          <p className="tw-card-sub">{changed.size ? `${changed.size} changed line${changed.size !== 1 ? 's' : ''} highlighted` : 'No unsaved changes'}</p>
        </div>
        <button type="button" className="tw-btn is-sm" onClick={onCopy}>
          <Icon name="copy" /> Copy
        </button>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: '8px 0', background: 'var(--surface-subtle)' }}>
        {lines.map((line, i) => (
          <div
            key={i}
            style={{
              fontFamily: 'var(--font-code)', fontSize: '11.5px',
              color: changed.has(i) ? 'var(--ink)' : 'var(--body)',
              lineHeight: '1.65', padding: '0 14px',
              boxShadow: changed.has(i) ? 'inset 3px 0 0 var(--ochre)' : 'none',
              background: changed.has(i) ? 'color-mix(in srgb, var(--ochre) 14%, transparent)' : 'transparent',
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

  const [staged, setStaged]           = useState({});
  const [showYaml, setShowYaml]       = useState(false);
  const [saveState, setSaveState]     = useState('idle'); // 'idle' | 'saving' | 'success' | 'error'
  const [saveError, setSaveError]     = useState('');
  const [openSections, setOpenSections] = useState(() => new Set(SECTIONS.map(s => s.id)));
  const accordionRef                  = useRef(null);

  const toggleSection = useCallback((id) => {
    setOpenSections(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  // Listen for DOCUMENT → ADJUST config-key highlight
  useEffect(() => {
    const handler = (e) => {
      const configKey = e.detail;
      if (!configKey) return;
      // Find the section containing this param and ensure it's open
      const section = SECTIONS.find(s => s.params.some(p => p.key === configKey));
      if (section) {
        setOpenSections(prev => {
          if (prev.has(section.id)) return prev;
          const next = new Set(prev);
          next.add(section.id);
          return next;
        });
      }
      // After the accordion opens, scroll to the control and pulse it
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          const container = accordionRef.current;
          if (!container) return;
          const el = container.querySelector(`[data-param="${configKey}"]`);
          if (!el) return;
          el.scrollIntoView({ behavior: 'smooth', block: 'center' });
          el.classList.add('config-pulse');
          setTimeout(() => el.classList.remove('config-pulse'), 1500);
        });
      });
    };
    window.addEventListener('tripwire:highlight-control', handler);
    return () => window.removeEventListener('tripwire:highlight-control', handler);
  }, []);

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
    return <div className="tw-page"><Loading>Loading configuration…</Loading></div>;
  }
  if (loadError) {
    return <div className="tw-page"><ErrorBanner error={loadError} /></div>;
  }

  const stagedCount = Object.keys(staged).length;

  return (
    <div className="tw-page">
      <PageHeader
        eyebrow="Administration · tripwire_config.yaml"
        title="Configuration"
        description="Thresholds and behaviour for every pipeline stage. Changes are staged here first; nothing is written until you apply them."
        actions={
          <button type="button" className={`tw-btn${showYaml ? ' is-quiet' : ''}`} onClick={() => setShowYaml(v => !v)} aria-pressed={showYaml}>
            <Icon name="code" /> {showYaml ? 'Hide YAML preview' : 'Preview YAML'}
          </button>
        }
      />

      {/* Save bar: appears once something is staged */}
      {(isDirty || saveState !== 'idle') && (
        <div style={{
          position: 'sticky', top: 0, zIndex: 30, marginBottom: '16px',
          display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap',
          padding: '12px 16px', background: 'var(--surface)',
          border: '1px solid var(--warn-line)', borderLeft: '4px solid var(--ochre)',
          borderRadius: 'var(--radius-lg)', boxShadow: 'var(--shadow)',
        }}>
          <Icon name={saveState === 'success' ? 'check' : 'info'} size={18} style={{ color: saveState === 'success' ? 'var(--ok)' : 'var(--ochre-ink)' }} />
          <div style={{ flex: 1, minWidth: 0, fontSize: '13px' }}>
            {saveState === 'success'
              ? <strong>Configuration saved.</strong>
              : <><strong>{stagedCount} unsaved change{stagedCount !== 1 ? 's' : ''}.</strong> <span className="tw-muted">Review them, then apply to write the configuration file.</span></>}
          </div>
          {isDirty && (
            <button type="button" className="tw-btn is-sm" onClick={() => setStaged({})} disabled={saveState === 'saving'}>
              Discard
            </button>
          )}
          <button
            type="button"
            className="tw-btn is-primary is-sm"
            onClick={handleSave}
            disabled={!isDirty || saveState === 'saving'}
          >
            {saveState === 'saving' ? 'Saving…' : saveState === 'success' ? 'Saved' : 'Apply changes'}
          </button>
        </div>
      )}

      {saveState === 'error' && saveError && (
        <Callout tone="error" icon="alert" title="The configuration was not saved" style={{ marginBottom: '16px' }}>
          {saveError}
        </Callout>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: showYaml ? 'minmax(0, 1fr) minmax(300px, 420px)' : 'minmax(0, 1fr)', gap: '18px', alignItems: 'start' }}>
        <div ref={accordionRef} className="tw-stack" style={{ gap: '14px' }}>
          {SECTIONS.map(section => (
            <AccordionSection
              key={section.id}
              section={section}
              config={config}
              staged={staged}
              onStage={stage}
              open={openSections.has(section.id)}
              onToggle={toggleSection}
            />
          ))}

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', alignItems: 'center' }}>
            {!isDirty && saveState === 'idle' && <span className="tw-small tw-muted">No unsaved changes</span>}
            <button
              type="button"
              className="tw-btn is-primary"
              onClick={handleSave}
              disabled={!isDirty || saveState === 'saving'}
            >
              {saveState === 'saving' ? 'Saving…'
                : saveState === 'success' ? <><Icon name="check" /> Saved</>
                : 'Apply changes'}
            </button>
          </div>
        </div>

        {showYaml && <YamlPreview config={config} staged={staged} onCopy={handleCopyYaml} />}
      </div>

      <div style={{ marginTop: '22px' }}>
        <ThresholdSimulator config={config} />
      </div>
    </div>
  );
}
