import { useEffect, useState } from 'preact/hooks';
import { clock } from '../lib/format';
import { Locked, type LockStatus, code, codePost, lockNow, lockStatus, prepare, registerPasskey, stale, unlock } from './lock';
import { Session } from './Session';

export interface Place { dir: string; sessions: number; updated: number }
interface SessionRow { id: string; title: string; dir: string; updated: number; changes: { additions: number; deletions: number; files: number } }
interface Pty { id: string; title: string; command: string; dir: string; running: boolean }

export const sessionHref = (id: string, dir: string) => `#/codice/sessione/${id}/${encodeURIComponent(dir)}`;
export const terminalHref = (id: string) => `#/codice/terminale/${id}`;
const base = (d: string) => d.split('/').filter(Boolean).pop() || d;

/** #/codice/<rest>: Face ID first, then OpenCode's sessions and terminals. */
export function CodeTab({ rest }: { rest: string }) {
  const [status, setStatus] = useState<LockStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = () => lockStatus().then(s => { setStatus(s); setError(null); }).catch(e => setError(e.message));
  useEffect(() => { load(); }, []);

  const open = status?.unlocked_until && status.unlocked_until * 1000 > Date.now();
  const locked = () => setStatus(s => s && { ...s, unlocked_until: null });

  if (!status) return <section class="page code"><h2>Codice</h2><p class="note-text">{error || 'carico…'}</p></section>;
  if (!open) return <LockScreen status={status} onUnlocked={load} />;

  const [kind, a = '', b = ''] = rest.split('/');
  if (kind === 'sessione' && a) return <Session key={a} id={a} dir={decodeURIComponent(b)} onLocked={locked} />;
  if (kind === 'terminale' && a) return <LazyTerminal key={a} id={a} onLocked={locked} />;
  return <Home until={status.unlocked_until!} onLocked={locked} onLock={async () => { await lockNow(); locked(); }} />;
}

// xterm.js is most of this tab's weight: fetched the first time a terminal opens.
let TerminalView: any = null;

function LazyTerminal(props: { id: string; onLocked: () => void }) {
  const [View, setView] = useState<any>(() => TerminalView);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!View) import('./Terminal').then(m => { TerminalView = m.Terminal; setView(() => m.Terminal); }).catch(e => setError(e.message));
  }, []);
  if (View) return <View {...props} />;
  return <section class="page code"><h2>Terminale</h2><p class="note-text">{error || 'carico…'}</p></section>;
}

function LockScreen({ status, onUnlocked }: { status: LockStatus; onUnlocked: () => void }) {
  const [opts, setOpts] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');

  const fetchOpts = () => prepare(status.registered).then(setOpts).catch(e => setMsg(e.message));
  useEffect(() => { fetchOpts(); }, [status.registered]);

  const go = async () => {
    // No await before the passkey call: Safari wants it straight from the tap.
    if (stale(opts)) { fetchOpts(); setMsg('riprova adesso'); return; }
    setBusy(true);
    setMsg('');
    try {
      if (status.registered) await unlock(opts);
      else await registerPasskey(opts, /iPhone/.test(navigator.userAgent) ? 'iPhone' : 'dispositivo');
      onUnlocked();
    } catch (e: any) {
      setMsg(e.name === 'NotAllowedError' ? 'Annullato o non consentito.' : e.message);
      fetchOpts();
    }
    setBusy(false);
  };

  return (
    <section class="page code lock">
      <h2>Codice</h2>
      <p class="note-text">
        Terminale del server e OpenCode. Qui si lavora da root, quindi oltre al login dell'app serve Face ID;
        lo sblocco dura 30 minuti.
      </p>
      <button class="btn btn-ok lock-btn" disabled={busy || !opts} onClick={go}>
        {status.registered ? 'Sblocca con Face ID' : 'Registra Face ID'}
      </button>
      {!status.registered && (
        <p class="small-note">
          Solo la prima registrazione non chiede uno sblocco: aggiungere un altro dispositivo dopo richiederà
          Face ID da questo.
        </p>
      )}
      {msg && <p class="small-note bad-note">{msg}</p>}
    </section>
  );
}

