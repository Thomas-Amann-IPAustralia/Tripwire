import { Router } from 'express';
import { PCA } from 'ml-pca';
import { db, dbGuard, getDbMtime } from '../db.js';

const router = Router();

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
        COUNT(DISTINCT e.id)       AS entity_count,
        COUNT(DISTINCT pr.id)      AS alert_count
      FROM pages p
      LEFT JOIN chunks   c  ON c.page_id  = p.page_id
      LEFT JOIN entities e  ON e.page_id  = p.page_id
      LEFT JOIN pipeline_runs pr ON pr.triggered_pages LIKE '%' || p.page_id || '%'
        AND json_extract(pr.details, '$.stages.llm_assessment.verdict') = 'CHANGE_REQUIRED'
      WHERE p.status = 'active'
      GROUP BY p.page_id
    `).all();

    const cache = getCache();

    const data = rows.map(row => {
      const emb = cache.byPageId[row.page_id] || {};
      return {
        ...row,
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
      SELECT run_id, source_id, timestamp, outcome,
        json_extract(details, '$.stages.llm_assessment.verdict')    AS verdict,
        json_extract(details, '$.stages.llm_assessment.confidence') AS confidence
      FROM pipeline_runs
      WHERE triggered_pages LIKE ?
      ORDER BY timestamp DESC LIMIT 50
    `).all(`%${page_id}%`);

    const alert_count = alerts.filter(a => a.verdict === 'CHANGE_REQUIRED').length;

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

export { getCache };
export default router;
