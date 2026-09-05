import { useState, useCallback, useMemo } from 'react';

const DEFAULT = {
  datePreset: '30D',
  from: null,
  to: null,
  sources: [],
  stageMin: null,
  verdicts: [],
};

export function useFilters() {
  const [filters, setFilters] = useState(DEFAULT);

  const setDatePreset = useCallback((preset) => {
    setFilters(f => ({ ...f, datePreset: preset, from: null, to: null }));
  }, []);

  const setDateRange = useCallback((from, to) => {
    setFilters(f => ({ ...f, from, to, datePreset: null }));
  }, []);

  const setSources = useCallback((sources) => {
    setFilters(f => ({ ...f, sources }));
  }, []);

  const setStageMin = useCallback((stageMin) => {
    setFilters(f => ({ ...f, stageMin }));
  }, []);

  const setVerdicts = useCallback((verdicts) => {
    setFilters(f => ({ ...f, verdicts }));
  }, []);

  const toQueryParams = useCallback(() => {
    const params = new URLSearchParams();
    if (filters.from)             params.set('from', filters.from);
    if (filters.to)               params.set('to', filters.to);
    if (filters.datePreset)       params.set('datePreset', filters.datePreset);
    if (filters.sources?.length)  params.set('sources', filters.sources.join(','));
    if (filters.stageMin != null) params.set('stageMin', String(filters.stageMin));
    if (filters.verdicts?.length) params.set('verdicts', filters.verdicts.join(','));
    return params;
  }, [filters]);

  return {
    filters,
    setDatePreset,
    setDateRange,
    setSources,
    setStageMin,
    setVerdicts,
    toQueryParams,
  };
}
