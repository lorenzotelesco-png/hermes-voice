import { useEffect, useRef, useState } from 'preact/hooks';
import { api } from '../lib/sse';
import { when } from '../chat/labels';
import { ACCOUNT, SETUP, type Chat, type Status, cache, chatHref, needsConnect, net, post } from './inbox';
import { Avatar } from './Avatar';
import { Conversation } from './Conversation';

/** #/inbox: the chats, or what is still missing before they can be read; #/inbox/<chat>: one chat. */
export function InboxTab({ rest }: { rest: string }) {
  if (rest) {
    let id = rest;
    try { id = decodeURIComponent(rest); } catch { /* an id typed by hand */ }
    return <Conversation key={id} id={id} />;
  }
  return <InboxHome />;
}

function InboxHome() {
  const [st, setSt] = useState<Status | null>(cache.status);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try {
      const s = await api<Status>('/api/inbox/status');
      cache.status = s;
      setSt(s);
      setError(null);
    } catch (e: any) {
      setError(e.message);
    }
  };

  useEffect(() => { load(); }, []);

  // Waiting for the approval in Beeper's window: look again every few seconds.
  useEffect(() => {
    if (!st?.pending) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [st?.pending]);

  return (
    <section class="page inbox">
      {st?.connected ? <Chats st={st} reload={load} /> : (
        <>
          <h2>Inbox</h2>
          {st ? <Setup st={st} reload={load} />
            : <p class="note-text">{error ? `Non riesco a leggere lo stato di Beeper: ${error}` : 'carico…'}</p>}
        </>
      )}
    </section>
  );
}

function PcScreen() {
  return (
    <details class="howto">
      <summary>Aprire la schermata di Beeper dal PC</summary>
      <ol>
        <li>Accendila: <code>ssh LORE-SERVER sudo systemctl start beeper-screen</code> (si spegne da sola dopo 45 minuti).</li>
        <li>Collegati: <code>ssh -N -L 6080:127.0.0.1:6080 LORE-SERVER</code></li>
        <li>Nel browser del PC apri <code>http://localhost:6080/vnc.html</code></li>
      </ol>
      <p>Le chat vanno collegate "su questo dispositivo": così restano sul server e non nel cloud di Beeper.</p>
    </details>
  );
}

