import { Fragment } from 'preact';
import { useEffect, useLayoutEffect, useRef, useState } from 'preact/hooks';
import { api } from '../lib/sse';
import { Dictation } from '../lib/dictate';
import { voice } from '../voice/engine';
import { Icon } from '../files/icons';
import { type Attachment, type Chat, type Message, cache, merge, needsConnect, post } from './inbox';
import { Avatar } from './Avatar';

const KIND: Record<string, string> = {
  IMAGE: 'Foto', VIDEO: 'Video', VOICE: 'Messaggio vocale', AUDIO: 'Audio', FILE: 'File',
  STICKER: 'Sticker', LOCATION: 'Posizione', NOTICE: 'Avviso',
};

const hhmm = (t: number | null) =>
  t ? new Date(t * 1000).toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' }) : '';

function dayLabel(t: number) {
  const d = new Date(t * 1000);
  const today = new Date();
  const yesterday = new Date(Date.now() - 86400000);
  if (d.toDateString() === today.toDateString()) return 'Oggi';
  if (d.toDateString() === yesterday.toDateString()) return 'Ieri';
  return d.toLocaleDateString('it-IT', {
    weekday: 'long', day: 'numeric', month: 'long',
    year: d.getFullYear() === today.getFullYear() ? undefined : 'numeric',
  });
}

function snippet(m: Message) {
  if (m.deleted) return 'messaggio eliminato';
  return m.text ? m.text.slice(0, 120) : KIND[m.type] || 'messaggio';
}

// Links in a message open outside the app; everything else is plain text,
// never markup.
const URL_RE = /(https?:\/\/[^\s<>"]+[^\s<>".,;:!?)\]'])/g;

function Linked({ text }: { text: string }) {
  const parts = text.split(URL_RE);
  return <>{parts.map((p, i) => (i % 2 ? <a key={i} href={p} target="_blank" rel="noopener noreferrer">{p}</a> : p))}</>;
}

function Media({ a, onImage }: { a: Attachment; onImage: (url: string) => void }) {
  if (!a.url) return <p class="ib-note">{a.name || KIND.FILE} non disponibile</p>;
  const ratio = a.w && a.h ? `aspect-ratio:${a.w}/${a.h}` : '';
  if (a.kind === 'img') {
    return (
      <button class={`ib-media${a.sticker ? ' is-sticker' : ''}`} onClick={e => { e.stopPropagation(); onImage(a.url!); }}>
        <img src={a.url} alt="" loading="lazy" style={ratio} />
      </button>
    );
  }
  if (a.kind === 'video') {
    return <video class="ib-media" src={a.url} poster={a.poster || undefined} controls playsInline preload="none"
                  style={ratio} onClick={e => e.stopPropagation()} />;
  }
  if (a.kind === 'audio') {
    return (
      <div class="ib-audio" onClick={e => e.stopPropagation()}>
        <audio src={a.url} controls preload="none" />
        {a.transcript && <p class="ib-transcript">{a.transcript}</p>}
      </div>
    );
  }
  const size = a.bytes ? ` · ${a.bytes < 1048576 ? Math.max(1, Math.round(a.bytes / 1024)) + ' KB' : (a.bytes / 1048576).toFixed(1) + ' MB'}` : '';
  return (
    <button class="ib-file" onClick={e => { e.stopPropagation(); window.open(a.url!, '_blank', 'noopener'); }}>
      <Icon name="attach" /><span>{a.name || 'file'}{size}</span>
    </button>
  );
}

function Tick({ m }: { m: Message }) {
  if (m.pending) return <span class="tick" title="in invio">·</span>;
  if (m.failed || m.status?.startsWith('FAIL')) return <span class="tick bad" title="non inviato">!</span>;
  return <span class="tick" title="inviato">✓</span>;
}

interface BubbleProps {
  m: Message;
  showSender: boolean;
  replied: Message | null;
  selected: boolean;
  onSelect: () => void;
  onReply: () => void;
  onImage: (url: string) => void;
}

