import type { ComponentChildren } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import { api } from '../lib/sse';
import { clock } from '../lib/format';
import { uploadToVault } from '../lib/image';
import { renderMarkdown } from '../chat/markdown';
import { flashNext, goBack, leaveTo, takeFlash } from './nav';
import {
  ConflictError, type VaultIndex, baseName, dirName, dropDraft, editHref, folderHref, isImage, isNote, isPdf,
  loadIndex, noteHref, pushNote, rawUrl, readDraft, saveNote, splitFrontmatter, title, toMarkdown, today, writeDraft,
} from './vault';

interface Loaded { text: string; sha: string; mtime: number }

async function readNote(path: string): Promise<Loaded | null> {
  try {
    return await api<Loaded>(`/api/vault/note?path=${encodeURIComponent(path)}`);
  } catch (e: any) {
    if (e.status === 404) return null;
    throw e;
  }
}

function Head({ path, back, right }: { path: string; back: () => void; right?: ComponentChildren }) {
  return (
    <header class="chat-head">
      <button class="head-btn" title="Indietro" onClick={back}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7" /></svg>
      </button>
      <div class="chat-title">
        <span>{isNote(path) ? title(path) : baseName(path)}</span>
        <small>{dirName(path) || 'vault'}</small>
      </div>
      {right || <span class="head-btn" />}
    </header>
  );
}

// Links in a note open in the app when they point into the vault; anything
// else leaves in a new window, or the home-screen app would navigate away.
function onLinkClick(e: MouseEvent) {
  const a = (e.target as HTMLElement).closest('a');
  const href = a?.getAttribute('href');
  if (!a || !href || href.startsWith('#')) return;
  e.preventDefault();
  window.open(a.href, '_blank', 'noopener');
}

