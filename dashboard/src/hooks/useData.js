import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';

const API_BASE = '';
const STALE = 5 * 60 * 1000;

async function apiFetch(path) {
  const res = await fetch(API_BASE + path);
  if (!res.ok) throw new Error(`API ${res.status}: ${path}`);
  return res.json();
}

// Unwrap { data: [...] } envelope — all list endpoints use this shape.
function selectArray(response) {
  if (Array.isArray(response)) return response;
  if (response && Array.isArray(response.data)) return response.data;
  return [];
}

const DATE_PRESET_DAYS = { '7D': 7, '30D': 30, '90D': 90, '180D': 180, '365D': 365 };

function filtersToSearch(filters) {
  if (!filters) return '';
  const params = new URLSearchParams();

  // Resolve datePreset into concrete ISO from/to so the backend only
  // needs to handle real timestamps, not a preset name.
  let from = filters.from;
  let to   = filters.to;
  if (!from && filters.datePreset && DATE_PRESET_DAYS[filters.datePreset]) {
    const now = new Date();
    to   = now.toISOString().slice(0, 10);
    const d = new Date(now);
    d.setDate(d.getDate() - DATE_PRESET_DAYS[filters.datePreset]);
    from = d.toISOString().slice(0, 10);
  }
  if (from) params.set('from', from);
  if (to)   params.set('to',   to);

  // Repeated source_id params so the server can use IN (?, ?, …)
  if (filters.sources?.length) {
    for (const src of filters.sources) params.append('source_id', src);
  }

  if (filters.stageMin != null) params.set('stage_reached_min', filters.stageMin);

  // Repeated verdict params for multi-select IN
  if (filters.verdicts?.length) {
    for (const v of filters.verdicts) params.append('verdict', v);
  }

  const s = params.toString();
  return s ? `?${s}` : '';
}

export function useRuns(filters) {
  const qs = filtersToSearch(filters);
  // lite fields + a high limit: list views need the whole filtered history
  // (the old default capped at 1000 rows and silently truncated), while the
  // heavy fields (diff text, LLM prose) are only fetched per-run on demand.
  const sep = qs ? '&' : '?';
  return useQuery({
    queryKey: ['runs', qs],
    queryFn: () => apiFetch(`/api/runs${qs}${sep}fields=lite&limit=20000`),
    staleTime: STALE,
    select: selectArray,
  });
}

export function useRunsSummary(filters) {
  const qs = filtersToSearch(filters);
  return useQuery({
    queryKey: ['runs', 'summary', qs],
    queryFn: () => apiFetch(`/api/runs/summary${qs}`),
    staleTime: STALE,
  });
}

export function useFeedback() {
  return useQuery({
    queryKey: ['runs', 'feedback'],
    queryFn: () => apiFetch('/api/runs/feedback'),
    staleTime: STALE,
  });
}

export function useRun(runId) {
  return useQuery({
    queryKey: ['runs', runId],
    queryFn: () => apiFetch(`/api/runs/${runId}`),
    staleTime: STALE,
    enabled: runId != null,
  });
}

export function usePages() {
  return useQuery({
    queryKey: ['pages'],
    queryFn: () => apiFetch('/api/pages'),
    staleTime: STALE,
  });
}

export function usePage(pageId) {
  return useQuery({
    queryKey: ['pages', pageId],
    queryFn: () => apiFetch(`/api/pages/${pageId}`),
    staleTime: STALE,
    enabled: pageId != null,
  });
}

export function useSources() {
  return useQuery({
    queryKey: ['sources'],
    queryFn: () => apiFetch('/api/sources'),
    staleTime: STALE,
    select: selectArray,
  });
}

export function useConfig() {
  return useQuery({
    queryKey: ['config'],
    queryFn: () => apiFetch('/api/config'),
    staleTime: STALE,
    // The server wraps the YAML config in a { data: … } envelope. Unwrap it
    // here so consumers see the real config object — without this every
    // dot-path lookup (pipeline.max_retries, …) misses and controls render 0.
    select: (response) =>
      response && typeof response === 'object' && 'data' in response
        ? response.data
        : response,
  });
}

// page_id → title lookup built from the pages list. IPFR page IDs (X02CB3…)
// are opaque to users, so views that reference pages should display titles.
export function usePageTitles() {
  const { data } = usePages();
  return useMemo(() => {
    const pages = Array.isArray(data?.data) ? data.data : (Array.isArray(data) ? data : []);
    const map = new Map();
    for (const p of pages) {
      if (p.page_id && p.title) map.set(p.page_id, p.title);
    }
    return map;
  }, [data]);
}

export function useEmbeddings() {
  return useQuery({
    queryKey: ['embeddings'],
    queryFn: () => apiFetch('/api/embeddings'),
    staleTime: STALE,
  });
}

export function useGraphNodes() {
  return useQuery({
    queryKey: ['graph', 'nodes'],
    queryFn: () => apiFetch('/api/graph/nodes'),
    staleTime: STALE,
  });
}

export function useGraphEdges() {
  return useQuery({
    queryKey: ['graph', 'edges'],
    queryFn: () => apiFetch('/api/graph/edges'),
    staleTime: STALE,
  });
}

// Source → IPFR page trigger edges aggregated server-side over the full
// run history (not subject to the /api/runs row limit).
export function useBipartiteEdges() {
  return useQuery({
    queryKey: ['graph', 'bipartite'],
    queryFn: () => apiFetch('/api/graph/bipartite'),
    staleTime: STALE,
    select: selectArray,
  });
}

// Acknowledge ("mark reviewed") a page's outstanding CHANGE_REQUIRED flags.
// Not a hook — call from event handlers, then invalidate queries.
export async function acknowledgePageAlerts(pageId, { undo = false } = {}) {
  const res = await fetch(`/api/pages/${encodeURIComponent(pageId)}/acknowledge`, {
    method: undo ? 'DELETE' : 'POST',
  });
  if (!res.ok) throw new Error(`API ${res.status}: acknowledge ${pageId}`);
  return res.json();
}

export function useSnapshot(sourceId) {
  return useQuery({
    queryKey: ['snapshots', sourceId],
    queryFn: () => apiFetch(`/api/snapshots/${sourceId}`),
    staleTime: STALE,
    enabled: sourceId != null,
  });
}

export function useHealthSummary() {
  return useQuery({
    queryKey: ['health', 'summary'],
    queryFn: () => apiFetch('/api/health/summary'),
    staleTime: STALE,
  });
}

export function useHealthRuns() {
  return useQuery({
    queryKey: ['health', 'runs'],
    queryFn: () => apiFetch('/api/health/runs'),
    staleTime: STALE,
  });
}

export function useHealthIngestion() {
  return useQuery({
    queryKey: ['health', 'ingestion'],
    queryFn: () => apiFetch('/api/health/ingestion'),
    staleTime: STALE,
  });
}

export function useLLMReports(filters) {
  const params = new URLSearchParams();
  if (filters?.verdict) params.set('verdict', filters.verdict);
  if (filters?.run_id)  params.set('run_id', filters.run_id);
  if (filters?.page_id) params.set('page_id', filters.page_id);
  const qs = params.toString() ? `?${params.toString()}` : '';
  return useQuery({
    queryKey: ['llm-reports', qs],
    queryFn: () => apiFetch(`/api/llm-reports${qs}`),
    staleTime: STALE,
  });
}
