import { useEffect, useState } from 'preact/hooks';
import { api } from '../lib/sse';
import { Icon, fileIcon } from './icons';
import { goBack } from './nav';
import { baseName, dirName } from './vault';

// Read through the dashboard; the hub only asks it for paths under these.
export const ROOTS: Record<string, { label: string; note: string }> = {
  hermes: { label: 'Codice di Hermes', note: 'il repo installato' },
  logs: { label: 'Log di Hermes', note: 'i file interi; i recenti in Server › Log' },
};

const enc = (p: string) => encodeURIComponent(p);
const href = (root: string, kind: 'dir' | 'file', path: string) =>
  path ? `#/file/${root}/${kind === 'dir' ? 'cartella' : 'file'}/${enc(path)}` : `#/file/${root}`;

interface Entry { name: string; dir: boolean; path: string }
interface Text { text: string; binary: boolean; truncated: boolean; size: number | null }

export function Hermes({ root, kind, path }: { root: string; kind: 'dir' | 'file'; path: string }) {
  const [entries, setEntries] = useState<Entry[] | null>(null);
  const [file, setFile] = useState<Text | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const q = encodeURIComponent(path);
    const load = kind === 'dir'
      ? api<{ entries: Entry[]; error?: string }>(`/api/files/${root}/list?path=${q}`).then(d => {
          setEntries(d.entries);
          if (d.error) setError(d.error === 'ENOENT' ? 'cartella inesistente' : d.error);
        })
      : api<Text>(`/api/files/${root}/read?path=${q}`).then(setFile);
    load.catch(e => setError(e.message));
  }, [root, kind, path]);

  const up = path.includes('/') ? href(root, 'dir', dirName(path)) : path ? href(root, 'dir', '') : '#/file';

  return (
    <section class="sub-page">
      <header class="chat-head">
        <button class="head-btn" title="Indietro" onClick={() => goBack(up)}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7" /></svg>
        </button>
        <div class="chat-title">
          <span>{path ? baseName(path) : ROOTS[root].label}</span>
          <small>{path ? `${ROOTS[root].label}${dirName(path) ? ' / ' + dirName(path) : ''}` : 'sola lettura'}</small>
        </div>
        <span class="head-btn" />
      </header>
      {kind === 'dir' ? (
        <div class="list padded">
          {error && <p class="list-note">{error}</p>}
          {!entries && !error && <p class="list-note">carico…</p>}
          {entries && (
            <div class="card list">
              {entries.map(e => (
                <a key={e.path} class="frow" href={href(root, e.dir ? 'dir' : 'file', e.path)}>
                  <Icon name={e.dir ? 'folder' : fileIcon(e.name)} />
                  <div class="frow-main"><span class="frow-title">{e.name}</span></div>
                </a>
              ))}
              {!entries.length && !error && <p class="list-note">Cartella vuota.</p>}
            </div>
          )}
        </div>
      ) : (
        <div class="code-view">
          {error && <p class="list-note">{error}</p>}
          {!file && !error && <p class="list-note">carico…</p>}
          {file?.binary && <p class="list-note">File binario: non si mostra come testo.</p>}
          {file && !file.binary && <pre>{file.text}</pre>}
          {file?.truncated && <p class="small-note">Mostrati i primi 512 KB.</p>}
        </div>
      )}
    </section>
  );
}
