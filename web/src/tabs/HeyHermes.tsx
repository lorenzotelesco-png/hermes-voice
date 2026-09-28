import { useEffect, useState } from 'preact/hooks';
import { api, postJSON } from '../lib/sse';
import { clock } from '../lib/format';

interface KeyStatus { configured: boolean; created: number | null; last_used: number | null; path: string }

function Copy({ label, value }: { label: string; value: string }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setDone(true);
      setTimeout(() => setDone(false), 1500);
    } catch { /* the value stays on screen to copy by hand */ }
  };
  return (
    <div class="copy-row">
      <span class="copy-label">{label}</span>
      <code>{value}</code>
      <button class="pill" onClick={copy}>{done ? 'copiato' : 'copia'}</button>
    </div>
  );
}

/** "Hey Hermes": a Vocal Shortcut runs a Shortcut that talks to Hermes through the hub. */
export function HeyHermes() {
  const [status, setStatus] = useState<KeyStatus | null>(null);
  const [key, setKey] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const url = `${location.origin}/api/assistant/ask`;

  const load = () => api<KeyStatus>('/api/assistant/key').then(setStatus).catch(e => setMsg(e.message));
  useEffect(() => { load(); }, []);

  const mint = async () => {
    if (status?.configured && !confirm('La Scorciatoia che usa la chiave attuale smetterà di funzionare. Continuare?')) return;
    setBusy(true);
    const r = await postJSON('/api/assistant/key', {});
    const d = await r.json().catch(() => ({}));
    if (r.ok) { setKey(d.key); load(); } else setMsg(d.error || 'errore');
    setBusy(false);
  };

  const revoke = async () => {
    if (!confirm('Revocare la chiave? "Hey Hermes" smetterà di rispondere.')) return;
    await api('/api/assistant/key', { method: 'DELETE' }).catch(e => setMsg(e.message));
    setKey(null);
    load();
  };

  const test = async () => {
    setMsg('chiedo a Hermes…');
    const r = await postJSON('/api/assistant/ask', { text: 'Rispondi solo: la prova funziona.', new: true });
    const d = await r.json().catch(() => ({}));
    setMsg(r.ok ? `Hermes: «${d.reply}»` : d.error || 'errore');
  };

  return (
    <div class="card hey">
      <p class="note-text">
        Parla con Hermes senza aprire l'app, anche a telefono bloccato: una frase come "Hey Hermes" fa
        partire una Scorciatoia che ti ascolta, chiede a Hermes e legge la risposta. Ha una chiave sua,
        che serve solo a parlare con Hermes: file, server e terminale restano chiusi.
      </p>

      {key ? (
        <>
          <p class="small-note warn-text">Copiala adesso: non la vedrai più.</p>
          <Copy label="Chiave" value={key} />
        </>
      ) : status && (
        <p class="small-note">
          {status.configured
            ? `Chiave attiva dal ${clock(status.created)}${status.last_used ? `, usata l'ultima volta ${clock(status.last_used)}` : ', mai usata'}.`
            : 'Nessuna chiave: creane una per la Scorciatoia.'}
        </p>
      )}
      <Copy label="Indirizzo" value={url} />

      <div class="job-actions">
        <button class="pill" disabled={busy} onClick={mint}>{status?.configured ? 'Nuova chiave' : 'Crea la chiave'}</button>
        {status?.configured && <button class="pill" onClick={revoke}>Revoca</button>}
        <button class="pill" onClick={test}>Prova</button>
      </div>
      {msg && <p class="small-note">{msg}</p>}

      <details class="howto">
        <summary>Come si prepara, una volta sola</summary>
        <p><b>1. La Scorciatoia</b>, nell'app Comandi: nuovo comando chiamato "Hermes", con queste azioni.</p>
        <ol>
          <li><b>Ripeti</b> 10 volte, e dentro:</li>
          <li><b>Detta testo</b> (Dictate Text): lingua Italiano, smetti di ascoltare dopo una pausa.</li>
          <li><b>Ottieni contenuti dell'URL</b>: l'indirizzo qui sopra; metodo POST; intestazioni
            <code>Authorization</code> = <code>Bearer </code> seguito dalla chiave, e
            <code>ngrok-skip-browser-warning</code> = <code>1</code>; corpo JSON con il campo
            <code>text</code> = Testo dettato.</li>
          <li><b>Ottieni valore dizionario</b> per la chiave <code>reply</code>, poi <b>Pronuncia testo</b> con quel valore.</li>
          <li><b>Ottieni valore dizionario</b> per <code>end</code> dai contenuti dell'URL; <b>Se</b> è uguale a 1,
            <b>Interrompi questo comando</b>.</li>
        </ol>
        <p><b>2. La frase</b>: Impostazioni › Accessibilità › Abbreviazioni vocali (Vocal Shortcuts) ›
          aggiungi azione › scegli il comando "Hermes", scrivi "Hey Hermes" e ripetila tre volte.</p>
        <p>Parli dopo il "ding" della dettatura. Per chiudere: "grazie" o "basta". Dopo 10 minuti di silenzio la
          conversazione successiva ne apre una nuova; tutte restano tra le sessioni della chat. Se Hermes chiede
          una conferma o ci mette più di 40 secondi, te lo dice e il resto arriva come notifica.</p>
      </details>
    </div>
  );
}
