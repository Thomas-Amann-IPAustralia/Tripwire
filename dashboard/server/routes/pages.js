import { Router } from 'express';
import { PCA } from 'ml-pca';
import { db, dbGuard, getDbMtime } from '../db.js';
import { loadAcks, acknowledgePage, clearAcknowledgment, outstandingCount } from '../acks.js';

const router = Router();

// Per-page CHANGE_REQUIRED alert timestamps, with acknowledgments applied.
// Returns { alertsByPage: { page_id: { count, total, last_alert } }, acks }.
export function computeAlertCounts() {
  const acks = loadAcks();
  const rows = db.prepare(`
    SELECT ipfr_page_id, generated_at
    FROM llm_assessments
    WHERE verdict = 'CHANGE_REQUIRED'
  `).all();

  const byPage = {};
  for (const row of rows) {
    (byPage[row.ipfr_page_id] ??= []).push(row.generated_at);
  }

  const alertsByPage = {};
  for (const [pageId, timestamps] of Object.entries(byPage)) {
    timestamps.sort();
    alertsByPage[pageId] = {
      count: outstandingCount(timestamps, acks[pageId]),
      total: timestamps.length,
      last_alert: timestamps[timestamps.length - 1] ?? null,
    };
  }
  return { alertsByPage, acks };
}

let embeddingCache = null;
let cacheMtime = null;

function deserialiseEmbedding(blob) {
  if (!blob) return null;
  const buf = Buffer.isBuffer(blob) ? blob : Buffer.from(blob);
  const arr = [];
  for (let i = 0; i < buf.length; i += 4) {
    arr.push(buf.readFloatLE(i));
  }
  return arr;
}

function normaliseAxis(values) {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  return values.map(v => ((v - min) / range) * 2 - 1);
}

function kMeans(points, k, maxIter = 100) {
  const n = points.length;
  if (n === 0) return [];
  k = Math.min(k, n);

  // Deterministic seed: pick evenly spaced initial centroids
  let centroids = Array.from({ length: k }, (_, i) => [...points[Math.floor((i * n) / k)]]);
  let assignments = new Array(n).fill(0);

  for (let iter = 0; iter < maxIter; iter++) {
    let changed = false;

    for (let i = 0; i < n; i++) {
      let best = 0;
      let bestDist = Infinity;
      for (let c = 0; c < k; c++) {
        const dx = points[i][0] - centroids[c][0];
        const dy = points[i][1] - centroids[c][1];
        const dist = dx * dx + dy * dy;
        if (dist < bestDist) { bestDist = dist; best = c; }
      }
      if (assignments[i] !== best) { assignments[i] = best; changed = true; }
    }

    if (!changed) break;

    const sums = Array.from({ length: k }, () => [0, 0]);
    const counts = new Array(k).fill(0);
    for (let i = 0; i < n; i++) {
      sums[assignments[i]][0] += points[i][0];
      sums[assignments[i]][1] += points[i][1];
      counts[assignments[i]]++;
    }
    for (let c = 0; c < k; c++) {
      if (counts[c] > 0) {
        centroids[c][0] = sums[c][0] / counts[c];
        centroids[c][1] = sums[c][1] / counts[c];
      }
    }
  }

  return assignments;
}

function buildEmbeddingCache() {
  const rows = db.prepare(`
    SELECT page_id, doc_embedding FROM pages
    WHERE status = 'active' AND doc_embedding IS NOT NULL
  `).all();

  if (!rows.length) return { byPageId: {}, mtime: getDbMtime() };

  const pageIds = rows.map(r => r.page_id);
  const embeddings = rows.map(r => deserialiseEmbedding(r.doc_embedding)).filter(Boolean);
  const validPageIds = rows.filter(r => deserialiseEmbedding(r.doc_embedding)).map(r => r.page_id);

  if (embeddings.length < 3) {
    const byPageId = {};
    validPageIds.forEach((id, i) => {
      byPageId[id] = { embedding_2d: [0, 0], embedding_3d: [0, 0, 0], cluster: 0 };
    });
    return { byPageId, mtime: getDbMtime() };
  }

  const pca = new PCA(embeddings);
  const proj3dMatrix = pca.predict(embeddings, { nComponents: 3 }).to2DArray();
  const proj2dMatrix = pca.predict(embeddings, { nComponents: 2 }).to2DArray();

  const x3 = normaliseAxis(proj3dMatrix.map(r => r[0]));
  const y3 = normaliseAxis(proj3dMatrix.map(r => r[1]));
  const z3 = normaliseAxis(proj3dMatrix.map(r => r[2]));

  const x2 = normaliseAxis(proj2dMatrix.map(r => r[0]));
  const y2 = normaliseAxis(proj2dMatrix.map(r => r[1]));

  const points2d = x2.map((x, i) => [x, y2[i]]);
  const clusters = kMeans(points2d, 7);

  const byPageId = {};
  validPageIds.forEach((id, i) => {
    byPageId[id] = {
      embedding_2d: [x2[i], y2[i]],
      embedding_3d: [x3[i], y3[i], z3[i]],
      cluster: clusters[i] ?? 0,
    };
  });

  return { byPageId, mtime: getDbMtime() };
}

function getCache() {
  const mtime = getDbMtime();
  if (!embeddingCache || cacheMtime !== mtime) {
    try {
      embeddingCache = buildEmbeddingCache();
      cacheMtime = mtime;
    } catch (err) {
      console.error('[pages] Embedding cache build failed:', err.message);
      embeddingCache = { byPageId: {} };
      cacheMtime = mtime;
    }
  }
  return embeddingCache;
}

