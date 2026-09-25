import { useEffect, useRef, useState } from 'preact/hooks';
import { api, postJSON } from '../lib/sse';
import { clock } from '../lib/format';
import { Dictation } from '../lib/dictate';
import { voice } from '../voice/engine';
import { Icon, fileIcon } from './icons';
import { remember, takeFlash } from './nav';
import { Folder } from './Folder';
import { NoteView, Editor } from './Note';
import { Hermes, ROOTS } from './Hermes';
import {
  type VaultIndex, type VaultStatus, baseName, cachedIndex, dirName, folderHref, isNote, loadIndex,
  noteHref, pushNote, sync, title, today,
} from './vault';

/** #/file/<rest>: the vault home, a folder, a note, the editor, or Hermes' folders. */
export function FilesTab({ rest }: { rest: string }) {
  useEffect(() => { remember(location.hash || '#/file'); }, [rest]);
  const [kind = '', a = '', b = ''] = rest.split('/');
  const arg = (s: string) => { try { return decodeURIComponent(s); } catch { return s; } };
  if (kind === 'cartella') return <Folder key={a} dir={arg(a)} />;
  if (kind === 'nota') return <NoteView key={a} path={arg(a)} />;
  if (kind === 'modifica') return <Editor key={a} path={arg(a)} />;
  if (kind in ROOTS) return <Hermes key={rest} root={kind} kind={a === 'file' ? 'file' : 'dir'} path={arg(b)} />;
  return <VaultHome />;
}

function syncLabel(s: VaultStatus | null, busy: boolean, error: string | null) {
  if (busy) return 'sincronizzo…';
  if (error) return 'vault non raggiungibile';
  if (!s) return '';
  if (s.online === false) return 'GitHub non raggiungibile';
  if (s.ahead) return `${s.ahead} ${s.ahead === 1 ? 'modifica' : 'modifiche'} da inviare`;
  return `sincronizzato ${s.fetched_at ? clock(s.fetched_at) : ''}`;
}

// The search box keeps its text when you come back from a result.
let lastQuery = '';

interface Hit { path: string; in_name: boolean; count: number; hits: { line: number; text: string }[] }

function VaultHome() {
  const [idx, setIdx] = useState<VaultIndex | null>(cachedIndex());
  const [status, setStatus] = useState<VaultStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(takeFlash());
  const [q, setQ] = useState(lastQuery);
  const [results, setResults] = useState<Hit[] | null>(null);
  const [searching, setSearching] = useState(false);

  const refresh = async (force: boolean) => {
    setBusy(true);
    try {
      const s = await sync(force);
      if (s) {
        setStatus(s);
        const msg = pushNote(s);
        if (msg && s.copies?.length) setFlash(msg);
      } else if (!status) {
        setStatus(await api<VaultStatus>('/api/vault/status'));
      }
      setIdx(await loadIndex());
      setError(null);
    } catch (e: any) {
      setError(e.message);
    }
    setBusy(false);
  };

  useEffect(() => { refresh(false); }, []);

  useEffect(() => {
    lastQuery = q;
    const query = q.trim();
    if (query.length < 2) { setResults(null); return; }
    setSearching(true);
    const t = setTimeout(async () => {
      try {
        setResults((await api<{ results: Hit[] }>(`/api/vault/search?q=${encodeURIComponent(query)}`)).results);
      } catch (e: any) {
        setError(e.message);
      }
      setSearching(false);
    }, 300);
    return () => clearTimeout(t);
  }, [q]);

  const notes = idx ? idx.files.filter(f => isNote(f.path) && !f.path.startsWith('Templates/')) : [];
  const recent = [...notes].sort((x, y) => y.changed - x.changed).slice(0, 6);
  const top = idx ? idx.dirs.filter(d => !d.includes('/')) : [];
  const count = (dir: string) => idx!.files.filter(f => f.path.startsWith(dir + '/')).length;

  return (
    <section class="page files">
      <div class="page-head">
        <h2>File</h2>
        <button class={`sync-chip${error ? ' bad' : status?.ahead || status?.online === false ? ' warn' : ''}`}
                disabled={busy} onClick={() => refresh(true)} title="Sincronizza con GitHub">
          <Icon name="sync" /> {syncLabel(status, busy, error)}
        </button>
      </div>
      {flash && <div class="flash ok" onClick={() => setFlash(null)}>{flash}</div>}

      <Capture onDone={() => loadIndex(true).then(setIdx).catch(() => {})} />

      <div class="search files-search">
        <input type="search" placeholder="Cerca nel vault" value={q} enterkeyhint="search"
               onInput={e => setQ((e.target as HTMLInputElement).value)} />
      </div>

      {results ? (
        <div class="card list">
          {searching && <p class="list-note">cerco…</p>}
          {!searching && !results.length && <p class="list-note">Nessuna nota contiene "{q.trim()}".</p>}
          {results.map(r => (
            <a key={r.path} class="frow" href={noteHref(r.path)}>
              <Icon name={fileIcon(r.path)} />
              <div class="frow-main">
                <span class="frow-title">{title(r.path)}</span>
                <span class="frow-sub">{dirName(r.path) || 'vault'}{r.count ? ` · ${r.count} ${r.count === 1 ? 'riga' : 'righe'}` : ''}</span>
                {r.hits.slice(0, 2).map(h => <span key={h.line} class="frow-hit">{h.text}</span>)}
              </div>
            </a>
          ))}
        </div>
      ) : idx ? (
        <>
          <h3>Recenti</h3>
          <div class="card list">
            {recent.map(f => (
              <a key={f.path} class="frow" href={noteHref(f.path)}>
                <Icon name="note" />
                <div class="frow-main">
                  <span class="frow-title">{title(f.path)}</span>
                  <span class="frow-sub">{dirName(f.path) || 'vault'} · {clock(f.changed)}</span>
                </div>
              </a>
            ))}
          </div>

          <h3>Cartelle</h3>
          <div class="card list">
            <a class="frow" href={folderHref('')}>
              <Icon name="folder" />
              <div class="frow-main"><span class="frow-title">Tutto il vault</span>
                <span class="frow-sub">{idx.files.length} file</span></div>
            </a>
            {top.map(d => (
              <a key={d} class="frow" href={folderHref(d)}>
                <Icon name="folder" />
                <div class="frow-main"><span class="frow-title">{baseName(d)}</span>
                  <span class="frow-sub">{count(d)} file</span></div>
              </a>
            ))}
          </div>

          <h3>Hermes, in sola lettura</h3>
          <div class="nav-cards">
            {Object.entries(ROOTS).map(([id, r]) => (
              <a key={id} class="card nav-card" href={`#/file/${id}`}><span>{r.label}</span><small>{r.note}</small></a>
            ))}
          </div>
        </>
      ) : (
        <p class="note-text">{error ? `Non riesco a leggere il vault: ${error}` : 'carico…'}</p>
      )}
    </section>
  );
}

