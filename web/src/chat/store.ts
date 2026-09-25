// The conversation on screen: one Hermes session, voice and text alike.
//
// Hermes keeps the history; this only holds what is drawn and the turn in
// flight. A turn outlives the connection on the hub side, so when iOS drops
// the stream (the app left the screen) the store picks it up again from the
// last event it saw instead of losing the reply or a pending approval.

import { api, postJSON, sseEvents } from '../lib/sse';

export type Item =
  | { id: number; role: 'user'; text: string; voice?: boolean }
  | { id: number; role: 'assistant'; text: string; streaming?: boolean }
  | { id: number; role: 'tool'; name: string; preview: string; state: 'running' | 'done' | 'error' }
  | { id: number; role: 'note'; text: string };

export interface Approval {
  runId: string;
  requestId: string | null;
  command: string;
  description: string;
  choices: string[];
}

export interface ChatSnap {
  sessionId: string | null;
  title: string;
  source: string;
  items: Item[];
  loading: boolean;
  running: boolean;
  approval: Approval | null;
}

interface SendOpts {
  voice?: boolean;
  onSentence?: (text: string) => void;
}

type Listener = (s: ChatSnap) => void;

const SESSION_KEY = 'hub.session';
const RUN_KEY = 'hub.run';

function store(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch { /* private mode: nothing survives a reload, which is fine */ }
}

function load(key: string) {
  try { return localStorage.getItem(key); } catch { return null; }
}

let nextId = 1;
const withIds = (items: any[]): Item[] => items.map(i => ({ ...i, id: nextId++ }));

function toolUpdate(items: Item[], ev: any): Item[] {
  // Text before a tool call is its own bubble; what follows starts a new one.
  const closed = items.map(i => (i.role === 'assistant' && i.streaming ? { ...i, streaming: false } : i));
  if (ev.state === 'running') {
    return [...closed, { id: nextId++, role: 'tool', name: ev.name, preview: ev.preview, state: 'running' }];
  }
  for (let k = closed.length - 1; k >= 0; k--) {
    const it = closed[k];
    if (it.role === 'tool' && it.name === ev.name && it.state === 'running') {
      return [...closed.slice(0, k), { ...it, state: ev.state }, ...closed.slice(k + 1)];
    }
  }
  return [...closed, { id: nextId++, role: 'tool', name: ev.name, preview: ev.preview, state: ev.state }];
}

export class ChatStore {
  onError: (msg: string) => void = () => {};

  private snap: ChatSnap = {
    sessionId: null, title: '', source: 'api_server', items: [],
    loading: false, running: false, approval: null,
  };
  private listeners = new Set<Listener>();

  private runId: string | null = null;
  // The conversation the turn in flight belongs to. The user may open another
  // one meanwhile; the turn goes on, but must not write into that thread.
  private runSession: string | null = null;
  private lastSeq = -1;
  private detached = false;      // the stream dropped before the turn ended
  private opts: SendOpts = {};
  private openToken = 0;

  get snapshot() { return this.snap; }

  subscribe(fn: Listener) {
    this.listeners.add(fn);
    fn(this.snap);
    return () => { this.listeners.delete(fn); };
  }

  private set(patch: Partial<ChatSnap>) {
    this.snap = { ...this.snap, ...patch };
    this.listeners.forEach(fn => fn(this.snap));
  }

  private setItems(fn: (items: Item[]) => Item[]) {
    this.set({ items: fn(this.snap.items) });
  }