function Bubble({ m, showSender, replied, selected, onSelect, onReply, onImage }: BubbleProps) {
  const failed = m.failed || m.status?.startsWith('FAIL');
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(m.text); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* no clipboard */ }
  };
  return (
    <div class={`ib-row${m.mine ? ' mine' : ''}`}>
      <div class={`ib${m.pending ? ' is-pending' : ''}${failed ? ' is-failed' : ''}${selected ? ' is-selected' : ''}`}
           onClick={onSelect}>
        {showSender && <span class="ib-sender">{m.sender}</span>}
        {m.reply_to && (
          <div class="ib-quote">
            {replied ? <><b>{replied.mine ? 'Tu' : replied.sender}</b> {snippet(replied)}</> : 'risposta a un messaggio precedente'}
          </div>
        )}
        {m.deleted ? <p class="ib-note">messaggio eliminato</p> : (
          <>
            {m.attachments.map((a, i) => <Media key={i} a={a} onImage={onImage} />)}
            {m.text && <p class="ib-text"><Linked text={m.text} /></p>}
            {!m.text && !m.attachments.length && <p class="ib-note">{KIND[m.type] || 'messaggio non supportato qui'}</p>}
          </>
        )}
        <span class="ib-meta">{m.edited && 'modificato · '}{hhmm(m.time)}{m.mine && <Tick m={m} />}</span>
      </div>
      {m.reactions.length > 0 && (
        <div class="ib-reactions">
          {m.reactions.map(r => <span key={r.key} class={r.mine ? 'mine' : ''}>{r.key}{r.count > 1 ? ` ${r.count}` : ''}</span>)}
        </div>
      )}
      {m.failed && <p class="ib-failed">non inviato: {m.failed}</p>}
      {selected && !m.deleted && !m.pending && (
        <div class="ib-actions">
          <button onClick={onReply}>Rispondi</button>
          {m.text && <button onClick={copy}>{copied ? 'Copiato' : 'Copia'}</button>}
        </div>
      )}
    </div>
  );
}

interface Detail { chat: Chat | null; messages: Message[]; older: string | null }

