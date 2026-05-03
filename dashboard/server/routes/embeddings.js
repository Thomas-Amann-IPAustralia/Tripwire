import { Router } from 'express';
import { PCA } from 'ml-pca';
import { db, dbGuard, getDbMtime } from '../db.js';

const router = Router();

const K_CLUSTERS = 8;

let chunkCache = null;
let chunkCacheMtime = null;

function deserialiseEmbedding(blob) {
  if (!blob) return null;
  const buf = Buffer.isBuffer(blob) ? blob : Buffer.from(blob);
  const arr = [];
  for (let i = 0; i < buf.length; i += 4) arr.push(buf.readFloatLE(i));
  return arr;
}

function normaliseAxis(values) {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  return values.map(v => ((v - min) / range) * 2 - 1);
}

function kMeans3D(points, k, maxIter = 100) {
  const n = points.length;
  if (n === 0) return [];
  k = Math.min(k, n);

  // Deterministic seed: evenly spaced initial centroids
  let centroids = Array.from({ length: k }, (_, i) => [...points[Math.floor((i * n) / k)]]);
  let assignments = new Array(n).fill(0);

  for (let iter = 0; iter < maxIter; iter++) {
    let changed = false;
    for (let i = 0; i < n; i++) {
      let best = 0, bestDist = Infinity;
      for (let c = 0; c < k; c++) {
        const dx = points[i][0] - centroids[c][0];
        const dy = points[i][1] - centroids[c][1];
        const dz = points[i][2] - centroids[c][2];
        const dist = dx * dx + dy * dy + dz * dz;
        if (dist < bestDist) { bestDist = dist; best = c; }
      }
      if (assignments[i] !== best) { assignments[i] = best; changed = true; }
    }
    if (!changed) break;

    const sums = Array.from({ length: k }, () => [0, 0, 0]);
    const counts = new Array(k).fill(0);
    for (let i = 0; i < n; i++) {
      sums[assignments[i]][0] += points[i][0];
      sums[assignments[i]][1] += points[i][1];
      sums[assignments[i]][2] += points[i][2];
      counts[assignments[i]]++;
    }
    for (let c = 0; c < k; c++) {
      if (counts[c] > 0) {
        centroids[c] = [
          sums[c][0] / counts[c],
          sums[c][1] / counts[c],
          sums[c][2] / counts[c],
        ];
      }
    }
  }
  return assignments;
}

function buildChunkCache() {
  // Query chunks joined to active pages.
  // chunk_embedding is per-chunk (BGE); falls back to doc_embedding when absent.
  // TODO: replace with per-chunk embeddings once all chunks have chunk_embedding populated
  const rows = db.prepare(`
    SELECT
      c.chunk_id,
      c.page_id,
      c.chunk_text,
      c.chunk_embedding,
      p.doc_embedding,
      p.title AS document_title
    FROM chunks c
    JOIN pages p ON p.page_id = c.page_id
    WHERE p.status = 'active'
    ORDER BY c.page_id, c.chunk_index
  `).all();

  if (!rows.length) return { data: [], mtime: getDbMtime() };

  // Prefer chunk_embedding; fall back to page doc_embedding
  const embeddings = rows.map(r => {
    const emb = deserialiseEmbedding(r.chunk_embedding) || deserialiseEmbedding(r.doc_embedding);
    return emb;
  });

  const validIndices = embeddings.map((e, i) => e ? i : -1).filter(i => i >= 0);
  const validEmbeddings = validIndices.map(i => embeddings[i]);

  if (validEmbeddings.length < 3) {
    return {
      data: rows.map(r => ({
        chunk_id: r.chunk_id,
        document_id: r.page_id,
        document_title: r.document_title || '',
        chunk_text: (r.chunk_text || '').slice(0, 200),
        x: 0, y: 0, z: 0,
        cluster_id: 0,
      })),
      mtime: getDbMtime(),
    };
  }

  const pca = new PCA(validEmbeddings);
  const proj = pca.predict(validEmbeddings, { nComponents: 3 }).to2DArray();

  const x = normaliseAxis(proj.map(r => r[0]));
  const y = normaliseAxis(proj.map(r => r[1]));
  const z = normaliseAxis(proj.map(r => r[2]));

  const points3d = x.map((xi, i) => [xi, y[i], z[i]]);
  const clusterAssignments = kMeans3D(points3d, K_CLUSTERS);

  // Build result, mapping validIndices back to all rows
  const coordMap = new Map();
  validIndices.forEach((origIdx, i) => {
    coordMap.set(origIdx, { x: x[i], y: y[i], z: z[i], cluster_id: clusterAssignments[i] ?? 0 });
  });

  const data = rows.map((r, i) => {
    const coords = coordMap.get(i) || { x: 0, y: 0, z: 0, cluster_id: 0 };
    return {
      chunk_id: r.chunk_id,
      document_id: r.page_id,
      document_title: r.document_title || '',
      chunk_text: (r.chunk_text || '').slice(0, 200),
      ...coords,
    };
  });

  return { data, mtime: getDbMtime() };
}

function getChunkCache() {
  const mtime = getDbMtime();
  if (!chunkCache || chunkCacheMtime !== mtime) {
    try {
      chunkCache = buildChunkCache();
      chunkCacheMtime = mtime;
    } catch (err) {
      console.error('[embeddings] Chunk cache build failed:', err.message);
      chunkCache = { data: [] };
      chunkCacheMtime = mtime;
    }
  }
  return chunkCache;
}

// GET /api/embeddings — chunk-level PCA + k-means projection
router.get('/', (req, res) => {
  if (!dbGuard(res)) return;
  try {
    const cache = getChunkCache();
    res.json({ data: cache.data });
  } catch (err) {
    console.error('[embeddings] GET /:', err.message);
    res.status(500).json({ data: [], error: err.message });
  }
});

export default router;