export function NoteView({ path }: { path: string }) {
  const [note, setNote] = useState<Loaded | null | undefined>(undefined);
  const [idx, setIdx] = useState<VaultIndex | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(takeFlash());
  const draft = readDraft(path);
  const back = () => goBack(folderHref(dirName(path)));

  useEffect(() => {
    if (!isNote(path)) return;
    Promise.all([readNote(path), loadIndex()])
      .then(([n, i]) => { setNote(n); setIdx(i); })
      .catch(e => setError(e.message));
  }, [path]);

  if (isImage(path) || isPdf(path)) {
    return (
      <section class="sub-page">
        <Head path={path} back={back} />
        <div class="list padded attachment">
          {isImage(path)
            ? <img src={rawUrl(path)} alt={baseName(path)} />
            : <button class="pill" onClick={() => window.open(rawUrl(path), '_blank')}>Apri il PDF</button>}
        </div>
      </section>
    );
  }

  const edit = (
    <a class="head-btn" href={editHref(path)} title="Modifica">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 19h4L19 9l-4-4L5 15zM13 7l4 4" /></svg>
    </a>
  );

  if (!isNote(path)) {
    return <section class="sub-page"><Head path={path} back={back} /><p class="list-note">L'app mostra note, immagini e PDF.</p></section>;
  }

  let html = '';
  let tags: string[] = [];
  if (note && idx) {
    const split = splitFrontmatter(note.text);
    tags = split.tags;
    html = renderMarkdown(toMarkdown(split.body, path, idx.files));
  }

  return (
    <section class="sub-page">
      <Head path={path} back={back} right={note !== null ? edit : undefined} />
      <div class="list padded note-page">
        {flash && <div class="flash ok" onClick={() => setFlash(null)}>{flash}</div>}
        {draft && note !== undefined && (
          <a class="flash warn-flash" href={editHref(path)}>Hai una modifica non salvata: tocca per riprenderla.</a>
        )}
        {error && <p class="list-note">Non riesco ad aprire la nota: {error}</p>}
        {note === undefined && !error && <p class="list-note">carico…</p>}
        {note === null && (
          <div class="empty-note">
            <p class="note-text">«{title(path)}» non esiste ancora.</p>
            <a class="pill" href={editHref(path)}>Creala</a>
          </div>
        )}
        {note && (
          <>
            {tags.length > 0 && <div class="tags">{tags.map(t => <span key={t}>#{t}</span>)}</div>}
            <article class="md note-body" onClick={onLinkClick} dangerouslySetInnerHTML={{ __html: html }} />
            <p class="small-note">
              modificata {clock(idx?.files.find(f => f.path === path)?.changed ?? note.mtime)}
            </p>
          </>
        )}
      </div>
    </section>
  );
}

export function Editor({ path }: { path: string }) {
  const [loaded, setLoaded] = useState<{ text: string; base: string | null } | null>(null);
  const [text, setText] = useState('');
  const [base, setBase] = useState<string | null>(null);
  const [restored, setRestored] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<ConflictError | null>(null);
  const [showTheirs, setShowTheirs] = useState(false);
  const [attaching, setAttaching] = useState(false);
  const area = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);

  useEffect(() => {
    readNote(path).then(n => {
      const original = { text: n ? n.text : '', base: n ? n.sha : null };
      setLoaded(original);
      // An unsaved edit comes back with the version it started from, so saving
      // it still goes through the merge-or-conflict check against today's note.
      const d = readDraft(path);
      if (d && d.text !== original.text) {
        setText(d.text); setBase(d.base); setRestored(true);
      } else {
        setText(original.text); setBase(original.base);
      }
    }).catch(e => setError(e.message));
  }, [path]);

  const dirty = loaded !== null && (text !== loaded.text || base !== loaded.base);

  const change = (value: string) => {
    setText(value);
    writeDraft(path, { text: value, base, at: Date.now() });
  };

  const finish = (target: string, msg: string) => {
    dropDraft(path);
    flashNext(msg);
    leaveTo(noteHref(target));
  };

  const save = async (target = path, againstBase = base, message?: string) => {
    setSaving(true);
    setError(null);
    try {
      const r = await saveNote(target, text, againstBase);
      finish(target, pushNote(r) || message || (r.merged ? 'Salvata, unita alle modifiche arrivate intanto dal PC.' : 'Salvata.'));
    } catch (e: any) {
      if (e instanceof ConflictError) { setConflict(e); setShowTheirs(false); } else setError(e.message);
    }
    setSaving(false);
  };

  const saveCopy = () => {
    const { date, time } = today();
    const copy = `${path.replace(/\.md$/i, '')} (telefono ${date} ${time.replace(':', '.')}).md`;
    setConflict(null);
    save(copy, null, `Salvata come copia: «${title(path)}» resta con la versione del PC.`);
  };

  const replace = () => {
    const c = conflict!;
    setConflict(null);
    save(path, c.theirsSha, c.theirs === null ? 'Nota ricreata.'
      : 'Salvata la tua versione; quella del PC resta nella cronologia di git.');
  };

  const cancel = () => {
    if (dirty && !confirm('Scartare le modifiche non salvate?')) return;
    dropDraft(path);
    goBack(noteHref(path));
  };

  const attach = async (list: FileList | null) => {
    if (!list?.length) return;
    setAttaching(true);
    setError(null);
    try {
      const el = area.current!;
      const at = el.selectionStart ?? text.length;
      let insert = '';
      for (const f of Array.from(list)) insert += `![[${(await uploadToVault(f)).name}]]\n`;
      const lead = at > 0 && text[at - 1] !== '\n' ? '\n' : '';
      change(text.slice(0, at) + lead + insert + text.slice(at));
    } catch (e: any) {
      setError(e.message);
    }
    setAttaching(false);
    if (picker.current) picker.current.value = '';
  };

  return (
    <section class="sub-page editor">
      <header class="chat-head">
        <button class="head-btn text-btn" onClick={cancel}>Annulla</button>
        <div class="chat-title">
          <span>{title(path)}</span>
          <small>{loaded?.base === null ? 'nuova nota' : dirty ? 'non salvata' : dirName(path) || 'vault'}</small>
        </div>
        <button class="head-btn text-btn strong" disabled={!loaded || saving || (!dirty && loaded.base !== null)}
                onClick={() => save()}>{saving ? '…' : 'Salva'}</button>
      </header>
      <div class="editor-bar">
        <button class="pill" disabled={attaching || !loaded} onClick={() => picker.current?.click()}>
          {attaching ? 'carico…' : 'Allega foto o PDF'}
        </button>
        {restored && <span class="small-note">bozza non salvata ripristinata</span>}
        <input ref={picker} type="file" accept="image/*,application/pdf" multiple hidden
               onChange={e => attach((e.target as HTMLInputElement).files)} />
      </div>
      {error && <div class="flash bad editor-flash" onClick={() => setError(null)}>{error}</div>}
      {loaded
        ? <textarea ref={area} class="editor-area" value={text} spellcheck autocapitalize="sentences"
                    onInput={e => change((e.target as HTMLTextAreaElement).value)} />
        : <p class="list-note">{error ? '' : 'carico…'}</p>}

      {conflict && (
        <div class="sheet-backdrop" onClick={e => { if (e.target === e.currentTarget) setConflict(null); }}>
          <div class="sheet" role="dialog" aria-label="Conflitto">
            <h3>{conflict.theirs === null ? 'La nota non c\'è più' : 'La nota è cambiata sul PC'}</h3>
            <p class="note-text">
              {conflict.theirs === null
                ? 'Mentre la modificavi è stata spostata o cancellata altrove.'
                : 'Mentre la modificavi è arrivata un\'altra versione, cambiata negli stessi punti. Non ho sovrascritto niente.'}
            </p>
            {conflict.theirs !== null && (
              <button class="link-btn" onClick={() => setShowTheirs(!showTheirs)}>
                {showTheirs ? 'Nascondi' : 'Mostra'} la versione del PC
              </button>
            )}
            {showTheirs && <pre class="cmd theirs">{conflict.theirs}</pre>}
            <div class="sheet-actions column">
              <button class="btn btn-ok" onClick={saveCopy}>Salva la mia come copia</button>
              <button class="btn btn-deny" onClick={replace}>
                {conflict.theirs === null ? 'Ricreala con la mia versione' : 'Sostituisci con la mia'}
              </button>
              <button class="btn btn-link" onClick={() => setConflict(null)}>Torna a modificare</button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