function Capture({ onDone }: { onDone: () => void }) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [rec, setRec] = useState<'idle' | 'rec' | 'stt'>('idle');
  const [msg, setMsg] = useState<{ ok: boolean; text: string; path?: string } | null>(null);
  const dictation = useRef(new Dictation());

  useEffect(() => () => dictation.current.cancel(), []);

  const send = async () => {
    const line = text.trim();
    if (!line || busy) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await postJSON('/api/vault/capture', { text: line, ...today() });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
      setText('');
      setMsg({ ok: true, text: pushNote(d) || `Aggiunta a ${title(d.path)}`, path: d.path });
      onDone();
    } catch (e: any) {
      setMsg({ ok: false, text: e.message });
    }
    setBusy(false);
  };

  const mic = async () => {
    const d = dictation.current;
    try {
      if (rec === 'idle') {
        await d.start();
        setRec('rec');
      } else if (rec === 'rec') {
        setRec('stt');
        const said = await d.stop();
        if (said) setText(t => (t.trim() ? t.trim() + ' ' : '') + said);
        setRec('idle');
      }
    } catch (e: any) {
      d.cancel();
      setRec('idle');
      setMsg({ ok: false, text: e.name === 'NotAllowedError' ? 'Microfono non consentito' : e.message });
    }
  };

  return (
    <div class="card capture">
      <div class="capture-top">
        <span>Nota di oggi</span>
        <a href={noteHref(`Daily/${today().date}.md`)}>apri</a>
      </div>
      <div class="capture-row">
        <textarea rows={1} placeholder={rec === 'rec' ? 'ti ascolto… tocca ■ per finire' : 'Un\'idea, una cosa da fare'}
                  value={text} disabled={busy}
                  onInput={e => { const t = e.target as HTMLTextAreaElement; setText(t.value); t.style.height = 'auto'; t.style.height = t.scrollHeight + 'px'; }}
                  onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }} />
        <button class={`round-btn${rec === 'rec' ? ' is-rec' : ''}`} disabled={rec === 'stt' || voice.active}
                onClick={mic} title={rec === 'rec' ? 'Fine' : 'Detta'}>
          {rec === 'stt' ? <span class="spin" /> : <Icon name={rec === 'rec' ? 'stop' : 'mic'} />}
        </button>
        <button class="round-btn solid" disabled={!text.trim() || busy} onClick={send} title="Aggiungi">
          {busy ? <span class="spin" /> : <Icon name="send" />}
        </button>
      </div>
      {msg && (
        <p class={`small-note${msg.ok ? '' : ' bad-note'}`}>
          {msg.text}{msg.path && <> · <a href={noteHref(msg.path)}>apri</a></>}
        </p>
      )}
    </div>
  );
}
