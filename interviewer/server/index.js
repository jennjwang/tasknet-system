// The interviewer's HTTP server. Routes only; the logic they call is in lib/.
import express from 'express';
import fs from 'fs/promises';
import path from 'path';
import { SESSIONS_DIR, SCREEN_OUTS_DIR, SCREENER_DIR, DIST_DIR } from '../lib/config.js';
import interviewRouter from './interview.js';
import sessionRouter from './session.js';
import occupationRouter from './occupation.js';
import validationRouter from '../../validation/server/validation.js';   // the validation survey (validation/)

for (const dir of [SESSIONS_DIR, SCREEN_OUTS_DIR, SCREENER_DIR]) await fs.mkdir(dir, { recursive: true });

const app = express();
app.use(express.json({ limit: '25mb' }));

// API routers, registered before the static catch-all.
app.use(interviewRouter);
app.use(sessionRouter);
app.use(occupationRouter);
app.use(validationRouter);

// In production, serve the Vite-built static frontend from the same Express
// process. In dev, Vite runs on a separate port and proxies /api/* to us.
if (process.env.NODE_ENV === 'production') {
  app.use(express.static(DIST_DIR));
  app.get('*', (req, res, next) => {
    if (req.path.startsWith('/api/')) return next();
    res.sendFile(path.join(DIST_DIR, 'index.html'));
  });
}

// Registered after the static block.
app.get('/health', (_req, res) => res.json({ ok: true }));
// The client's runtime config.
app.get('/api/config', (_req, res) => {
  const maxFails = Number(process.env.ATTN_CHECK_MAX_FAILS || 0);
  res.json({ attnCheckMaxFails: Number.isFinite(maxFails) ? maxFails : 0 });
});

const PORT = process.env.PORT ? Number(process.env.PORT) : 3001;
app.listen(PORT, () => console.log(`Server running on http://localhost:${PORT}`));
