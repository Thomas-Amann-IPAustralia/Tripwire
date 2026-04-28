import { Router } from 'express';
import { db, dbGuard } from '../db.js';
import { getCache } from './pages.js';

const router = Router();

// GET /api/embeddings
router.get('/', (req, res) => {
  if (!dbGuard(res)) return;

  try {
    const cache = getCache();

    const alertCounts = db.prepare(`
      SELECT p.page_id, COUNT(pr.id) AS alert_count
      FROM pages p
      LEFT JOIN pipeline_runs pr
        ON pr.triggered_pages LIKE '%' || p.page_id || '%'
        AND json_extract(pr.details, '$.stages.llm_assessment.verdict') = 'CHANGE_REQUIRED'
      WHERE p.status = 'active'
      GROUP BY p.page_id
    `).all();

    const alertMap = {};
    for (const row of alertCounts) alertMap[row.page_id] = row.alert_count;

    const titles = db.prepare(`SELECT page_id, title FROM pages WHERE status = 'active'`).all();
    const titleMap = {};
    for (const row of titles) titleMap[row.page_id] = row.title;

    const data = Object.entries(cache.byPageId).map(([page_id, emb]) => ({
      page_id,
      title: titleMap[page_id] ?? '',
      x: emb.embedding_3d?.[0] ?? 0,
      y: emb.embedding_3d?.[1] ?? 0,
      z: emb.embedding_3d?.[2] ?? 0,
      cluster: emb.cluster ?? 0,
      alert_count: alertMap[page_id] ?? 0,
    }));

    res.json({ data });
  } catch (err) {
    console.error('[embeddings] GET /:', err.message);
    res.status(500).json({ data: [], error: err.message });
  }
});

export default router;