/** One chat: its messages, live, and a reply box. Opening it marks it read, as on the phone. */
export function Conversation({ id }: { id: string }) {
  const [chat, setChat] = useState<Chat | null>(cache.chats?.find(c => c.id === id) ?? null);
  const [msgs, setMsgs] = useState<Message[] | null>(null);
  const [older, setOlder] = useState<string | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reply, setReply] = useState<Message | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [image, setImage] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const prepend = useRef<number | null>(null);   // scroll height before older messages went on top
  const paged = useRef(false);
  const sentIds = useRef(new Set<string>());      // Beeper's ids for messages sent from here
  const path = `/api/inbox/chats/${encodeURIComponent(id)}`;

  // A message sent from here shows at once; Beeper's copy replaces it when it
  // comes back (same text, from me, within a few minutes). Beeper may first
  // echo it under the id it answered the send with, then under its final one.
  const settle = (list: Message[]) => {
    const sent = sentIds.current;
    const near = (a: Message, b: Message) => a.text === b.text && Math.abs((a.time ?? 0) - (b.time ?? 0)) < 300;
    return list.filter(x => {
      if (x.pending) return !list.some(y => y.mine && !y.pending && near(x, y));
      if (sent.has(x.id)) return !list.some(y => y !== x && y.mine && !y.pending && !sent.has(y.id) && near(x, y));
      return true;
    });
  };

  const readTimer = useRef<number | undefined>();
  const markRead = (last: Message | undefined) => {
    if (!last || last.pending || document.visibilityState !== 'visible') return;
    clearTimeout(readTimer.current);
    readTimer.current = window.setTimeout(() => {
      post(`${path}/read`, { message_id: last.id }).catch(() => {});
    }, 600);
  };

  const load = async () => {
    try {
      const d = await api<Detail>(path);
      if (d.chat) setChat(d.chat);
      setMsgs(m => settle(merge(m || [], d.messages)));
      if (!paged.current) setOlder(d.older);
      setError(null);
      const c = d.chat;
      if (c && (c.unread > 0 || c.marked_unread)) markRead(d.messages[d.messages.length - 1]);
    } catch (e: any) {
      if (needsConnect(e)) { location.replace('#/inbox'); return; }
      setError(e.message);
    }
  };

  const loadOlder = async () => {
    if (!older || loadingOlder) return;
    setLoadingOlder(true);
    try {
      const d = await api<Detail>(`${path}?cursor=${encodeURIComponent(older)}`);
      paged.current = true;
      prepend.current = box.current ? box.current.scrollHeight - box.current.scrollTop : null;
      setMsgs(m => merge(m || [], d.messages));
      setOlder(d.older);
    } catch (e: any) {
      setError(e.message);
    }
    setLoadingOlder(false);
  };

  // Live, for this chat only. Each reopen reads the chat again: EventSource
  // reconnects by itself when iOS drops it, and nothing said meanwhile is lost.
  useEffect(() => {
    load();
    const es = new EventSource(`/api/inbox/events?chat=${encodeURIComponent(id)}`);
    let opened = false;
    es.onopen = () => { if (opened) load(); opened = true; };
    es.onmessage = ev => {
      let e: any;
      try { e = JSON.parse(ev.data); } catch { return; }
      if (e.t === 'message' && e.chat === id) {
        setMsgs(m => settle(merge(m || [], [e.message])));
        if (!e.message.mine) markRead(e.message);
      } else if (e.t === 'deleted' && e.chat === id) {
        const gone = new Set<string>(e.ids);
        setMsgs(m => m && m.map(x => (gone.has(x.id) ? { ...x, deleted: true, text: '', attachments: [] } : x)));
      } else if (e.t === 'changed' && e.chat === id) {
        load();
      }
    };
    return () => { es.close(); clearTimeout(readTimer.current); };
  }, [id]);

  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    if (prepend.current != null) {
      el.scrollTop = el.scrollHeight - prepend.current;
      prepend.current = null;
    } else if (pinned.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [msgs]);

  const onScroll = () => {
    const el = box.current!;
    pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  const send = async (text: string) => {
    const temp: Message = {
      id: `local-${Date.now()}`, sender: '', sender_id: null, mine: true, time: Date.now() / 1000, sort: null,
      type: 'TEXT', text, edited: false, deleted: false, reply_to: reply?.id ?? null, status: null, failed: null,
      attachments: [], reactions: [], pending: true,
    };
    const replyTo = reply?.id;
    setReply(null);
    pinned.current = true;
    setMsgs(m => merge(m || [], [temp]));
    try {
      const r = await post<{ pending_id: string | null }>(`${path}/messages`, { text, reply_to: replyTo });
      if (r.pending_id) sentIds.current.add(r.pending_id);
      // Beeper's copy normally arrives live; if the stream is down, read it.
      setTimeout(load, 3000);
      return true;
    } catch (e: any) {
      setMsgs(m => m && m.map(x => (x.id === temp.id ? { ...x, pending: false, failed: e.message } : x)));
      return false;
    }
  };

  const byId = new Map((msgs || []).map(m => [m.id, m]));
  const title = chat?.title || '';
  let lastDay = '';
  let lastSender: string | null = null;

  return (
    <section class="sub-page conversation">
      <header class="chat-head">
        <a class="head-btn" href="#/inbox" title="Tutte le chat">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7" /></svg>
        </a>
        {chat && <Avatar chat={chat} size={32} />}
        <div class="chat-title conv-title">
          <span>{title || 'Chat'}</span>
          {chat && <small>{chat.network}{chat.muted ? ' · silenziata' : ''}</small>}
        </div>
      </header>

      <div class="thread ib-thread" ref={box} onScroll={onScroll} onClick={e => { if (e.target === box.current) setSelected(null); }}>
        {older && (
          <button class="load-more" disabled={loadingOlder} onClick={loadOlder}>
            {loadingOlder ? 'carico…' : 'Messaggi precedenti'}
          </button>
        )}
        {msgs === null && !error && <div class="loading">carico la chat…</div>}
        {error && <p class="list-note">Non riesco a caricare: {error}</p>}
        {msgs && !msgs.length && <p class="list-note">Nessun messaggio.</p>}
        {msgs?.map(m => {
          const day = m.time ? dayLabel(m.time) : '';
          const newDay = day !== lastDay;
          lastDay = day;
          const showSender = !!chat?.group && !m.mine && (newDay || m.sender_id !== lastSender);
          lastSender = m.mine ? null : m.sender_id;
          return (
            <Fragment key={m.id}>
              {newDay && day && <div class="note day">{day}</div>}
              <Bubble m={m} showSender={showSender}
                      replied={m.reply_to ? byId.get(m.reply_to) ?? null : null}
                      selected={selected === m.id}
                      onSelect={() => setSelected(s => (s === m.id ? null : m.id))}
                      onReply={() => { setReply(m); setSelected(null); }}
                      onImage={setImage} />
            </Fragment>
          );
        })}
      </div>

      {chat?.readonly
        ? <p class="composer ro-note">Questa chat è in sola lettura.</p>
        : <Composer reply={reply} onCancelReply={() => setReply(null)} onSend={send} placeholder={`Scrivi a ${title || 'questa chat'}`} />}

      {image && (
        <div class="lightbox" onClick={() => setImage(null)}>
          <img src={image} alt="" />
        </div>
      )}
    </section>
  );
}

