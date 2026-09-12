import { Router } from 'express';
import fs from 'fs';
import path from 'path';
import { db, SNAPSHOTS_PATH } from '../db.js';

const router = Router();

// Try to load diff-match-patch; set to null if not installed.
let DiffMatchPatch = null;
try {
  const m = await import('diff-match-patch');
  DiffMatchPatch = m.diff_match_patch ?? m.default?.diff_match_patch ?? null;
} catch { /* not installed — use LCS fallback */ }

// Find the most-recent <sourceId>_*.diff file written by the pipeline.
function loadPipelineDiff(snapDir, sourceId) {
  try {
    const entries = fs.readdirSync(snapDir);
    const diffs = entries
      .filter(f => f.startsWith(`${sourceId}_`) && f.endsWith('.diff'))
      .sort(); // lexicographic → most recent last (ISO-timestamp filenames)
    if (!diffs.length) return null;
    return fs.readFileSync(path.join(snapDir, diffs[diffs.length - 1]), 'utf8');
  } catch {
    return null;
  }
}

// Minimal LCS-based line diff; returns a +/- annotated string.
function lcsDiff(oldLines, newLines) {
  const m = oldLines.length;
  const n = newLines.length;

  // Build LCS table
  const dp = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i][j] = oldLines[i - 1] === newLines[j - 1]
        ? dp[i - 1][j - 1] + 1
        : Math.max(dp[i - 1][j], dp[i][j - 1]);
    }
  }

  // Backtrack
  const edits = [];
  let i = m, j = n;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && oldLines[i - 1] === newLines[j - 1]) {
      i--; j--;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      edits.push(`+ ${newLines[j - 1]}`);
      j--;
    } else {
      edits.push(`- ${oldLines[i - 1]}`);
      i--;
    }
  }
  edits.reverse();
  return edits.join('\n') || null;
}

// Compute a diff using diff-match-patch (word-level) or LCS (line-level).
function computeFallbackDiff(oldText, newText) {
  if (!oldText && !newText) return null;
  if (!oldText) return newText.split('\n').map(l => `+ ${l}`).join('\n');
  if (!newText) return oldText.split('\n').map(l => `- ${l}`).join('\n');

  if (DiffMatchPatch) {
    const dmp = new DiffMatchPatch();
    const diffs = dmp.diff_main(oldText, newText);
    dmp.diff_cleanupSemantic(diffs);
    const out = diffs
      .filter(([op]) => op !== 0)
      .map(([op, text]) => op === 1 ? `+ ${text}` : `- ${text}`)
      .join('\n');
    return out || null;
  }

  return lcsDiff(oldText.split('\n'), newText.split('\n'));
}

// GET /api/snapshots/:sourceId
router.get('/:sourceId', (req, res) => {
  const { sourceId } = req.params;
  const snapDir = path.join(SNAPSHOTS_PATH, sourceId);

  let snapshot_text = null;
  let previous_snapshot_text = null;

  try {
    const currentPath = path.join(snapDir, `${sourceId}.txt`);
    if (fs.existsSync(currentPath)) {
      snapshot_text = fs.readFileSync(currentPath, 'utf8');
    }

    const prevPath = path.join(snapDir, `${sourceId}.v1.txt`);
    if (fs.existsSync(prevPath)) {
      previous_snapshot_text = fs.readFileSync(prevPath, 'utf8');
    } else {
      // For sources that have not yet changed, the previous scrape lives in
      // state.json["previous_text"] written by Stage 2 (change detection).
      const statePath = path.join(snapDir, 'state.json');
      if (fs.existsSync(statePath)) {
        try {
          const state = JSON.parse(fs.readFileSync(statePath, 'utf8'));
          if (state.previous_text) previous_snapshot_text = state.previous_text;
        } catch { /* malformed state.json — ignore */ }
      }
    }
  } catch (err) {
    console.error(`[snapshots] reading files for ${sourceId}:`, err.message);
  }

  // Prefer the real unified diff written by the pipeline; fall back to computed diff.
  const diff = loadPipelineDiff(snapDir, sourceId)
    ?? computeFallbackDiff(previous_snapshot_text, snapshot_text);

  let best_match_page_id = null;
  let best_match_page_content = null;
  let similarity_score = null;
  let matching_passages = [];
  let top_chunk_ids = [];
  let chunk_texts = [];

  if (db) {
    try {
      const run = db.prepare(`
        SELECT id, run_id, details
        FROM pipeline_runs
        WHERE source_id = ?
          AND CASE stage_reached
                WHEN 'stage5'          THEN 1
                WHEN 'stage6'          THEN 1
                WHEN 'stage6_complete' THEN 1
                ELSE 0
              END = 1
        ORDER BY timestamp DESC
        LIMIT 1
      `).get(sourceId);

      if (run) {
        let details;
        try { details = JSON.parse(run.details); } catch { details = {}; }

        const candidates = details?.stages?.biencoder?.candidate_pages || [];
        const bestPage = candidates.sort((a, b) => (b.max_chunk_score || 0) - (a.max_chunk_score || 0))[0];

        if (bestPage) {
          best_match_page_id = bestPage.page_id ?? null;
          similarity_score = bestPage.max_chunk_score ?? null;

          const topChunkScores = bestPage.top_chunk_scores || [];
          top_chunk_ids = topChunkScores.map(c => c.chunk_id).filter(Boolean);

          if (top_chunk_ids.length > 0) {
            const placeholders = top_chunk_ids.map(() => '?').join(',');
            const chunkRows = db.prepare(
              `SELECT chunk_id, chunk_text FROM chunks WHERE chunk_id IN (${placeholders})`
            ).all(...top_chunk_ids);

            chunk_texts = chunkRows.map(r => r.chunk_text);

            const chunkMap = {};
            for (const r of chunkRows) chunkMap[r.chunk_id] = r.chunk_text;

            matching_passages = topChunkScores.map(c => ({
              source_text: null,
              ipfr_text: chunkMap[c.chunk_id] ?? null,
              score: c.score ?? null,
            }));
          }

          if (best_match_page_id) {
            const pageRow = db.prepare(`SELECT content FROM pages WHERE page_id = ?`).get(best_match_page_id);
            if (pageRow) best_match_page_content = pageRow.content;
          }
        }
      }
    } catch (err) {
      console.error(`[snapshots] DB query for ${sourceId}:`, err.message);
    }
  }

  res.json({
    data: {
      source_id: sourceId,
      snapshot_text,
      previous_snapshot_text,
      diff,
      best_match_page_id,
      best_match_page_content,
      similarity_score,
      matching_passages,
      top_chunk_ids,
      chunk_texts,
    },
  });
});

export default router;