// GET /api/pages
router.get('/', (req, res) => {
  if (!dbGuard(res)) return;

  try {
    const rows = db.prepare(`
      SELECT
        p.page_id, p.url, p.title, p.last_modified, p.last_ingested, p.status,
        COUNT(DISTINCT c.chunk_id) AS chunk_count,
        COUNT(DISTINCT e.id)       AS entity_count
      FROM pages p
      LEFT JOIN chunks   c  ON c.page_id  = p.page_id
      LEFT JOIN entities e  ON e.page_id  = p.page_id
      WHERE p.status = 'active'
      GROUP BY p.page_id
    `).all();

    // Alert counts come from the llm_assessments table (one row per assessed
    // IPFR page per run). Fetched separately and merged in JS — joining it into
    // the query above via a triggered_pages LIKE scan over pipeline_runs is
    // pathologically slow and, since verdicts moved out of pipeline_runs.details,
    // no longer returns anything anyway. alert_count excludes acknowledged
    // alerts; alert_count_total is the all-time figure.
    const { alertsByPage, acks } = computeAlertCounts();

    const cache = getCache();

    const data = rows.map(row => {
      const emb = cache.byPageId[row.page_id] || {};
      const alerts = alertsByPage[row.page_id];
      return {
        ...row,
        alert_count: alerts?.count ?? 0,
        alert_count_total: alerts?.total ?? 0,
        last_alert: alerts?.last_alert ?? null,
        acknowledged_at: acks[row.page_id] ?? null,
        embedding_2d: emb.embedding_2d ?? null,
        embedding_3d: emb.embedding_3d ?? null,
        cluster: emb.cluster ?? null,
      };
    });

    res.json({ data });
  } catch (err) {
    console.error('[pages] GET /:', err.message);
    res.status(500).json({ data: [], error: err.message });
  }
});

// GET /api/pages/:page_id
router.get('/:page_id', (req, res) => {
  if (!dbGuard(res)) return;

  const { page_id } = req.params;

  try {
    const page = db.prepare(`
      SELECT
        p.page_id, p.url, p.title, p.content, p.last_modified, p.last_ingested, p.status,
        COUNT(DISTINCT c.chunk_id) AS chunk_count,
        COUNT(DISTINCT e.id)       AS entity_count
      FROM pages p
      LEFT JOIN chunks   c ON c.page_id = p.page_id
      LEFT JOIN entities e ON e.page_id = p.page_id
      WHERE p.page_id = ? AND p.status = 'active'
      GROUP BY p.page_id
    `).get(page_id);

    if (!page) return res.json({ data: null });

    const keyphrases = db.prepare(`
      SELECT keyphrase, score FROM keyphrases
      WHERE page_id = ? ORDER BY score ASC LIMIT 10
    `).all(page_id);

    const entities = db.prepare(`
      SELECT entity_text, entity_type FROM entities
      WHERE page_id = ?
    `).all(page_id);

    const neighbours = db.prepare(`
      SELECT ge.target_page_id AS page_id, p.title, ge.edge_type, ge.weight
      FROM graph_edges ge
      JOIN pages p ON p.page_id = ge.target_page_id
      WHERE ge.source_page_id = ?
      ORDER BY ge.weight DESC LIMIT 5
    `).all(page_id);

    const alerts = db.prepare(`
      SELECT
        la.run_id,
        la.generated_at AS timestamp,
        la.verdict,
        la.confidence,
        (SELECT pr.source_id FROM pipeline_runs pr
           WHERE pr.run_id = la.run_id
             AND pr.triggered_pages LIKE '%' || la.ipfr_page_id || '%'
           LIMIT 1) AS source_id,
        (SELECT pr.outcome FROM pipeline_runs pr
           WHERE pr.run_id = la.run_id
             AND pr.triggered_pages LIKE '%' || la.ipfr_page_id || '%'
           LIMIT 1) AS outcome
      FROM llm_assessments la
      WHERE la.ipfr_page_id = ?
      ORDER BY la.generated_at DESC LIMIT 50
    `).all(page_id);

    const acks = loadAcks();
    const ackTs = acks[page_id] ?? null;
    const changeRequired = alerts.filter(a => a.verdict === 'CHANGE_REQUIRED');
    const alert_count = outstandingCount(changeRequired.map(a => a.timestamp), ackTs);

    const cache = getCache();
    const emb = cache.byPageId[page_id] || {};

    res.json({
      data: {
        ...page,
        keyphrases,
        entities,
        neighbours,
        alerts,
        alert_count,
        alert_count_total: changeRequired.length,
        acknowledged_at: ackTs,
        embedding_2d: emb.embedding_2d ?? null,
        embedding_3d: emb.embedding_3d ?? null,
        cluster: emb.cluster ?? null,
      },
    });
  } catch (err) {
    console.error('[pages] GET /:page_id:', err.message);
    res.status(500).json({ data: null, error: err.message });
  }
});

// POST /api/pages/:page_id/acknowledge — mark a page's outstanding
// CHANGE_REQUIRED flags as reviewed. Alerts generated after this moment
// re-flag the page automatically.
router.post('/:page_id/acknowledge', (req, res) => {
  try {
    const acknowledged_at = acknowledgePage(req.params.page_id);
    res.json({ success: true, page_id: req.params.page_id, acknowledged_at });
  } catch (err) {
    console.error('[pages] POST /:page_id/acknowledge:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// DELETE /api/pages/:page_id/acknowledge — undo a reset, restoring the
// page's full alert history as outstanding.
router.delete('/:page_id/acknowledge', (req, res) => {
  try {
    const removed = clearAcknowledgment(req.params.page_id);
    res.json({ success: true, page_id: req.params.page_id, removed });
  } catch (err) {
    console.error('[pages] DELETE /:page_id/acknowledge:', err.message);
    res.status(500).json({ error: err.message });
  }
});

export { getCache };
export default router;
