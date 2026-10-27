// The app's folders, resolved once here so no other file counts its own way up to the app root (interviewer/).
// No imports beyond node, so scripts can use it without the model client.
import path from 'path';
import { fileURLToPath } from 'url';

export const APP_ROOT = fileURLToPath(new URL('..', import.meta.url));
export const DATA_DIR = path.join(APP_ROOT, 'data');     // the shipped O*NET lookup files
export const DIST_DIR = path.join(APP_ROOT, 'dist');     // the built front end, served in production

// A path setting from the environment, else `def`. A relative value is taken from the app root, so it means the
// same thing whatever directory the server was started in.
export const envPath = (name, def) => (process.env[name] ? path.resolve(APP_ROOT, process.env[name]) : def);
