import { useCallback, useEffect, useState } from 'react';

// Theme handling. The warm light theme is the default; dark mode is stored in
// localStorage under STORAGE_KEY and applied as <html data-theme="dark">.
// index.html applies the saved value before first paint.

export const STORAGE_KEY = 'tripwire-theme';

function readTheme() {
  if (typeof document === 'undefined') return 'light';
  return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
}

export function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === 'dark') root.setAttribute('data-theme', 'dark');
  else root.removeAttribute('data-theme');
  try { localStorage.setItem(STORAGE_KEY, theme); } catch { /* storage unavailable */ }
}

// Re-renders whenever the theme attribute changes, wherever it was changed.
export function useTheme() {
  const [theme, setThemeState] = useState(readTheme);

  useEffect(() => {
    const obs = new MutationObserver(() => setThemeState(readTheme()));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => obs.disconnect();
  }, []);

  const setTheme = useCallback((next) => {
    applyTheme(next);
    setThemeState(next);
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme(readTheme() === 'dark' ? 'light' : 'dark');
  }, [setTheme]);

  return { theme, setTheme, toggleTheme };
}

// Resolve a CSS custom property to its computed value for canvas / WebGL code
// that cannot use var() directly.
export function cssVar(name, fallback = '') {
  if (typeof document === 'undefined') return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}
