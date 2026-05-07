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
  return useQuery({
    queryKey: ['runs', qs],
    queryFn: () => apiFetch(`/api/runs${qs}`),
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
  });
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
