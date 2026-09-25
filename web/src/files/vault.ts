import { api, postJSON } from '../lib/sse';

export interface VaultFile { path: string; size: number; mtime: number; changed: number }
export interface VaultIndex { files: VaultFile[]; dirs: string[]; attachments: string }
export interface VaultStatus {
  ahead: number; behind: number; dirty: string[];
  last: { hash: string; time: string; author: string; subject: string } | null;
  fetched_at: number | null;
  online?: boolean; pushed?: boolean; push_error?: string | null; copies?: string[];
}
export interface SaveResult {
  path: string; sha: string; merged: boolean; text: string | null;
  pushed: boolean; push_error: string | null; copies: string[];
}

// ── routes ────────────────────────────────────────────────────────
// A path is one URL segment, slashes included, so "#/file/nota/Daily%2F2026-09-25.md".
const enc = (p: string) => encodeURIComponent(p).replace(/\(/g, '%28').replace(/\)/g, '%29');
export const noteHref = (p: string) => `#/file/nota/${enc(p)}`;
export const editHref = (p: string) => `#/file/modifica/${enc(p)}`;
export const folderHref = (p: string) => (p ? `#/file/cartella/${enc(p)}` : '#/file/cartella');
export const rawUrl = (p: string) => `/api/vault/raw?path=${encodeURIComponent(p)}`;

export const baseName = (p: string) => p.slice(p.lastIndexOf('/') + 1);
export const dirName = (p: string) => (p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : '');
export const title = (p: string) => baseName(p).replace(/\.md$/i, '');
export const isNote = (p: string) => /\.(md|txt)$/i.test(p);
export const isImage = (p: string) => /\.(png|jpe?g|gif|webp|heic)$/i.test(p);
export const isPdf = (p: string) => /\.pdf$/i.test(p);

// ── the index, shared by every page of the tab ────────────────────
let index: VaultIndex | null = null;
let loading: Promise<VaultIndex> | null = null;

export function cachedIndex() { return index; }

export function loadIndex(force = false): Promise<VaultIndex> {
  if (index && !force) return Promise.resolve(index);
  if (!loading) {
    loading = api<VaultIndex>('/api/vault/index')
      .then(i => (index = i))
      .finally(() => { loading = null; });
  }
  return loading;
}

let lastSync = 0;
/** GitHub → server, at most once a minute unless asked; then a fresh index. */
export async function sync(force = false): Promise<VaultStatus | null> {
  if (!force && Date.now() - lastSync < 60_000) return null;
  lastSync = Date.now();
  const r = await postJSON('/api/vault/sync', {});
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
  await loadIndex(true);
  return d as VaultStatus;
}

// ── Obsidian's links ──────────────────────────────────────────────
/** Where [[target]] points, the way Obsidian finds it: exact path, next to
 *  the note, then any file with that name (the closest wins). */
export function resolveLink(target: string, from: string, files: VaultFile[]): string | null {
  const t = target.split('#')[0].split('^')[0].trim().replace(/^\/+/, '');
  if (!t) return from;
  const want = [t, t + '.md'].map(s => s.toLowerCase());
  const paths = files.map(f => f.path);
  const lower = paths.map(p => p.toLowerCase());
  for (const w of want) {
    const i = lower.indexOf(w);
    if (i >= 0) return paths[i];
  }
  const here = dirName(from);
  if (here) {
    for (const w of want) {
      const i = lower.indexOf(`${here}/${w}`.toLowerCase());
      if (i >= 0) return paths[i];
    }
  }
  const name = baseName(t).toLowerCase();
  const matches = paths.filter(p => {
    const b = baseName(p).toLowerCase();
    return b === name || b === name + '.md';
  });
  if (!matches.length) return null;
  const score = (p: string) => (dirName(p) === here ? 0 : 1000) + p.length;
  return matches.sort((a, b) => score(a) - score(b))[0];
}

