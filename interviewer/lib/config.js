// The app's settings: its folders, the gateway client and model names, the screener target, and where sessions
// are stored (server/index.js creates the session folders at startup).
import OpenAI from 'openai';
import path from 'path';
import { APP_ROOT, envPath } from './paths.js';

export { APP_ROOT, DATA_DIR, DIST_DIR } from './paths.js';

// One OpenAI-SDK client for every model call: OpenAI itself (OPENAI_API_KEY), or an OpenAI-compatible gateway
// (LLM_BASE_URL + LLM_API_KEY, or CLAUDE_BASE_URL + CLAUDE_API_KEY). For Claude models served that way it papers
// over two quirks so call sites can stay OpenAI-style:
//   1. Newer Claude models reject temperature/top_p/top_k with a 400, so those are stripped for them.
//   2. The gateway's json_object mode makes Claude return "{}" on large prompts, so response_format is
//      dropped for every Claude model and a "JSON only" instruction is added to the system prompt instead.
const isClaude = (model) => typeof model === 'string' && /claude/i.test(model);

// Models that reject sampling params (Sonnet 4.6 and Haiku still accept them).
const stripsSampling = (model) =>
  typeof model === 'string' && /(opus-4-[6-9]|opus-4-\d\d|sonnet-5|fable-5|mythos-5)/i.test(model);

const JSON_ONLY_NOTE =
  '\n\nIMPORTANT: Respond with ONLY a single valid JSON object as specified above — ' +
  'no markdown, no code fences, no prose before or after.';

// Append the JSON-only note to the first system message, or prepend one.
function enforceJson(messages = []) {
  const msgs = messages.map((m) => ({ ...m }));
  const sysIdx = msgs.findIndex((m) => m.role === 'system');
  if (sysIdx >= 0) msgs[sysIdx].content = `${msgs[sysIdx].content}${JSON_ONLY_NOTE}`;
  else msgs.unshift({ role: 'system', content: JSON_ONLY_NOTE.trim() });
  return msgs;
}

function makeGatewayClient() {
  const env = process.env;
  const baseURL = env.LLM_BASE_URL || env.CLAUDE_BASE_URL || env.OPENAI_BASE_URL;
  const client = new OpenAI({
    apiKey: env.LLM_API_KEY || env.CLAUDE_API_KEY || env.OPENAI_API_KEY,
    ...(baseURL ? { baseURL } : {}),
  });

  const orig = client.chat.completions.create.bind(client.chat.completions);
  client.chat.completions.create = (body, opts) => {
    if (body && isClaude(body.model) && !baseURL) {
      throw new Error(`${body.model} needs an OpenAI-compatible endpoint that serves Claude models: `
        + 'set CLAUDE_BASE_URL (or LLM_BASE_URL) in interviewer/.env');
    }
    if (body && stripsSampling(body.model)) {
      const { temperature, top_p, top_k, ...rest } = body;
      body = rest;
    }
    if (body && isClaude(body.model) && body.response_format?.type === 'json_object') {
      const { response_format, ...rest } = body;
      body = { ...rest, messages: enforceJson(rest.messages) };
    }
    return orig(body, opts);
  };

  return client;
}

// Every model call goes through this one client.
export const client = makeGatewayClient();
// Audio transcription has no Claude equivalent, so /api/transcribe uses OpenAI directly. Built on the first
// request, so the server starts without OPENAI_API_KEY and only voice input needs it.
let transcribeClient = null;
export function getTranscribeClient() {
  if (!transcribeClient) {
    if (!process.env.OPENAI_API_KEY) {
      throw new Error('Audio transcription needs OPENAI_API_KEY (set it in interviewer/.env); typing still works without it.');
    }
    transcribeClient = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });
  }
  return transcribeClient;
}
// The defaults are the models the paper reports (Appendix A).
// Task extraction, writing and gap-fill.
export const MODEL = process.env.MODEL || 'gpt-5.4';
// Cheaper model for high-volume, lower-stakes calls (the screener check).
export const SMALL_MODEL = process.env.SMALL_MODEL || 'gpt-4o-mini';

// SCREENER_TARGET_SOC: the O*NET-SOC code(s) being recruited, comma-separated. Unset = no screener, and the flow
// is consent → interview → occupation self-ID.
export const SCREENER_TARGET_CODES = (process.env.SCREENER_TARGET_SOC || '')
  .split(',')
  .map((c) => c.trim())
  .filter(Boolean);
export const screenerEnabled = () => SCREENER_TARGET_CODES.length > 0;

// Where sessions are written; point SESSIONS_DIR at durable storage when deploying.
export const SESSIONS_DIR = envPath('SESSIONS_DIR', path.join(APP_ROOT, 'sessions'));
export const SCREEN_OUTS_DIR = path.join(SESSIONS_DIR, 'screen-outs');
// Every screener verdict, passes included, so rejections can be audited against the answers behind them.
export const SCREENER_DIR = path.join(SESSIONS_DIR, 'screener');
