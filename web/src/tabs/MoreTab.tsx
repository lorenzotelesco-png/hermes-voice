import { useEffect, useState } from 'preact/hooks';
import { DEBUG, DEBUG_KEY } from '../voice/engine';

declare const __BUILD__: { sha: string; time: string };

function setFlag(key: string, on: boolean) {
  try {
    if (on) localStorage.setItem(key, '1');
    else localStorage.removeItem(key);
  } catch { /* private mode: the query string still works */ }
}

export function MoreTab() {
  const [server, setServer] = useState<'...' | 'ok' | 'non raggiungibile'>('...');

  useEffect(() => {
    fetch('/api/health')
      .then(r => setServer(r.ok ? 'ok' : 'non raggiungibile'))
      .catch(() => setServer('non raggiungibile'));
  }, []);

  const built = new Date(__BUILD__.time).toLocaleString('it-IT', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  });

  return (
    <section class="page more">
      <h2>Altro</h2>
      <dl class="rows">
        <div><dt>Server</dt><dd>{server}</dd></div>
        <div><dt>Versione</dt><dd>{__BUILD__.sha} · {built}</dd></div>
      </dl>
      <label class="switch">
        <span>
          Tempi sullo schermo
          <small>Mostra dove va il tempo di ogni turno vocale.</small>
        </span>
        <input type="checkbox" checked={DEBUG}
               onChange={e => { setFlag(DEBUG_KEY, (e.target as HTMLInputElement).checked); location.reload(); }} />
      </label>
    </section>
  );
}
