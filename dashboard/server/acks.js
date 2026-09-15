// Alert acknowledgment store.
//
// The pipeline flags IPFR pages with CHANGE_REQUIRED verdicts; the dashboard
// surfaces those flags in several views (2D graph pulse rings, 3D glow,
// content map, page detail). The SQLite database is opened read-only and is
// replaced wholesale by the data sync, so acknowledgments live in a small
// JSON sidecar file instead: { [page_id]: ISO timestamp }.
//
// Semantics: a page's "outstanding" alert count is the number of
// CHANGE_REQUIRED assessments generated AFTER its acknowledgment timestamp.
// New alerts after a reset re-flag the page automatically.

import fs from 'fs';
import path from 'path';
import { REPO_ROOT } from './db.js';

export const ACKS_PATH = path.join(REPO_ROOT, 'data/logs/alert_acks.json');

export function loadAcks() {
  try {
    const parsed = JSON.parse(fs.readFileSync(ACKS_PATH, 'utf8'));
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function writeAcks(acks) {
  fs.mkdirSync(path.dirname(ACKS_PATH), { recursive: true });
  const tmp = ACKS_PATH + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(acks, null, 2), 'utf8');
  fs.renameSync(tmp, ACKS_PATH);
}

export function acknowledgePage(pageId, timestamp = new Date().toISOString()) {
  const acks = loadAcks();
  acks[pageId] = timestamp;
  writeAcks(acks);
  return acks[pageId];
}

export function clearAcknowledgment(pageId) {
  const acks = loadAcks();
  const existed = pageId in acks;
  delete acks[pageId];
  writeAcks(acks);
  return existed;
}

// Count only alerts newer than the page's ack timestamp (all, if never acked).
export function outstandingCount(alertTimestamps, ackTs) {
  if (!ackTs) return alertTimestamps.length;
  return alertTimestamps.filter(ts => ts && ts > ackTs).length;
}
