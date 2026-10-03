import { Router } from 'express';
import fs from 'fs';
import yaml from 'js-yaml';
import { CONFIG_PATH } from '../db.js';

const router = Router();

const REQUIRED_FIELDS = ['pipeline', 'change_detection', 'relevance_scoring', 'semantic_scoring', 'graph', 'storage', 'notifications'];

function validateConfig(obj) {
  for (const field of REQUIRED_FIELDS) {
    if (!(field in obj)) return `Missing required top-level key: ${field}`;
  }
  if (obj.pipeline && typeof obj.pipeline !== 'object') return 'pipeline must be an object';
  if (obj.relevance_scoring && typeof obj.relevance_scoring !== 'object') return 'relevance_scoring must be an object';
  if (obj.semantic_scoring && typeof obj.semantic_scoring !== 'object') return 'semantic_scoring must be an object';
  return null;
}

// GET /api/config
router.get('/', (req, res) => {
  try {
    if (!fs.existsSync(CONFIG_PATH)) {
      return res.json({ data: {} });
    }
    const content = fs.readFileSync(CONFIG_PATH, 'utf8');
    const config = yaml.load(content);
    res.json({ data: config || {} });
  } catch (err) {
    console.error('[config] GET /:', err.message);
    res.status(500).json({ data: {}, error: err.message });
  }
});

// POST /api/config
router.post('/', (req, res) => {
  try {
    const body = req.body;
    if (!body || typeof body !== 'object') {
      return res.status(400).json({ error: 'Request body must be a JSON object' });
    }

    const validationError = validateConfig(body);
    if (validationError) {
      return res.status(400).json({ error: validationError });
    }

    const yamlStr = yaml.dump(body, { lineWidth: 120, noRefs: true });
    fs.writeFileSync(CONFIG_PATH, yamlStr, 'utf8');
    res.json({ success: true });
  } catch (err) {
    console.error('[config] POST /:', err.message);
    res.status(500).json({ error: err.message });
  }
});

export default router;
