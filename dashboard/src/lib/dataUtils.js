export function formatRelativeTime(isoString) {
  if (!isoString) return '—';
  const diff = Date.now() - new Date(isoString).getTime();
  const secs = Math.floor(diff / 1000);
  if (secs < 60)         return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60)         return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24)        return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function formatScore(n) {
  if (n == null || n === '') return '—';
  return Number(n).toFixed(2);
}

export function stageLabel(n) {
  return `S${n}`;
}

export function stageColor(n) {
  return `var(--stage-${n})`;
}

export function verdictClass(verdict) {
  if (!verdict) return '';
  switch (verdict.toUpperCase()) {
    case 'CHANGE_REQUIRED': return 'verdict-change-required';
    case 'UNCERTAIN':       return 'verdict-uncertain';
    case 'NO_CHANGE':       return 'verdict-no-change';
    default:                return '';
  }
}

export function aggregateByDay(runs) {
  if (!runs?.length) return [];
  const map = new Map();
  for (const run of runs) {
    const date = (run.timestamp ?? run.run_at)?.slice(0, 10);
    if (!date) continue;
    map.set(date, (map.get(date) || 0) + 1);
  }
  return Array.from(map.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, count]) => ({ date, count }));
}

export function computeFunnelCounts(runs) {
  if (!runs?.length) {
    return Array.from({ length: 9 }, (_, i) => ({ stage: i + 1, count: 0 }));
  }
  const counts = new Array(9).fill(0);
  for (const run of runs) {
    const stage = run.deepest_stage ?? run.stage_reached;
    if (stage >= 1 && stage <= 9) {
      for (let s = 1; s <= stage; s++) {
        counts[s - 1]++;
      }
    }
  }
  return counts.map((count, i) => ({ stage: i + 1, count }));
}
