import { Router } from 'express';
import fs from 'fs';
import path from 'path';
import { db, SNAPSHOTS_PATH } from '../db.js';

const router = Router();

function computeDiff(oldText, newText) {
  if (!oldText || !newText) return null;
  const oldLines = oldText.split('\n');
  const newLines = newText.split('\n');
  const diff = [];

  const oldSet = new Set(oldLines);
  const newSet = new Set(newLines);

  for (const line of oldLines) {
    if (!newSet.has(line)) diff.push(`- ${line}`);
  }
  for (const line of newLines) {
    if (!oldSet.has(line)) diff.push(`+ ${line}`);
  }
  return diff.join('\n');
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
    }
  } catch (err) {
    console.error(`[snapshots] reading files for ${sourceId}:`, err.message);
  }

  const diff = computeDiff(previous_snapshot_text, snapshot_text);

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
