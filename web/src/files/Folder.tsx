import { useEffect, useRef, useState } from 'preact/hooks';
import { clock } from '../lib/format';
import { uploadToVault } from '../lib/image';
import { Icon, fileIcon } from './icons';
import { goBack } from './nav';
import {
  type VaultIndex, baseName, cachedIndex, dirName, editHref, folderHref, loadIndex, noteHref, pushNote, title,
} from './vault';

function size(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 2 ** 20) return `${Math.round(n / 1024)} KB`;
  return `${(n / 2 ** 20).toLocaleString('it-IT', { maximumFractionDigits: 1 })} MB`;
}

export function Folder({ dir }: { dir: string }) {
  const [idx, setIdx] = useState<VaultIndex | null>(cachedIndex());
  const [error, setError] = useState<string | null>(null);
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<{ ok: boolean; text: string } | null>(null);
  const picker = useRef<HTMLInputElement>(null);

  useEffect(() => { loadIndex().then(setIdx).catch(e => setError(e.message)); }, []);

  const dirs = idx ? idx.dirs.filter(d => dirName(d) === dir) : [];
  const files = idx ? idx.files.filter(f => dirName(f.path) === dir) : [];
  // Notes by name, attachments after them, newest first.
  const notes = files.filter(f => /\.(md|txt)$/i.test(f.path));
  const others = files.filter(f => !/\.(md|txt)$/i.test(f.path)).sort((a, b) => b.changed - a.changed);

  const create = () => {
    const clean = name.trim().replace(/[\\/:*?"<>|#^[\]]+/g, '-').replace(/^\.+/, '');
    if (!clean) return;
    const path = (dir ? dir + '/' : '') + (clean.endsWith('.md') ? clean : clean + '.md');
    if (idx?.files.some(f => f.path.toLowerCase() === path.toLowerCase())) {
      setFlash({ ok: false, text: `«${title(path)}» esiste già qui.` });
      return;
    }
    location.hash = editHref(path);
  };

  const upload = async (list: FileList | null) => {
    if (!list?.length) return;
    setBusy(true);
    setFlash(null);
    const done: string[] = [];
    try {
      for (const f of Array.from(list)) {
        const r = await uploadToVault(f);
        done.push(r.name);
        const note = pushNote(r);
        if (note) setFlash({ ok: true, text: note });
      }
      setFlash(f => f || { ok: true, text: `Caricato in ${cachedIndex()?.attachments || 'Allegati'}: ${done.join(', ')}` });
      setIdx(await loadIndex(true));
    } catch (e: any) {
      setFlash({ ok: false, text: (done.length ? `Caricati ${done.join(', ')}; poi: ` : '') + e.message });
    }
    setBusy(false);
    if (picker.current) picker.current.value = '';
  };

  return (
    <section class="sub-page">
      <header class="chat-head">
        <button class="head-btn" title="Indietro" onClick={() => goBack(dir ? folderHref(dirName(dir)) : '#/file')}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7" /></svg>
        </button>
        <div class="chat-title"><span>{dir ? baseName(dir) : 'Vault'}</span>{dir.includes('/') && <small>{dirName(dir)}</small>}</div>
        <button class="head-btn" title="Nuova nota" onClick={() => { setNaming(true); setName(''); }}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
        </button>
      </header>

      <div class="list padded">
        {flash && <div class={`flash ${flash.ok ? 'ok' : 'bad'}`} onClick={() => setFlash(null)}>{flash.text}</div>}
        {naming && (
          <div class="card new-note">
            <input autofocus placeholder="Titolo della nuova nota" value={name} enterkeyhint="done"
                   onInput={e => setName((e.target as HTMLInputElement).value)}
                   onKeyDown={e => { if (e.key === 'Enter') create(); if (e.key === 'Escape') setNaming(false); }} />
            <div class="job-actions">
              <button class="pill" onClick={() => setNaming(false)}>Annulla</button>
              <button class="pill" disabled={!name.trim()} onClick={create}>Crea</button>
            </div>
          </div>
        )}
        {error && <p class="list-note">Non riesco a leggere il vault: {error}</p>}
        {idx && (
          <div class="card list">
            {dirs.map(d => (
              <a key={d} class="frow" href={folderHref(d)}>
                <Icon name="folder" />
                <div class="frow-main"><span class="frow-title">{baseName(d)}</span></div>
              </a>
            ))}
            {[...notes, ...others].map(f => (
              <a key={f.path} class="frow" href={noteHref(f.path)}>
                <Icon name={fileIcon(f.path)} />
                <div class="frow-main">
                  <span class="frow-title">{/\.md$/i.test(f.path) ? title(f.path) : baseName(f.path)}</span>
                  <span class="frow-sub">{clock(f.changed)} · {size(f.size)}</span>
                </div>
              </a>
            ))}
            {!dirs.length && !files.length && <p class="list-note">Cartella vuota.</p>}
          </div>
        )}
        <div class="folder-actions">
          <button class="pill" onClick={() => { setNaming(true); setName(''); }}>Nuova nota</button>
          <button class="pill" disabled={busy} onClick={() => picker.current?.click()}>
            {busy ? 'carico…' : 'Carica foto o PDF'}
          </button>
        </div>
        <input ref={picker} type="file" accept="image/*,application/pdf" multiple hidden
               onChange={e => upload((e.target as HTMLInputElement).files)} />
      </div>
    </section>
  );
}
