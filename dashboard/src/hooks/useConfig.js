import { useState, useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useConfig as useConfigData } from './useData.js';

export function useConfigManager() {
  const { data: config, isLoading, error } = useConfigData();
  const [staged, setStaged] = useState({});
  const queryClient = useQueryClient();

  const stage = useCallback((key, val) => {
    setStaged(s => ({ ...s, [key]: val }));
  }, []);

  const reset = useCallback(() => {
    setStaged({});
  }, []);

  const isDirty = Object.keys(staged).length > 0;

  const save = useCallback(async () => {
    const res = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(staged),
    });
    if (!res.ok) throw new Error(`Config save failed: ${res.status}`);
    setStaged({});
    queryClient.invalidateQueries({ queryKey: ['config'] });
  }, [staged, queryClient]);

  return { config, isLoading, error, staged, stage, reset, isDirty, save };
}
