// The interview and its task list: the next question, the tasks mentioned so far, and the participant's task
// list (streamed as server-sent events).
import express from 'express';
import { client } from '../lib/config.js';
import { plannerStep } from '../lib/planner.js';
import { extractMentionedTasks, roleFromTranscript, generateTasks } from '../lib/tasks.js';

const router = express.Router();

router.post('/api/extract-interview-tasks', async (req, res) => {
  const { backgroundTranscript = [] } = req.body;
  if (!Array.isArray(backgroundTranscript)) {
    return res.status(400).json({ error: 'backgroundTranscript must be an array' });
  }
  try {
    const tasks = await extractMentionedTasks(backgroundTranscript);
    // Log what the extractor pulled so grounding can be checked in the server log.
    console.log(`[extract-interview-tasks] role=${roleFromTranscript(backgroundTranscript) || '(none)'} count=${tasks.length}`);
    for (const t of tasks) console.log(`  • ${t}`);
    res.json({ tasks });
  } catch (err) {
    console.error('[extract-interview-tasks]', err);
    res.status(500).json({ error: err.message });
  }
});

router.post('/api/planner-next', async (req, res) => {
  const { turns = [], state = null } = req.body;
  if (!Array.isArray(turns)) {
    return res.status(400).json({ error: 'turns must be an array' });
  }
  try {
    const result = await plannerStep(client, { turns, state });
    console.log(`[planner-next] turn=${turns.length} phase=${result.phase} done=${result.done}${result.stopReason ? ` stop=${result.stopReason}` : ''} q="${(result.question || '').slice(0, 60)}"`);
    res.json(result);
  } catch (err) {
    console.error('[planner-next]', err);
    res.status(500).json({ error: err.message });
  }
});

router.post('/api/generate-tasks-from-interview', async (req, res) => {
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.flushHeaders?.();

  const sendEvent = (event, data) => {
    res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
    res.flush?.();
  };

  const { jobTitle, interviewTasks = [] } = req.body;
  try {
    const { normalized, shown, gapNames } = await generateTasks(req.body, (name, source) => sendEvent('task', { name, source }));
    console.log(`[generate-tasks-from-interview] role=${jobTitle} mentioned=${interviewTasks.length} normalized=${normalized}->shown=${shown} gapfill=${gapNames.length}`);
    for (const g of gapNames) console.log(`    + (gap) ${g}`);
    sendEvent('done', {});
    res.end();
  } catch (err) {
    console.error('[generate-tasks-from-interview]', err);
    sendEvent('error', { message: err.message });
    res.end();
  }
});

export default router;
