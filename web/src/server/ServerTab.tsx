import { useEffect, useState } from 'preact/hooks';
import { api, postJSON } from '../lib/sse';
import { bytes, clock, duration, money } from '../lib/format';
import { currentSubscription, disablePush, enablePush, pushSupport } from '../lib/push';

interface Service {
  unit: string; label: string; state: string; sub: string; result: string;
  restarts: number; up_s: number | null; memory: number | null; restartable?: boolean;
}

interface Overview {
  services: Service[];
  others: Service[];
  resources: null | {
    memory: { total: number; available: number };
    disk: { total: number; used: number; free: number; percent: number };
    load: number[]; cpus: number; uptime_s: number;
  };
  hermes: null | { version: string; gateway: string; sessions: number; platforms: { name: string; state: string }[] };
  usage: null | { days: { day: string; cost: number; calls: number }[]; today: number; week: number; calls: number };
  alerts: { key: string; title: string; body: string; since: number }[];
  checked_at: number | null;
  devices: number;
  errors: Record<string, string>;
}

const REFRESH_MS = 15000;

function dotClass(state: string) {
  if (state === 'active') return 'ok';
  if (state === 'activating' || state === 'reloading' || state === 'deactivating') return 'warn';
  if (state === 'failed') return 'bad';
  return 'off';
}

const STATES: Record<string, string> = {
  active: 'attivo', inactive: 'fermo', failed: 'in errore', activating: 'in avvio',
  deactivating: 'in arresto', reloading: 'ricarica',
};

function ServiceRow({ s, onDone }: { s: Service; onDone: (msg: string, ok: boolean) => void }) {
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);

  const restart = async () => {
    setBusy(true);
    try {
      const r = await postJSON(`/api/server/services/${encodeURIComponent(s.unit)}/restart`, {});
      const data = await r.json().catch(() => ({}));
      onDone(r.ok ? `${s.label}: riavvio avviato` : `${s.label}: ${data.error || r.status}`, r.ok);
    } catch (e: any) {
      onDone(`${s.label}: ${e.message}`, false);
    }
    setBusy(false);
    setConfirm(false);
  };

  const detail = s.state === 'active'
    ? [s.up_s != null && `da ${duration(s.up_s)}`, s.memory != null && bytes(s.memory)].filter(Boolean).join(' · ')
    : [STATES[s.state] || s.state, s.result && s.result !== 'success' && s.result].filter(Boolean).join(' · ');

  return (
    <div class={`svc${confirm ? ' is-confirm' : ''}`}>
      <span class={`dot ${dotClass(s.state)}`} aria-label={STATES[s.state] || s.state} />
      <div class="svc-main">
        <span class="svc-name">{s.label}</span>
        <span class="svc-detail">
          {detail}{s.restarts > 0 && <em> · {s.restarts} riavvii automatici</em>}
        </span>
      </div>
      {s.restartable && !confirm && (
        <button class="pill" disabled={busy} onClick={() => setConfirm(true)}>Riavvia</button>
      )}
      {confirm && (
        <div class="confirm">
          <button class="pill" onClick={() => setConfirm(false)}>Annulla</button>
          <button class="pill pill-danger" disabled={busy} onClick={restart}>{busy ? '…' : 'Riavvia'}</button>
        </div>
      )}
    </div>
  );
}

function Meter({ label, used, total, note }: { label: string; used: number; total: number; note: string }) {
  const pct = Math.min(100, Math.round((used / total) * 100));
  return (
    <div class="meter">
      <div class="meter-top"><span>{label}</span><span>{note}</span></div>
      <div class="bar"><i class={pct >= 85 ? 'hot' : pct >= 70 ? 'warm' : ''} style={{ width: pct + '%' }} /></div>
    </div>
  );
}