function FolderPicker({ places, title, onPick, onClose }:
  { places: Place[]; title: string; onPick: (dir: string) => void; onClose: () => void }) {
  const [custom, setCustom] = useState('');
  const dirs = Array.from(new Set(['/root', ...places.map(p => p.dir)]));
  return (
    <div class="sheet-backdrop" onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div class="sheet" role="dialog" aria-label={title}>
        <h3>{title}</h3>
        <div class="card list">
          {dirs.map(d => (
            <button key={d} class="frow" onClick={() => onPick(d)}>
              <div class="frow-main"><span class="frow-title">{base(d)}</span><span class="frow-sub">{d}</span></div>
            </button>
          ))}
        </div>
        <div class="custom-dir">
          <input placeholder="/percorso/assoluto" value={custom} autocapitalize="off" autocorrect="off" spellcheck={false}
                 onInput={e => setCustom((e.target as HTMLInputElement).value)} />
          <button class="pill" disabled={!custom.startsWith('/')} onClick={() => onPick(custom.trim())}>Usa</button>
        </div>
      </div>
    </div>
  );
}

function Home({ until, onLocked, onLock }: { until: number; onLocked: () => void; onLock: () => void }) {
  const [ptys, setPtys] = useState<Pty[] | null>(null);
  const [sessions, setSessions] = useState<SessionRow[] | null>(null);
  const [places, setPlaces] = useState<Place[]>([]);
  const [pick, setPick] = useState<null | 'shell' | 'opencode' | 'session'>(null);
  const [error, setError] = useState<string | null>(null);

  const fail = (e: any) => { if (e instanceof Locked) onLocked(); else setError(e.message); };
  const load = () => {
    code<{ terminals: Pty[] }>('/api/code/pty').then(d => setPtys(d.terminals)).catch(fail);
    code<{ sessions: SessionRow[] }>('/api/code/sessions').then(d => setSessions(d.sessions)).catch(fail);
    code<{ places: Place[] }>('/api/code/places').then(d => setPlaces(d.places)).catch(fail);
  };
  useEffect(() => { load(); }, []);

  const start = async (dir: string) => {
    const what = pick;
    setPick(null);
    try {
      if (what === 'session') {
        const s = await codePost<SessionRow>('/api/code/sessions', { dir });
        location.hash = sessionHref(s.id, s.dir);
      } else {
        const t = await codePost<Pty>('/api/code/pty', { kind: what, dir });
        location.hash = terminalHref(t.id);
      }
    } catch (e: any) { fail(e); }
  };

  const close = async (t: Pty) => {
    if (!confirm(`Chiudere "${t.title}"? Quello che gira dentro viene fermato.`)) return;
    try { await codePost(`/api/code/pty/${t.id}`, {}, 'DELETE'); load(); } catch (e: any) { fail(e); }
  };

  return (
    <section class="page code">
      <div class="page-head">
        <h2>Codice</h2>
        <button class="pill" onClick={onLock} title={`Sbloccata fino alle ${clock(until)}`}>Blocca</button>
      </div>
      {error && <div class="flash bad" onClick={() => setError(null)}>{error}</div>}

      <h3>Terminali</h3>
      <div class="card list">
        {ptys?.map(t => (
          <div key={t.id} class="frow">
            <a class="frow-main" href={terminalHref(t.id)}>
              <span class="frow-title">{t.title}</span>
              <span class="frow-sub">{t.dir}{t.running ? '' : ' · terminato'}</span>
            </a>
            <button class="pill" onClick={() => close(t)}>Chiudi</button>
          </div>
        ))}
        {ptys && !ptys.length && <p class="list-note">Nessun terminale aperto.</p>}
      </div>
      <div class="folder-actions">
        <button class="pill" onClick={() => setPick('shell')}>Nuovo terminale</button>
        <button class="pill" onClick={() => setPick('opencode')}>OpenCode interattivo</button>
      </div>

      <h3>OpenCode</h3>
      <div class="card list">
        {sessions?.slice(0, 30).map(s => (
          <a key={s.id} class="frow" href={sessionHref(s.id, s.dir)}>
            <div class="frow-main">
              <span class="frow-title">{s.title || 'senza titolo'}</span>
              <span class="frow-sub">
                {base(s.dir)} · {clock(s.updated)}
                {s.changes.files ? ` · ${s.changes.files} file, +${s.changes.additions} −${s.changes.deletions}` : ''}
              </span>
            </div>
          </a>
        ))}
        {sessions && !sessions.length && <p class="list-note">Nessuna sessione.</p>}
      </div>
      <div class="folder-actions">
        <button class="pill" onClick={() => setPick('session')}>Nuova sessione</button>
      </div>
      <p class="small-note">Sbloccata fino alle {clock(until)}.</p>

      {pick && (
        <FolderPicker places={places} onClose={() => setPick(null)} onPick={start}
                      title={pick === 'session' ? 'Nuova sessione OpenCode in…' : pick === 'shell' ? 'Terminale in…' : 'OpenCode interattivo in…'} />
      )}
    </section>
  );
}