function Composer({ reply, onCancelReply, onSend, placeholder }: {
  reply: Message | null; onCancelReply: () => void; onSend: (text: string) => Promise<boolean>; placeholder: string;
}) {
  const [text, setText] = useState('');
  const [rec, setRec] = useState<'idle' | 'rec' | 'stt'>('idle');
  const [note, setNote] = useState<string | null>(null);
  const ref = useRef<HTMLTextAreaElement>(null);
  const dictation = useRef(new Dictation());

  useEffect(() => () => dictation.current.cancel(), []);
  useEffect(() => { if (reply) ref.current?.focus(); }, [reply]);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 140) + 'px';
  }, [text]);

  const send = async () => {
    const t = text.trim();
    if (!t) return;
    setText('');
    if (!(await onSend(t))) setText(cur => cur || t);
  };

  // Dictation fills the box; sending stays a tap, since it reaches someone.
  const mic = async () => {
    const d = dictation.current;
    try {
      if (rec === 'idle') {
        await d.start();
        setRec('rec');
        setNote(null);
      } else if (rec === 'rec') {
        setRec('stt');
        const said = await d.stop();
        if (said) setText(t => (t.trim() ? t.trim() + ' ' : '') + said);
        setRec('idle');
      }
    } catch (e: any) {
      d.cancel();
      setRec('idle');
      setNote(e.name === 'NotAllowedError' ? 'Microfono non consentito' : e.message);
    }
  };

  return (
    <div class="ib-compose">
      {reply && (
        <div class="reply-bar">
          <div><b>Rispondi a {reply.mine ? 'te' : reply.sender || 'questo messaggio'}</b><span>{snippet(reply)}</span></div>
          <button class="head-btn" title="Annulla" onClick={onCancelReply}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
          </button>
        </div>
      )}
      {note && <p class="small-note bad-note compose-note">{note}</p>}
      <form class="composer" onSubmit={e => { e.preventDefault(); send(); }}>
        <textarea ref={ref} rows={1} value={text}
                  placeholder={rec === 'rec' ? 'ti ascolto… tocca ■ per finire' : placeholder}
                  onInput={e => setText((e.target as HTMLTextAreaElement).value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && !e.shiftKey && !('ontouchstart' in window)) { e.preventDefault(); send(); }
                  }} />
        {text.trim()
          ? <button type="submit" class="round send" title="Invia">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7" /></svg>
            </button>
          : <button type="button" class={`round-btn${rec === 'rec' ? ' is-rec' : ''}`} disabled={rec === 'stt' || voice.active}
                    onClick={mic} title={rec === 'rec' ? 'Fine' : 'Detta'}>
              {rec === 'stt' ? <span class="spin" /> : <Icon name={rec === 'rec' ? 'stop' : 'mic'} />}
            </button>}
      </form>
    </div>
  );
}
