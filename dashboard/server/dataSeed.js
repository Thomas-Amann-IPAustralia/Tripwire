// Token-free data fallback.
//
// The dashboard normally refreshes its data by downloading the latest GitHub
// Release asset in syncData.js. That path needs a valid GITHUB_TOKEN for a
// private repo; when the token is missing or expired the API returns HTTP 401
// and the release download aborts, leaving the persistent disk frozen.
//
// The pipeline already commits the authoritative data (ipfr.sqlite, config,
// source_registry.csv) into the repository, and Render ships those files in
// every git deploy. This module copies that committed copy into DATA_ROOT when
// the deploy carries a newer/different version, so the dashboard stays current
// with each redeploy without any credentials.

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Root of the deployed git checkout (dashboard/server/ -> repo root). This is
// always the committed data, independent of DATA_ROOT (which points at the
// persistent disk in production).
export const COMMITTED_ROOT = path.join(__dirname, '..', '..');

export function committedPath(relPath) {
  return path.join(COMMITTED_ROOT, relPath);
}

// Copy `src` -> `dest` when the committed copy looks newer or different, or the
// destination is missing. Returns true when a copy happened.
//
// No-ops when src and dest resolve to the same file — in local development
// DATA_ROOT is the repo root, so the committed copy IS the served copy.
export function seedFileIfNewer(src, dest, label) {
  try {
    if (!fs.existsSync(src)) return false;
    if (path.resolve(src) === path.resolve(dest)) return false;

    let destMtime = -Infinity;
    let destSize = -1;
    try {
      const st = fs.statSync(dest);
      destMtime = st.mtimeMs;
      destSize = st.size;
    } catch { /* destination absent — always seed */ }

    const srcStat = fs.statSync(src);
    // Refresh when the deploy is newer (fresh clone bumps mtime) OR the content
    // size differs (guards against a preserved mtime hiding changed content).
    const isStale = destMtime < 0 || srcStat.mtimeMs > destMtime || srcStat.size !== destSize;
    if (!isStale) return false;

    fs.mkdirSync(path.dirname(dest), { recursive: true });
    const tmp = dest + '.seed.tmp';
    fs.copyFileSync(src, tmp);
    fs.renameSync(tmp, dest);
    console.log(`[seed] ${label}: refreshed from committed data`);
    return true;
  } catch (err) {
    console.error(`[seed] ${label}: failed to seed from committed data: ${err.message}`);
    return false;
  }
}
