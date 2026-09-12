import { Router } from 'express';
import { db, dbGuard } from '../db.js';
import { getCache, computeAlertCounts } from './pages.js';

const router = Router();

// GET /api/graph/nodes
router.get('/nodes', (req, res) => {
  if (!dbGuard(res)) return;

  try {
    const rows = db.prepare(`
      SELECT
        p.page_id, p.title,
        COUNT(DISTINCT ge.id) AS degree
      FROM pages p
      LEFT JOIN graph_edges ge ON ge.source_page_id = p.page_id OR ge.target_page_id = p.page_id
      WHERE p.status = 'active'
      GROUP BY p.page_id
    `).all();

    // Alert counts come from the llm_assessments table, keyed by IPFR page id,
    // with acknowledgments ("mark reviewed") applied.
    const { alertsByPage, acks } = computeAlertCounts();

    const cache = getCache();

    const data = rows.map(row => {
      const emb = cache.byPageId[row.page_id] || {};
      const alerts = alertsByPage[row.page_id];
      return {
        page_id: row.page_id,
        title: row.title,
        cluster: emb.cluster ?? null,
        alert_count: alerts?.count ?? 0,
        alert_count_total: alerts?.total ?? 0,
        acknowledged_at: acks[row.page_id] ?? null,
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

// GET /api/graph/bipartite — source → IPFR page trigger edges aggregated
// over the FULL run history (the row-limited /api/runs endpoint previously
// used by the client silently dropped older connections once the history
// exceeded the row cap).
router.get('/bipartite', (req, res) => {
  if (!dbGuard(res)) return;

  try {
    const triggeredRows = db.prepare(`
      SELECT run_id, source_id, timestamp, triggered_pages
      FROM pipeline_runs
      WHERE triggered_pages IS NOT NULL AND triggered_pages != '[]'
    `).all();

    // Assessment lookup keyed by run + page for verdict/confidence per edge.
    const assessmentRows = db.prepare(`
      SELECT run_id, ipfr_page_id, verdict, confidence FROM llm_assessments
    `).all();
    const assessmentByRunPage = new Map(
      assessmentRows.map(r => [`${r.run_id}|${r.ipfr_page_id}`, r])
    );

    const edgeMap = new Map();
    for (const row of triggeredRows) {
      let pageIds;
      try { pageIds = JSON.parse(row.triggered_pages); } catch { continue; }
      if (!Array.isArray(pageIds)) continue;

      for (const pageId of pageIds) {
        const key = `${row.source_id}|${pageId}`;
        let edge = edgeMap.get(key);
        if (!edge) {
          edge = {
            source_id: row.source_id,
            page_id: pageId,
            trigger_count: 0,
            change_required_count: 0,
            max_confidence: null,
            last_triggered: null,
          };
          edgeMap.set(key, edge);
        }
        edge.trigger_count += 1;
        if (!edge.last_triggered || row.timestamp > edge.last_triggered) {
          edge.last_triggered = row.timestamp;
        }
        const assessment = assessmentByRunPage.get(`${row.run_id}|${pageId}`);
        if (assessment) {
          if (assessment.verdict === 'CHANGE_REQUIRED') edge.change_required_count += 1;
          if (assessment.confidence != null &&
              (edge.max_confidence == null || assessment.confidence > edge.max_confidence)) {
            edge.max_confidence = assessment.confidence;
          }
        }
      }
    }

    res.json({ data: [...edgeMap.values()] });
  } catch (err) {
    console.error('[graph] GET /bipartite:', err.message);
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