function Costs({ usage }: { usage: NonNullable<Overview['usage']> }) {
  const max = Math.max(...usage.days.map(d => d.cost), 0.0001);
  return (
    <div class="card">
      <dl class="rows compact">
        <div><dt>Oggi</dt><dd>{money(usage.today)}</dd></div>
        <div><dt>Ultimi 7 giorni</dt><dd>{money(usage.week)} · {usage.calls} chiamate</dd></div>
      </dl>
      <div class="spark" aria-hidden="true">
        {usage.days.map(d => (
          <div key={d.day} class="spark-col" title={`${d.day}: ${money(d.cost)}`}>
            <i style={{ height: Math.max(2, (d.cost / max) * 100) + '%' }} />
            <span>{new Date(d.day + 'T12:00:00Z').toLocaleDateString('it-IT', { weekday: 'narrow' })}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Notifications({ devices, refresh }: { devices: number; refresh: () => void }) {
  const support = pushSupport();
  const [on, setOn] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');

  useEffect(() => { currentSubscription().then(s => setOn(!!s)).catch(() => setOn(false)); }, []);

  if (support === 'install') {
    return <p class="card note-text">Le notifiche su iPhone arrivano solo all'app aperta dalla schermata Home: in Safari, Condividi › Aggiungi alla schermata Home, poi apri l'app da lì.</p>;
  }
  if (support === 'unsupported') {
    return <p class="card note-text">Questo browser non supporta le notifiche push.</p>;
  }

  const toggle = async () => {
    setBusy(true);
    setMsg('');
    try {
      if (on) { await disablePush(); setOn(false); } else { await enablePush(); setOn(true); }
      refresh();
    } catch (e: any) {
      setMsg(e.message);
    }
    setBusy(false);
  };

  const test = async () => {
    setMsg('');
    const r = await postJSON('/api/push/test', {});
    const d = await r.json().catch(() => ({}));
    setMsg(r.ok ? (d.sent ? 'inviata: arriva tra pochi secondi' : 'nessun dispositivo l\'ha accettata') : d.error || 'errore');
  };

  return (
    <div class="card">
      <label class="switch plain">
        <span>
          Avvisi sul telefono
          <small>Servizio fermo da 2 minuti, disco oltre 85%, RAM sotto 300 MB, riavvii, cron falliti.</small>
        </span>
        <input type="checkbox" checked={!!on} disabled={busy || on === null} onChange={toggle} />
      </label>
      {on && <button class="pill" onClick={test}>Invia una notifica di prova</button>}
      <p class="small-note">{msg || `${devices} dispositivi iscritti`}</p>
    </div>
  );
}

export function ServerTab() {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<{ msg: string; ok: boolean } | null>(null);
  const [showOthers, setShowOthers] = useState(false);

  const load = async () => {
    try {
      setData(await api<Overview>('/api/server/overview'));
      setError(null);
    } catch (e: any) {
      setError(e.message);
    }
  };

  useEffect(() => {
    load();
    const t = setInterval(() => { if (document.visibilityState === 'visible') load(); }, REFRESH_MS);
    return () => clearInterval(t);
  }, []);

  const done = (msg: string, ok: boolean) => {
    setFlash({ msg, ok });
    setTimeout(() => setFlash(null), 5000);
    // Give systemd a moment, then show where the service landed.
    setTimeout(load, 2500);
  };

  if (!data) {
    return <section class="page server"><h2>Server</h2><p class="note-text">{error ? `Non riesco a leggere lo stato: ${error}` : 'carico…'}</p></section>;
  }

  const r = data.resources;
  return (
    <section class="page server">
      <div class="page-head">
        <h2>Server</h2>
        <button class="pill" onClick={load}>aggiorna</button>
      </div>

      {flash && <div class={`flash ${flash.ok ? 'ok' : 'bad'}`}>{flash.msg}</div>}
      {data.alerts.map(a => (
        <div class="alert" key={a.key}><strong>{a.title}</strong><span>{a.body} · da {clock(a.since)}</span></div>
      ))}

      <h3>Servizi</h3>
      <div class="card list">
        {data.services.map(s => <ServiceRow key={s.unit} s={s} onDone={done} />)}
        {data.errors.services && <p class="note-text">{data.errors.services}</p>}
      </div>
      {data.others.length > 0 && (
        <button class="link-btn" onClick={() => setShowOthers(!showOthers)}>
          {showOthers ? 'Nascondi' : 'Mostra'} gli altri {data.others.length} servizi in esecuzione
        </button>
      )}
      {showOthers && (
        <div class="card list">
          {data.others.map(s => <ServiceRow key={s.unit} s={s} onDone={done} />)}
        </div>
      )}

      {r && <>
        <h3>Risorse</h3>
        <div class="card">
          <Meter label="Memoria" used={r.memory.total - r.memory.available} total={r.memory.total}
                 note={`${bytes(r.memory.available)} liberi su ${bytes(r.memory.total)}`} />
          <Meter label="Disco" used={r.disk.used} total={r.disk.total}
                 note={`${bytes(r.disk.free)} liberi · ${r.disk.percent.toLocaleString('it-IT')}%`} />
          <dl class="rows compact">
            <div><dt>Carico</dt><dd>{r.load[0].toLocaleString('it-IT')} su {r.cpus} CPU</dd></div>
            <div><dt>Acceso da</dt><dd>{duration(r.uptime_s)}</dd></div>
          </dl>
        </div>
      </>}

      <h3>Hermes</h3>
      <div class="card">
        {data.hermes ? (
          <dl class="rows compact">
            <div><dt>Versione</dt><dd>{data.hermes.version}</dd></div>
            <div><dt>Gateway</dt><dd>{data.hermes.gateway === 'running' ? 'attivo' : data.hermes.gateway}</dd></div>
            <div><dt>Sessioni in corso</dt><dd>{data.hermes.sessions}</dd></div>
            {data.hermes.platforms.map(p => (
              <div key={p.name}><dt>{p.name}</dt><dd>{p.state}</dd></div>
            ))}
          </dl>
        ) : <p class="note-text">La dashboard di Hermes non risponde: {data.errors.hermes}</p>}
      </div>

      {data.usage && <><h3>Costi</h3><Costs usage={data.usage} /></>}

      <div class="nav-cards">
        <a class="card nav-card" href="#/server/log"><span>Log</span><small>Hermes e servizi, filtrabili</small></a>
        <a class="card nav-card" href="#/server/cron"><span>Cron</span><small>pausa, ripresa, avvio</small></a>
      </div>

      <h3>Notifiche</h3>
      <Notifications devices={data.devices} refresh={load} />

      <p class="small-note">controllato alle {clock(data.checked_at)} · aggiornato ogni {REFRESH_MS / 1000} s</p>
    </section>
  );
}
