// Where the survey reads its inputs and, without VALIDATION_DATABASE_URL, writes its records: validation/tools/,
// or $VALIDATION_DIR (relative to the repository root). The analysis tools import this too, so both read the
// same folder.
import path from 'path';
import { fileURLToPath } from 'url';

const REPO_ROOT = fileURLToPath(new URL('../..', import.meta.url));
export const VALIDATION_DIR = process.env.VALIDATION_DIR
  ? path.resolve(REPO_ROOT, process.env.VALIDATION_DIR)
  : path.join(REPO_ROOT, 'validation', 'tools');
export const VALIDATION_INPUTS_DIR = path.join(VALIDATION_DIR, 'inputs');
export const VALIDATION_OUT_DIR = path.join(VALIDATION_DIR, 'out');
export const COVERAGE_ASSIGN_DIR = path.join(VALIDATION_OUT_DIR, 'assignments', 'coverage');
export const COVERAGE_RESPONSE_DIR = path.join(VALIDATION_OUT_DIR, 'responses', 'coverage');
// Screener verdicts, including screen-outs (who never submit a response).
export const COVERAGE_FIT_DIR = path.join(VALIDATION_OUT_DIR, 'fit', 'coverage');
export const COVERAGE_DRAFT_DIR = path.join(VALIDATION_OUT_DIR, 'drafts', 'coverage');
