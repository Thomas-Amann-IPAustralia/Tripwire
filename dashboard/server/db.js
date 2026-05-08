import Database from 'better-sqlite3';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export const REPO_ROOT = process.env.DATA_ROOT || path.join(__dirname, '..', '..');
export const DB_PATH = path.join(REPO_ROOT, 'data/ipfr_corpus/ipfr.sqlite');
export const CONFIG_PATH = path.join(REPO_ROOT, 'tripwire_config.yaml');
export const REGISTRY_PATH = path.join(REPO_ROOT, 'data/influencer_sources/source_registry.csv');
export const FEEDBACK_PATH = path.join(REPO_ROOT, 'data/logs/feedback.jsonl');
export const SNAPSHOTS_PATH = path.join(REPO_ROOT, 'data/influencer_sources/snapshots');

export function getDbMtime() {
  try {
    return fs.statSync(DB_PATH).mtimeMs;
  } catch {
    return null;
  }
}

export let db = null;

export function openDb() {
  if (db) return db;

  try {
    if (!fs.existsSync(DB_PATH)) {
      throw new Error(`Database file not found at ${DB_PATH}`);
    }

    db = new Database(DB_PATH, {
      readonly: true,
      fileMustExist: true,
    });

    // Do not set WAL mode on a read-only database.
    db.pragma('query_only = ON');

    console.log(`[db] Opened SQLite database at ${DB_PATH}`);
    return db;
  } catch (err) {
    console.error(`[db] Failed to open SQLite database: ${err.message}`);
    db = null;
    return null;
  }
}

export { db };

export function dbGuard(res) {
  if (!db) {
    res.status(503).json({ data: [], error: 'database_not_found' });
    return false;
  }
  return true;
}
