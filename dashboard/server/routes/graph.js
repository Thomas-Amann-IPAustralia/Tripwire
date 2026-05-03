import { Router } from 'express';
import { db, dbGuard } from '../db.js';
import { getCache } from './pages.js';

const router = Router();

// GET /api/graph/nodes
router.get('/nodes', (req, res) => {
  if (!dbGuard(res)) return;

  try {
    const rows = db.prepare(`
      SELECT
        p.page_id, p.title,
        COUNT(DISTINCT ge.id) AS degree,
        COUNT(DISTINCT pr.id) AS alert_count
      FROM pages p
      LEFT JOIN graph_edges ge ON ge.source_page_id = p.page_id OR ge.target_page_id = p.page_id
      LEFT JOIN pipeline_runs pr
        ON pr.triggered_pages LIKE '%' || p.page_id || '%'
        AND json_extract(pr.details, '$.stages.llm_assessment.verdict') = 'CHANGE_REQUIRED'
      WHERE p.status = 'active'
      GROUP BY p.page_id
    `).all();

    const cache = getCache();

    const data = rows.map(row => {
      const emb = cache.byPageId[row.page_id] || {};
      return {
        page_id: row.page_id,
        title: row.title,
        cluster: emb.cluster ?? null,
        alert_count: row.alert_count,
        degree: row.degree,
        embedding_2d: emb.embedding_2d ?? null,
      };
    });

    res.json({ data });
  } catch (err) {
    console.error('[graph] GET /nodes:', err.message);
    res.status(500).json({ data: [], error: err.message });
  }
});

// GET /api/graph/edges
router.get('/edges', (req, res) => {
  if (!dbGuard(res)) return;

  try {
    const data = db.prepare(`
      SELECT ge.source_page_id, ge.target_page_id, ge.edge_type, ge.weight
      FROM graph_edges ge
      JOIN pages src ON src.page_id = ge.source_page_id AND src.status = 'active'
      JOIN pages tgt ON tgt.page_id = ge.target_page_id AND tgt.status = 'active'
      ORDER BY ge.weight DESC
    `).all();

    res.json({ data });
  } catch (err) {
    console.error('[graph] GET /edges:', err.message);
    res.status(500).json({ data: [], error: err.message });
  }
});

export default router;
