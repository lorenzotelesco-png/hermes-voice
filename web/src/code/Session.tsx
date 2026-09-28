import { useEffect, useLayoutEffect, useRef, useState } from 'preact/hooks';
import { renderMarkdown } from '../chat/markdown';
import { Locked, code, codePost } from './lock';

interface Part {
  id: string; type: string; text?: string; tool?: string; status?: string; title?: string; input?: string;
  output?: string; cut?: boolean; error?: string; files?: string[]; name?: string;
}
interface Message { id: string; role: 'user' | 'assistant'; parts: Part[]; error: string | null; model?: string }
interface Permission { id: string; permission: string; patterns: string[]; title: string; command: string }
interface Detail {
  session: { id: string; title: string; dir: string };
  messages: Message[]; status: string; permissions: Permission[];
}

const TOOL_STATE: Record<string, string> = { pending: '…', running: '…', completed: '✓', error: '✗' };

function ToolCard({ p }: { p: Part }) {
  const [open, setOpen] = useState(false);
  return (
    <div class={`oc-tool ${p.status}`}>
      <button class="oc-tool-head" onClick={() => setOpen(!open)}>
        <span class="oc-tool-name">{p.tool}</span>
        <code>{p.input || p.title}</code>
        <span class="oc-tool-state">{TOOL_STATE[p.status || ''] || ''}</span>
      </button>
      {open && (p.output || p.error) && (
        <pre class="oc-tool-out">{p.cut ? '…\n' : ''}{p.error || p.output}</pre>
      )}
    </div>
  );
}

function PartView({ p }: { p: Part }) {
  if (p.type === 'text') return <div class="md oc-text" dangerouslySetInnerHTML={{ __html: renderMarkdown(p.text || '') }} />;
  if (p.type === 'reasoning') {
    return <details class="oc-reason"><summary>ragionamento</summary><p>{p.text}</p></details>;
  }
  if (p.type === 'tool') return <ToolCard p={p} />;
  if (p.type === 'patch') return <p class="oc-patch">modificati: {p.files?.join(', ')}</p>;
  if (p.type === 'file') return <p class="oc-patch">allegato: {p.name}</p>;
  return null;
}

