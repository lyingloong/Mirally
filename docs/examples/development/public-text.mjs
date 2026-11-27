/** Public-text hygiene for everything this workflow posts to GitHub.
 *
 * Pull-request titles and bodies, merge-brief sections and requirement issues are
 * public content. They must not carry machine-specific absolute paths, the local
 * account name, the host name or credentials, so every publication step funnels
 * its text through publicText() and then asserts the result is clean before it is
 * written to the store or posted. Redaction is deterministic: the same input
 * always produces the same published text, and raw evidence stays in the local
 * artifact/review files where it belongs.
 *
 * The rules discover the machine identity at run time (environment plus OS); no
 * developer name, drive letter or install location is hard-coded, so the module
 * behaves the same on any Windows, macOS or Linux checkout.
 */
import os from 'node:os';
import path from 'node:path';

// A path body stops at whitespace, quotes, brackets and markup so surrounding
// prose and Markdown code spans survive; trailing punctuation is put back.
const TAIL = '[^\\s`\'"()\\[\\]<>|]';
// POSIX roots that hold a user or a checkout. A curated list, not "any slash
// path": repo-relative paths and documented API routes must survive unchanged.
const POSIX_ROOTS = 'Users|home|mnt|media|srv|opt|var|tmp|data|exports|Volumes|private';
const ABSOLUTE_PATH = new RegExp(
  `(?:file:\\/{2,3})?(?:`
  + `(?<![A-Za-z0-9])[A-Za-z]:[\\\\/]${TAIL}+` // C:\Users\name\Code\... or C:/Users/name
  + `|\\\\\\\\${TAIL}+` // \\server\share\...
  + `|(?<![A-Za-z0-9.])[\\\\/](?:${POSIX_ROOTS})[\\\\/]${TAIL}+` // \Users\name\... or /home/name/...
  + `)`, 'gi');
const CREDENTIAL = /\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16})\b/g;

// Words that may legitimately name a machine account but must never be stripped
// from prose (generic Windows/CI account names and directory words).
const DENY = new Set(['users', 'home', 'user', 'public', 'temp', 'tmp', 'windows', 'localhost', 'administrator', 'admin', 'default', 'defaultuser', 'docker', 'runner', 'containerd', 'packer', 'packeruser', 'guest', 'system', 'network', 'daily', 'mirally']);

const escapePattern = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const PATH_PLACEHOLDER = '<local-path>';
const IDENTITY_PLACEHOLDER = '<local-user>';

function homeDirectory() {
  try { return os.homedir() || ''; } catch { return ''; }
}

/** Machine-specific identity strings that must never appear in public text.
 * Longest first, so a host name such as `name-pc` is replaced before `name`. */
export function localIdentityMarkers() {
  const found = new Set();
  const add = value => {
    const marker = String(value ?? '').trim();
    if (marker.length < 4 || !/[A-Za-z]/.test(marker) || DENY.has(marker.toLowerCase())) return;
    found.add(marker);
  };
  add(process.env.USERNAME); add(process.env.USER); add(process.env.LNAME);
  const home = homeDirectory();
  add(home); add(home ? path.basename(home) : '');
  try { add(os.userInfo().username); } catch { /* identity unavailable */ }
  try { add(os.hostname()); } catch { /* identity unavailable */ }
  return [...found].sort((left, right) => right.length - left.length || left.localeCompare(right));
}

/** One marker as a whole-word pattern. Only a `/` (a URL path segment such as
 * `github.com/<login>`) or an `@` mention is left alone: those name a public
 * GitHub identity, not the local account. */
function markerPattern(marker) {
  return new RegExp(`(?<![/@\\w])${escapePattern(marker)}(?![/\\w])`, 'gi');
}

function keepTrailingPunctuation(match) {
  const split = /^([\s\S]*?)([.,;:!?]+)$/.exec(match);
  return split && split[1] ? `${PATH_PLACEHOLDER}${split[2]}` : PATH_PLACEHOLDER;
}

function resolveMarkers(options) {
  const supplied = Array.isArray(options?.markers) ? options.markers : null;
  if (!supplied) return localIdentityMarkers();
  const seen = new Set();
  for (const value of supplied) {
    const marker = String(value ?? '').trim();
    if (marker) seen.add(marker);
  }
  return [...seen].sort((left, right) => right.length - left.length || left.localeCompare(right));
}

/** Deterministic sanitization of one public-text field: credentials, absolute
 * local paths and machine identity are replaced by stable placeholders.
 * `options.markers` overrides the discovered identity for tests and for callers
 * that know additional machine-specific names. */
export function publicText(value, options = {}) {
  let text = String(value ?? '')
    .replace(CREDENTIAL, '<redacted>')
    .replace(ABSOLUTE_PATH, keepTrailingPunctuation);
  for (const marker of resolveMarkers(options)) text = text.replace(markerPattern(marker), IDENTITY_PLACEHOLDER);
  return text;
}

/** Machine-specific text still present after sanitization. The patterns are the
 * ones publicText() promises to remove, so a non-empty result is a sanitizer
 * bug rather than unusual input. */
export function findLocalLeaks(value, options = {}) {
  const text = String(value ?? '');
  const leaks = [];
  for (const match of text.matchAll(ABSOLUTE_PATH)) leaks.push(match[0]);
  const credentials = text.match(CREDENTIAL);
  if (credentials) leaks.push(...credentials);
  for (const marker of resolveMarkers(options)) {
    const pattern = markerPattern(marker);
    const match = pattern.exec(text);
    if (match) leaks.push(match[0]);
  }
  return [...new Set(leaks)];
}

/** Fail closed: publication must not proceed with machine-specific text. */
export function assertPublicText(value, where, options = {}) {
  const leaks = findLocalLeaks(value, options);
  if (!leaks.length) return value;
  throw new Error(`${where} still contains machine-specific text after redaction: ${leaks.slice(0, 5).join(', ')}`);
}