function Setup({ st, reload }: { st: Status; reload: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const again = async () => { setBusy(true); await reload(); setBusy(false); };
  const connect = async () => {
    setBusy(true);
    try { await post('/api/inbox/connect', {}); } catch { /* the status says why */ }
    await reload();
    setBusy(false);
  };

  if (st.beeper === 'down') {
    return (
      <div class="card">
        <p>Beeper non risponde sul server.</p>
        <p class="small-note">Si sta riavviando o è fermo: lo trovi nella tab Server, "Beeper (Inbox)".</p>
        <button class="pill" disabled={busy} onClick={again}>Riprova</button>
      </div>
    );
  }
  if (st.setup !== 'ready') {
    return (
      <div class="card">
        <p>{SETUP[st.setup || 'unknown'] || SETUP.unknown}</p>
        <p class="small-note">Si fa una volta sola, dal PC, sulla schermata di Beeper del server.</p>
        <PcScreen />
        <button class="pill" disabled={busy} onClick={again}>Controlla di nuovo</button>
      </div>
    );
  }
  return (
    <div class="card">
      <p>Beeper è pronto: resta da dare all'Hub il permesso di leggere le chat.</p>
      {st.pending ? (
        <p class="waiting">
          <span class="spin" />
          <span>Approva la richiesta nella finestra di Beeper sul server (dal PC). Come scadenza scegli la più lunga.</span>
        </p>
      ) : (
        <button class="pill pill-strong" disabled={busy} onClick={connect}>Collega l'Hub a Beeper</button>
      )}
      {st.error && !st.pending && <p class="small-note bad-note">{st.error}</p>}
      <PcScreen />
    </div>
  );
}

function previewLine(c: Chat) {
  const p = c.preview;
  if (!p) return '';
  if (p.mine) return `Tu: ${p.text}`;
  if (c.group && p.sender) return `${p.sender.split(' ')[0]}: ${p.text}`;
  return p.text;
}

// The search and the filter survive a visit to a chat and back.
let lastQuery = '';
let lastFilter = '';

function Chats({ st, reload }: { st: Status; reload: () => Promise<void> }) {
  const plain = !lastQuery && !lastFilter;
  const [chats, setChats] = useState<Chat[] | null>(plain ? cache.chats : null);
  const [older, setOlder] = useState<string | null>(plain ? cache.older : null);
  const current = useRef(chats);
  current.current = chats;
  const [q, setQ] = useState(lastQuery);
  const [filter, setFilter] = useState(lastFilter);   // '', 'unread' or an account id
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState(st.live);
  const seq = useRef(0);
  const view = useRef({ q, filter });
  view.current = { q, filter };

  const query = (cursor = '') => {
    const p = new URLSearchParams();
    const { q, filter } = view.current;
    if (q.trim()) p.set('q', q.trim());
    if (filter === 'unread') p.set('unread', 'true');
    else if (filter) p.set('account', filter);
    if (cursor) p.set('cursor', cursor);
    return p.toString();
  };

  /** The first page again; `keep` holds on to older pages already loaded (a live refresh). */
  const load = async (keep: boolean) => {
    const n = ++seq.current;
    setBusy(true);
    try {
      const d = await api<{ chats: Chat[]; older: string | null }>(`/api/inbox/chats?${query()}`);
      if (n !== seq.current) return;
      const prev = current.current;
      if (!keep || !prev || prev.length <= d.chats.length) {
        setChats(d.chats);
        setOlder(d.older);
      } else {
        const ids = new Set(d.chats.map(c => c.id));
        const oldest = Math.min(...d.chats.map(c => c.time ?? Infinity));
        setChats([...d.chats, ...prev.filter(c => !ids.has(c.id) && (c.time ?? 0) < oldest)]);
      }
      if (!view.current.q.trim() && !view.current.filter) { cache.chats = d.chats; cache.older = d.older; }
      setError(null);
    } catch (e: any) {
      if (n !== seq.current) return;
      if (needsConnect(e)) { reload(); return; }
      setError(e.message);
    }
    setBusy(false);
  };

  const more = async () => {
    if (!older) return;
    const n = ++seq.current;
    setBusy(true);
    try {
      const d = await api<{ chats: Chat[]; older: string | null }>(`/api/inbox/chats?${query(older)}`);
      if (n !== seq.current) return;
      setChats(prev => {
        const ids = new Set((prev || []).map(c => c.id));
        return [...(prev || []), ...d.chats.filter(c => !ids.has(c.id))];
      });
      setOlder(d.older);
    } catch (e: any) {
      if (n === seq.current) setError(e.message);
    }
    if (n === seq.current) setBusy(false);
  };

  useEffect(() => {
    lastQuery = q;
    lastFilter = filter;
    const t = setTimeout(() => load(false), q.trim() ? 300 : 0);
    return () => clearTimeout(t);
  }, [q, filter]);

  // Live: a new message or a changed chat refreshes the list (a burst once).
  // EventSource reconnects by itself when iOS drops it, and each reopen reads
  // the list again, so nothing that arrived meanwhile is missed.
  useEffect(() => {
    let t: number | undefined;
    const soon = () => { clearTimeout(t); t = window.setTimeout(() => load(true), 700); };
    const es = new EventSource('/api/inbox/events');
    let opened = false;
    es.onopen = () => { if (opened) soon(); opened = true; };
    es.onmessage = ev => {
      let e: any;
      try { e = JSON.parse(ev.data); } catch { return; }
      if (e.t === 'hello') setLive(e.live);
      else if (e.t === 'live') { setLive(e.on); if (e.on) soon(); }
      else if (e.t === 'state') reload();
      else soon();
    };
    return () => { es.close(); clearTimeout(t); };
  }, []);

  const trouble = st.accounts.filter(a => ACCOUNT[a.status]);
  const nets = st.accounts.filter(a => a.network);
  const expiring = st.expires && st.expires - Date.now() / 1000 < 5 * 86400;

  return (
    <>
      <div class="page-head">
        <h2>Inbox</h2>
        <button class={`sync-chip live-chip${live ? ' is-live' : ''}`} disabled={busy} onClick={() => load(false)}
                title={live ? 'Aggiornata in diretta' : 'Non in diretta: tocca per aggiornare'}>
          {busy ? <span class="spin" /> : <i />}{live ? 'in diretta' : 'aggiorna'}
        </button>
      </div>
      {trouble.map(a => (
        <div key={a.id} class="warn-flash">
          {a.network}{a.name ? ` (${a.name})` : ''}: {ACCOUNT[a.status]}{a.status_text ? ` · ${a.status_text}` : ''}
        </div>
      ))}
      {expiring && (
        <div class="warn-flash">
          L'accesso dell'Hub a Beeper scade il {new Date(st.expires! * 1000).toLocaleDateString('it-IT')}:
          scollegalo e ricollegalo qui sotto, approvando dal PC.
        </div>
      )}

      <div class="search inbox-search">
        <input type="search" placeholder="Cerca nelle chat" value={q} enterkeyhint="search"
               onInput={e => setQ((e.target as HTMLInputElement).value)} />
      </div>
      <div class="chips">
        <button class={filter === '' ? 'is-on' : ''} onClick={() => setFilter('')}>Tutte</button>
        <button class={filter === 'unread' ? 'is-on' : ''} onClick={() => setFilter('unread')}>Non lette</button>
        {nets.map(a => (
          <button key={a.id} class={filter === a.id ? 'is-on' : ''} onClick={() => setFilter(filter === a.id ? '' : a.id)}>
            <i style={`background:${net(a.network).color}`} />{a.network}
          </button>
        ))}
      </div>

      {error && <p class="small-note bad-note">Non riesco a caricare: {error}</p>}
      <div class="card list chat-list">
        {chats === null && <p class="list-note">carico…</p>}
        {chats && !chats.length && !busy && (
          <p class="list-note">{q.trim() ? `Nessuna chat per "${q.trim()}".` : filter === 'unread' ? 'Nessuna chat da leggere.' : 'Nessuna chat.'}</p>
        )}
        {chats?.map(c => {
          const unread = c.unread > 0 || c.marked_unread;
          return (
            <a key={c.id} class={`crow${unread ? ' is-unread' : ''}${c.muted ? ' is-muted' : ''}`} href={chatHref(c.id)}>
              <Avatar chat={c} />
              <div class="crow-main">
                <div class="crow-top">
                  <span class="crow-title">{c.title}</span>
                  <time>{when(c.time)}</time>
                </div>
                <div class="crow-bottom">
                  <span class="crow-preview">{previewLine(c)}</span>
                  {c.muted && <svg class="ico muted" viewBox="0 0 24 24" aria-label="silenziata"><path d="M11 5L6 9H3v6h3l5 4zM16 9l5 6M21 9l-5 6" /></svg>}
                  {unread && <span class={`count${c.mentions ? ' mention' : ''}`}>{c.mentions ? '@' : c.unread || ''}</span>}
                </div>
              </div>
            </a>
          );
        })}
      </div>
      {older && chats && (
        <button class="load-more" disabled={busy} onClick={more}>{busy ? 'carico…' : 'Chat meno recenti'}</button>
      )}

      <Settings st={st} reload={reload} />
    </>
  );
}

function Settings({ st, reload }: { st: Status; reload: () => Promise<void> }) {
  const [notify, setNotify] = useState(st.notify);
  const [busy, setBusy] = useState(false);

  const toggle = async (on: boolean) => {
    setNotify(on);
    try { await post('/api/inbox/notify', { on }); } catch { setNotify(!on); }
  };

  const disconnect = async () => {
    if (!confirm('Scollegare l\'Hub da Beeper? Per ricollegarlo servirà di nuovo la finestra di Beeper dal PC.')) return;
    setBusy(true);
    try { await post('/api/inbox/connect', {}, 'DELETE'); } catch { /* the status shows what is left */ }
    cache.chats = null;
    await reload();
    setBusy(false);
  };

  return (
    <>
      <h3>Impostazioni</h3>
      <label class="switch">
        <span>
          Notifiche dei messaggi
          <small>Una per chat alla volta, mai per le chat silenziate o già aperte. Il telefono deve avere le notifiche attive in Server › Notifiche.</small>
        </span>
        <input type="checkbox" checked={notify} onChange={e => toggle((e.target as HTMLInputElement).checked)} />
      </label>
      <p class="small-note">
        {st.expires ? `Accesso a Beeper valido fino al ${new Date(st.expires * 1000).toLocaleDateString('it-IT')}. ` : ''}
        <button class="inline-link" disabled={busy} onClick={disconnect}>Scollega l'Hub</button>
      </p>
    </>
  );
}