export function Session({ id, dir, onLocked }: { id: string; dir: string; onLocked: () => void }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const q = `dir=${encodeURIComponent(dir)}`;

  const fail = (e: any) => { if (e instanceof Locked) onLocked(); else setError(e.message); };
  const load = () => code<Detail>(`/api/code/sessions/${id}?${q}`).then(setDetail).catch(fail);

  // Live: OpenCode's own events for this session. EventSource reconnects by
  // itself when iOS drops it, and each (re)open re-reads the whole session,
  // so nothing said while the app was away is missed.
  useEffect(() => {
    const es = new EventSource(`/api/code/sessions/${id}/events`);
    es.onopen = () => load();
    es.onmessage = ev => {
      let e: any;
      try { e = JSON.parse(ev.data); } catch { return; }
      if (e.t === 'status') setDetail(d => d && { ...d, status: e.status });
      else if (e.t === 'permissions') load();
      else if (e.t === 'error') setError(e.message);
      else if (e.t === 'session') setDetail(d => d && { ...d, session: { ...d.session, title: e.session.title || d.session.title } });
      else if (e.t === 'message') setDetail(d => d && upsertMessage(d, e.message));
      else if (e.t === 'part') setDetail(d => d && upsertPart(d, e.mid, e.part));
      else if (e.t === 'delta' && e.field === 'text') setDetail(d => d && appendDelta(d, e.mid, e.pid, e.delta));
    };
    return () => es.close();
  }, [id]);

  useLayoutEffect(() => {
    const el = box.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [detail]);

  const busy = detail?.status === 'busy' || detail?.status === 'retry';

  const send = async () => {
    const t = text.trim();
    if (!t || sending) return;
    setSending(true);
    try {
      await codePost(`/api/code/sessions/${id}/prompt`, { dir, text: t });
      setText('');
      pinned.current = true;
      setDetail(d => d && { ...d, status: 'busy' });
    } catch (e: any) { fail(e); }
    setSending(false);
  };

  const stop = () => codePost(`/api/code/sessions/${id}/abort`, { dir }).catch(fail);
  const answer = (p: Permission, response: 'once' | 'reject') =>
    codePost(`/api/code/sessions/${id}/permissions/${p.id}`, { dir, response }).then(load).catch(fail);

  const perm = detail?.permissions[0];
  return (
    <section class="sub-page oc">
      <header class="chat-head">
        <a class="head-btn" href="#/codice" title="Indietro">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7" /></svg>
        </a>
        <div class="chat-title">
          <span>{detail?.session.title || 'OpenCode'}</span>
          <small>{dir}{busy ? ' · sta lavorando' : ''}</small>
        </div>
        {busy
          ? <button class="head-btn" title="Ferma" onClick={stop}><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="2" /></svg></button>
          : <span class="head-btn" />}
      </header>
      <div class="list padded oc-thread" ref={box}
           onScroll={() => { const el = box.current!; pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; }}>
        {error && <div class="flash bad" onClick={() => setError(null)}>{error}</div>}
        {!detail && !error && <p class="list-note">carico…</p>}
        {detail && !detail.messages.length && <p class="list-note">Scrivi cosa deve fare OpenCode in {dir}.</p>}
        {detail?.messages.map(m => m.role === 'user' ? (
          <div key={m.id} class="oc-user">{m.parts.map(p => p.text).join('\n')}</div>
        ) : (
          <div key={m.id} class="oc-assistant">
            {m.parts.map(p => <PartView key={p.id} p={p} />)}
            {m.error && <p class="oc-error">{m.error}</p>}
          </div>
        ))}
        {busy && <p class="oc-busy"><span class="spin" /> OpenCode sta lavorando</p>}
      </div>
      <div class="composer oc-composer">
        <textarea rows={1} placeholder="Chiedi a OpenCode…" value={text} disabled={sending}
                  onInput={e => { const t = e.target as HTMLTextAreaElement; setText(t.value); t.style.height = 'auto'; t.style.height = Math.min(t.scrollHeight, 160) + 'px'; }} />
        <button class="round-btn solid" disabled={!text.trim() || sending} onClick={send} title="Invia">
          <svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h13M13 6l6 6-6 6" /></svg>
        </button>
      </div>

      {perm && (
        <div class="sheet-backdrop">
          <div class="sheet" role="dialog" aria-label="Permesso">
            <h3>OpenCode chiede: {perm.permission}</h3>
            <pre class="cmd">{perm.command || perm.patterns.join('\n') || perm.title}</pre>
            <div class="sheet-actions">
              <button class="btn btn-deny" onClick={() => answer(perm, 'reject')}>Rifiuta</button>
              <button class="btn btn-ok" onClick={() => answer(perm, 'once')}>Consenti una volta</button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

function upsertMessage(d: Detail, m: Message): Detail {
  const i = d.messages.findIndex(x => x.id === m.id);
  if (i < 0) return { ...d, messages: [...d.messages, m] };
  const messages = d.messages.slice();
  messages[i] = { ...messages[i], role: m.role, error: m.error, model: m.model };
  return { ...d, messages };
}

function withMessage(d: Detail, mid: string, fn: (m: Message) => Message): Detail {
  const i = d.messages.findIndex(x => x.id === mid);
  const messages = d.messages.slice();
  if (i < 0) messages.push(fn({ id: mid, role: 'assistant', parts: [], error: null }));
  else messages[i] = fn(messages[i]);
  return { ...d, messages };
}

function upsertPart(d: Detail, mid: string, part: Part): Detail {
  return withMessage(d, mid, m => {
    const i = m.parts.findIndex(p => p.id === part.id);
    const parts = m.parts.slice();
    if (i < 0) parts.push(part); else parts[i] = part;
    return { ...m, parts };
  });
}

function appendDelta(d: Detail, mid: string, pid: string, delta: string): Detail {
  return withMessage(d, mid, m => {
    const i = m.parts.findIndex(p => p.id === pid);
    const parts = m.parts.slice();
    if (i < 0) parts.push({ id: pid, type: 'text', text: delta });
    else parts[i] = { ...parts[i], text: (parts[i].text || '') + delta };
    return { ...m, parts };
  });
}
