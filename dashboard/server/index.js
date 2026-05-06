import express from 'express';
import cors from 'cors';
import path from 'path';
import { fileURLToPath } from 'url';
import { basicAuth } from './auth.js';
import { syncDataFromRelease } from './syncData.js';

import runsRouter from './routes/runs.js';
import pagesRouter from './routes/pages.js';
import sourcesRouter from './routes/sources.js';
import configRouter from './routes/config.js';
import embeddingsRouter from './routes/embeddings.js';
import graphRouter from './routes/graph.js';
import snapshotsRouter from './routes/snapshots.js';
import healthRouter from './routes/health.js';
import feedbackRouter from './routes/feedback.js';
import sqlRouter from './routes/sql.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const app = express();
const PORT = process.env.PORT || 3001;

const corsOptions = process.env.NODE_ENV === 'production'
  ? { origin: process.env.DASHBOARD_ORIGIN }
  : { origin: true };
app.use(cors(corsOptions));

app.use(express.json());

app.use(basicAuth);

app.use('/api/runs', runsRouter);
app.use('/api/pages', pagesRouter);
app.use('/api/sources', sourcesRouter);
app.use('/api/config', configRouter);
app.use('/api/embeddings', embeddingsRouter);
app.use('/api/graph', graphRouter);
app.use('/api/snapshots', snapshotsRouter);
app.use('/api/health', healthRouter);
app.use('/api/feedback', feedbackRouter);
app.use('/api/sql', sqlRouter);

if (process.argv.includes('--serve-build')) {
  const distDir = path.join(__dirname, '..', 'dist');
  app.use(express.static(distDir));
  app.get('*', (req, res) => {
    res.sendFile(path.join(distDir, 'index.html'));
  });
}

syncDataFromRelease().finally(() => {
  app.listen(PORT, () => {
    console.log(`Tripwire Dashboard server running on port ${PORT}`);
  });
});
