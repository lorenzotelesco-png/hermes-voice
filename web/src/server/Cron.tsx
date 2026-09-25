import { useEffect, useState } from 'preact/hooks';
import { api, postJSON } from '../lib/sse';
import { clock } from '../lib/format';

interface Job {
  id: string; name: string; schedule: string; state: string; enabled: boolean;
  next_run_at: string | null; last_run_at: string | null; last_status: string | null; last_error: string;
}

const STATES: Record<string, string> = {
  scheduled: 'attivo', paused: 'in pausa', running: 'in esecuzione', completed: 'completato', error: 'in errore',
};

function JobCard({ job, reload }: { job: Job; reload: () => void }) {
  const [confirm, setConfirm] = useState(false);
  const [msg, setMsg] = useState('');
  const paused = job.state === 'paused' || !job.enabled;

  const act = async (action: 'pause' | 'resume' | 'trigger') => {
    setMsg('');
    const r = await postJSON(`/api/server/cron/${encodeURIComponent(job.id)}/${action}`, {});
    const d = await r.json().catch(() => ({}));
    setConfirm(false);
    setMsg(r.ok ? (action === 'trigger' ? 'avviato: il risultato arriva dove lo manda di solito' : '') : d.error || 'errore');
    reload();
  };

  return (
    <div class="card job">
      <div class="job-top">
        <span class={`dot ${job.last_status === 'error' ? 'bad' : paused ? 'off' : 'ok'}`} />
        <strong>{job.name}</strong>
        <span class="badge small">{STATES[job.state] || job.state}</span>
      </div>
      <p class="job-meta">{job.schedule}</p>
      <dl class="rows compact">
        {!paused && <div><dt>Prossima</dt><dd>{clock(job.next_run_at)}</dd></div>}
        <div><dt>Ultima</dt><dd>{clock(job.last_run_at)}{job.last_status ? ` · ${job.last_status === 'error' ? 'fallita' : 'ok'}` : ''}</dd></div>
      </dl>
      {job.last_status === 'error' && job.last_error && <pre class="job-error">{job.last_error}</pre>}
      <div class="job-actions">
        {paused
          ? <button class="pill" onClick={() => act('resume')}>Riprendi</button>
          : <button class="pill" onClick={() => act('pause')}>Pausa</button>}
        {confirm
          ? <>
              <button class="pill" onClick={() => setConfirm(false)}>Annulla</button>
              <button class="pill pill-danger" onClick={() => act('trigger')}>Avvia adesso</button>
            </>
          : <button class="pill" onClick={() => setConfirm(true)}>Avvia ora</button>}
      </div>
      {msg && <p class="small-note">{msg}</p>}
    </div>
  );
}

export function Cron() {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try {
      setJobs((await api<{ jobs: Job[] }>('/api/server/cron')).jobs);
      setError(null);
    } catch (e: any) {
      setError(e.message);
    }
  };

  useEffect(() => { load(); }, []);

  return (
    <section class="sub-page">
      <header class="chat-head">
        <a class="head-btn" href="#/server" title="Torna al server">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7" /></svg>
        </a>
        <div class="chat-title"><span>Cron</span></div>
        <span class="head-btn" />
      </header>
      <div class="list padded">
        {error && <p class="list-note">Non riesco a leggere i cron: {error}</p>}
        {jobs && !jobs.length && (
          <p class="list-note">Nessun cron. Si creano chiedendolo a Hermes ("ogni mattina alle 8 fammi un riepilogo…") o dalla dashboard.</p>
        )}
        {jobs?.map(j => <JobCard key={j.id} job={j} reload={load} />)}
      </div>
    </section>
  );
}
