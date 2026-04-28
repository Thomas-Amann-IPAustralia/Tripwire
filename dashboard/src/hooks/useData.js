import { useQuery } from '@tanstack/react-query';

const API_BASE = '';
const STALE = 5 * 60 * 1000;

async function apiFetch(path) {
  const res = await fetch(API_BASE + path);
  if (!res.ok) throw new Error(`API ${res.status}: ${path}`);
  return res.json();
}

function filtersToSearch(filters) {
  if (!filters) return '';
  const params = new URLSearchParams();
  if (filters.from)               params.set('from', filters.from);
  if (filters.to)                 params.set('to', filters.to);
  if (filters.datePreset)         params.set('datePreset', filters.datePreset);
  if (filters.sources?.length)    params.set('sources', filters.sources.join(','));
  if (filters.stageMin != null)   params.set('stageMin', filters.stageMin);
  if (filters.verdicts?.length)   params.set('verdicts', filters.verdicts.join(','));
  const s = params.toString();
  return s ? `?${s}` : '';
}

export function useRuns(filters) {
  const qs = filtersToSearch(filters);
  return useQuery({
    queryKey: ['runs', qs],
    queryFn: () => apiFetch(`/api/runs${qs}`),
    staleTime: STALE,
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
