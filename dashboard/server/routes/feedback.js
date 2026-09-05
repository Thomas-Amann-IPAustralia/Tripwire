import { Router } from 'express';
import fs from 'fs';
import path from 'path';
import { FEEDBACK_PATH } from '../db.js';

const router = Router();

// POST /api/feedback/submit
router.post('/submit', (req, res) => {
  try {
    const body = req.body;
    if (!body || !body.run_id || !body.category) {
      return res.status(400).json({ error: 'run_id and category are required' });
    }

    const record = {
      run_id: body.run_id,
      page_id: body.page_id ?? null,
      source_id: body.source_id ?? null,
      category: body.category,
      comment: body.comment ?? '',
      ingested_at: new Date().toISOString(),
    };

    const dir = path.dirname(FEEDBACK_PATH);
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }

    fs.appendFileSync(FEEDBACK_PATH, JSON.stringify(record) + '\n', 'utf8');
    res.json({ success: true });
  } catch (err) {
    console.error('[feedback] POST /submit:', err.message);
    res.status(500).json({ error: err.message });
  }
});

export default router;