/** Frontmatter off, tags out. */
export function splitFrontmatter(text: string): { body: string; tags: string[] } {
  const m = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(text);
  if (!m) return { body: text, tags: [] };
  const tags: string[] = [];
  const lines = m[1].split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const t = /^tags?:\s*(.*)$/.exec(lines[i]);
    if (!t) continue;
    if (t[1].trim()) {
      tags.push(...t[1].replace(/[[\]]/g, '').split(',').map(s => s.trim()).filter(Boolean));
    } else {
      for (i++; i < lines.length && /^\s+-\s*/.test(lines[i]); i++) tags.push(lines[i].replace(/^\s+-\s*/, '').trim());
      i--;
    }
  }
  return { body: text.slice(m[0].length), tags: tags.map(t => t.replace(/^#/, '').replace(/^["']|["']$/g, '')) };
}

const esc = (s: string) => s.replace(/([[\]\\*_`])/g, '\\$1');

function linkOut(inner: string, from: string, files: VaultFile[], embed: boolean) {
  const [target, alias] = inner.split('|');
  const resolved = resolveLink(target, from, files);
  const shown = target.trim().replace(/\.md$/i, '').replace('#', ' › ');
  if (embed && resolved && isImage(resolved)) return `![${esc(baseName(resolved))}](${rawUrl(resolved)})`;
  if (resolved && (isImage(resolved) || isPdf(resolved))) {
    return `[${esc(alias?.trim() || baseName(resolved))}](${rawUrl(resolved)})`;
  }
  const label = esc(alias && !/^\d+(x\d+)?$/.test(alias.trim()) ? alias.trim() : shown);
  // A link to a note that does not exist yet opens as "create it", like Obsidian.
  const dest = resolved || target.split('#')[0].trim().replace(/(\.md)?$/i, '.md');
  return `[${embed ? '↪ ' : ''}${label}](${noteHref(dest)})`;
}

function inline(segment: string, from: string, files: VaultFile[]) {
  return segment
    .replace(/!\[\[([^\]]+)\]\]/g, (_, inner) => linkOut(inner, from, files, true))
    .replace(/\[\[([^\]]+)\]\]/g, (_, inner) => linkOut(inner, from, files, false));
}

/** Obsidian's Markdown turned into plain CommonMark for micromark: wiki-links
 *  and embeds become ordinary links and images, callouts a bold title,
 *  %%comments%% disappear. Code blocks and inline code are left alone. */
export function toMarkdown(body: string, from: string, files: VaultFile[]) {
  const out: string[] = [];
  let fence: string | null = null;
  const text = body.replace(/%%[\s\S]*?%%/g, '');
  for (const line of text.split('\n')) {
    const f = /^\s*(`{3,}|~{3,})/.exec(line);
    if (f) {
      if (!fence) fence = f[1][0];
      else if (f[1][0] === fence) fence = null;
      out.push(line);
      continue;
    }
    if (fence) { out.push(line); continue; }
    let l = line.replace(/^(\s*(?:>\s*)+)\[!(\w+)\][+-]?\s*(.*)$/, (_, q, type, t) => `${q}**${t || type}**`);
    // Only outside `inline code`.
    l = l.split(/(`+[^`]*`+)/).map((part, i) => (i % 2 ? part : inline(part, from, files))).join('');
    out.push(l);
  }
  return out.join('\n');
}

// ── saving ────────────────────────────────────────────────────────
export class ConflictError extends Error {
  constructor(public theirs: string | null, public theirsSha: string | null) {
    super('la nota è cambiata altrove mentre la modificavi');
  }
}

export async function saveNote(path: string, text: string, base: string | null): Promise<SaveResult> {
  const r = await postJSON('/api/vault/note', { path, text, base });
  const d = await r.json().catch(() => ({}));
  if (r.status === 409 && d.conflict) throw new ConflictError(d.theirs ?? null, d.theirs_sha ?? null);
  if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
  loadIndex(true).catch(() => {});
  return d as SaveResult;
}

export function pushNote(r: { pushed?: boolean; push_error?: string | null; copies?: string[] }) {
  if (r.copies?.length) return `In conflitto con GitHub: la versione del telefono è in «${r.copies.map(title).join('», «')}».`;
  if (r.pushed === false) return 'Salvato sul server; GitHub non ha risposto, lo invio alla prossima sincronizzazione.';
  return '';
}

// Local date and time, not the server's: the daily note is the day you are living.
export function today() {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  return { date: `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`, time: `${p(d.getHours())}:${p(d.getMinutes())}` };
}

// ── drafts: an edit survives a reload or a closed app ─────────────
const DRAFT = 'hub.draft:';
export interface Draft { text: string; base: string | null; at: number }

export function readDraft(path: string): Draft | null {
  try { return JSON.parse(localStorage.getItem(DRAFT + path) || 'null'); } catch { return null; }
}
export function writeDraft(path: string, d: Draft) {
  try { localStorage.setItem(DRAFT + path, JSON.stringify(d)); } catch { /* private mode: no drafts */ }
}
export function dropDraft(path: string) {
  try { localStorage.removeItem(DRAFT + path); } catch { /* ignore */ }
}