  // ── sessions ────────────────────────────────────────────────────
  init() {
    addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') this.resume();
    });
    addEventListener('online', () => this.resume());
    const id = load(SESSION_KEY);
    if (id) this.open(id);
  }

  async open(id: string) {
    const token = ++this.openToken;
    if (id !== this.snap.sessionId) {
      this.set({ sessionId: id, title: '', items: [], loading: true });
    } else {
      this.set({ loading: true });
    }
    store(SESSION_KEY, id);
    try {
      const data = await api(`/api/sessions/${encodeURIComponent(id)}`);
      if (token !== this.openToken) return;
      this.set({
        items: withIds(data.items),
        title: data.session.title || '',
        source: data.session.source || 'api_server',
        loading: false,
      });
    } catch (e: any) {
      if (token !== this.openToken) return;
      this.set({ loading: false });
      if (e.status === 404) { this.newChat(); return; }
      this.onError('Conversazione: ' + e.message);
      return;
    }
    // A turn that was running when the page was last closed: watch it to the
    // end, then show the transcript Hermes kept.
    const saved = load(RUN_KEY);
    if (saved && !this.snap.running) {
      const [runId, sessionId] = saved.split(' ');
      if (sessionId === id) this.watch(runId);
      else store(RUN_KEY, null);
    }
  }

  newChat() {
    ++this.openToken;
    store(SESSION_KEY, null);
    this.set({ sessionId: null, title: '', source: 'api_server', items: [], loading: false });
  }

  // ── turns ───────────────────────────────────────────────────────
  /** Send a message; resolves when the turn is over (or its stream is lost). */
  async send(text: string, opts: SendOpts = {}) {
    if (this.snap.running) await this.stop();
    this.opts = opts;
    this.runSession = this.snap.sessionId;
    this.runId = null;
    this.lastSeq = -1;
    this.detached = false;
    this.setItems(items => [...items, { id: nextId++, role: 'user', text, voice: opts.voice }]);
    this.set({ running: true });
    let res: Response;
    try {
      res = await postJSON('/api/chat', { message: text, session_id: this.snap.sessionId, voice: !!opts.voice });
    } catch (e: any) {
      this.finish();
      this.onError('Rete: ' + e.message);
      return;
    }
    if (!res.ok || !res.body) {
      const err = await res.json().catch(() => ({}));
      this.finish();
      this.onError('Chat: ' + (err.error || res.status));
      return;
    }
    await this.consume(res, 'live');
  }

  /** Ask Hermes to stop the turn in flight. */
  async stop() {
    const runId = this.runId;
    if (!runId) return;
    try {
      await api(`/api/runs/${runId}/stop`, { method: 'POST' });
    } catch (e: any) {
      // Already over: nothing to stop.
      if (e.status !== 409) this.onError('Stop: ' + e.message);
    }
  }

  async answer(choice: 'once' | 'session' | 'deny') {
    const a = this.snap.approval;
    if (!a) return;
    this.set({ approval: null });
    let res: Response;
    try {
      res = await postJSON(`/api/runs/${a.runId}/approval`, { choice, request_id: a.requestId });
    } catch (e: any) {
      this.onError('Approvazione non inviata: ' + e.message);
      return;
    }
    if (res.status === 409) {
      this.onError('La richiesta era già scaduta: Hermes non ha eseguito il comando.');
    } else if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      this.onError('Approvazione: ' + (err.error || res.status));
    }
  }

  /** Pick a dropped stream up again from the last event seen. */
  async resume() {
    if (!this.detached || !this.runId) return;
    this.detached = false;
    let res: Response;
    try {
      res = await fetch(`/api/runs/${this.runId}/events?after=${this.lastSeq}`);
    } catch {
      this.detached = true;   // still offline; the next 'online' or focus retries
      return;
    }
    if (res.status === 404) {
      // The hub no longer has it (restart, or long gone): the transcript does.
      this.finish();
      if (this.snap.sessionId) this.open(this.snap.sessionId);
      return;
    }
    await this.consume(res, 'live');
  }

  /** Follow a turn started before a reload: only its approvals and its end matter. */
  private async watch(runId: string) {
    let res: Response;
    try {
      res = await fetch(`/api/runs/${runId}/events`);
    } catch { return; }
    if (!res.ok) { store(RUN_KEY, null); return; }
    this.runId = runId;
    this.set({ running: true });
    await this.consume(res, 'watch');
  }

  private async consume(res: Response, mode: 'live' | 'watch') {
    let ended = false;
    try {
      for await (const ev of sseEvents(res)) {
        if (typeof ev.seq === 'number') this.lastSeq = ev.seq;
        if (mode === 'live') this.apply(ev);
        else this.applyWatched(ev);
        if (ev.type === 'done') { ended = true; break; }
      }
    } catch { /* the connection dropped: handled below */ }
    if (ended) return;
    if (this.runId) {
      // iOS closes connections of apps off screen; the turn goes on in the hub.
      this.detached = true;
      if (document.visibilityState === 'visible' && navigator.onLine) setTimeout(() => this.resume(), 1000);
    } else {
      this.finish();
      this.onError('Connessione persa prima dell\'inizio della risposta');
    }
  }

  private apply(ev: any) {
    switch (ev.type) {
      case 'session':
        if (this.onScreen() && !this.snap.sessionId) {
          this.set({ sessionId: ev.session_id, source: 'api_server' });
          store(SESSION_KEY, ev.session_id);
        }
        this.runSession = ev.session_id;
        break;
      case 'run':
        this.runId = ev.run_id;
        store(RUN_KEY, `${ev.run_id} ${this.snap.sessionId}`);
        break;
      case 'delta':
        if (!this.onScreen()) break;
        this.setItems(items => {
          const last = items[items.length - 1];
          if (last && last.role === 'assistant' && last.streaming) {
            return [...items.slice(0, -1), { ...last, text: last.text + ev.text }];
          }
          return [...items, { id: nextId++, role: 'assistant', text: ev.text, streaming: true }];
        });
        break;
      case 'sentence':
        this.opts.onSentence?.(ev.text);
        break;
      case 'tool': {
        if (!this.onScreen()) break;
        const items = toolUpdate(this.snap.items, ev);
        this.set({ items });
        // The tool reporting back settles the question it was waiting on
        // (answered, or timed out on Hermes' side).
        if (ev.state !== 'running' && !items.some(i => i.role === 'tool' && i.state === 'running')) {
          this.set({ approval: null });
        }
        break;
      }
      case 'approval':
      case 'approval_done':
      case 'error':
        this.applyWatched(ev);
        break;
      case 'done':
        if (this.onScreen()) this.setItems(items => {
          let out = items.map(i => (i.role === 'assistant' && i.streaming ? { ...i, streaming: false } : i));
          // Tools still marked running never reported back: the turn is over.
          out = out.map(i => (i.role === 'tool' && i.state === 'running' ? { ...i, state: 'error' as const } : i));
          const last = out[out.length - 1];
          if (ev.reply && !(last && last.role === 'assistant')) {
            out = [...out, { id: nextId++, role: 'assistant', text: ev.reply }];
          }
          if (ev.status === 'cancelled') out = [...out, { id: nextId++, role: 'note', text: 'Interrotto' }];
          return out;
        });
        this.finish();
        if (!this.snap.title && this.snap.sessionId) this.refreshTitle(this.snap.sessionId);
        break;
    }
  }

  private applyWatched(ev: any) {
    switch (ev.type) {
      case 'approval':
        this.set({
          approval: {
            runId: ev.run_id, requestId: ev.request_id ?? null, command: ev.command,
            description: ev.description, choices: ev.choices || ['once', 'deny'],
          },
        });
        break;
      case 'approval_done':
        if (this.snap.approval && (this.snap.approval.requestId ?? null) === (ev.request_id ?? null)) {
          this.set({ approval: null });
        }
        break;
      case 'tool':
        // Once the tool reports back, the question it waited on is settled.
        if (ev.state !== 'running') this.set({ approval: null });
        break;
      case 'error':
        this.onError('Hermes: ' + ev.message);
        break;
      case 'done':
        this.finish();
        if (this.snap.sessionId) this.open(this.snap.sessionId);
        break;
    }
  }

  private onScreen() {
    return this.runSession === this.snap.sessionId;
  }

  private finish() {
    this.runId = null;
    this.detached = false;
    this.opts = {};
    store(RUN_KEY, null);
    this.set({ running: false, approval: null });
  }

  // Hermes names a new conversation from its first exchange, a moment after it
  // ends — a few seconds, longer after a long reply.
  private refreshTitle(id: string, delays = [4000, 12000]) {
    const [delay, ...rest] = delays;
    if (delay === undefined) return;
    setTimeout(async () => {
      if (this.snap.sessionId !== id || this.snap.title) return;
      try {
        const data = await api(`/api/sessions/${encodeURIComponent(id)}`);
        if (this.snap.sessionId !== id) return;
        if (data.session.title) this.set({ title: data.session.title });
        else this.refreshTitle(id, rest);
      } catch { /* the title is a nicety */ }
    }, delay);
  }
}

export const chat = new ChatStore();
