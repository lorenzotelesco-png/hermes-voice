import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'preact/hooks';
import { chat, type ChatSnap, type Item } from './store';
import { voice } from '../voice/engine';
import { renderMarkdown } from './markdown';
import { sourceLabel, toolLabel } from './labels';
import { MicIcon, VoiceDock } from './VoiceDock';

function Markdown({ text }: { text: string }) {
  const html = useMemo(() => renderMarkdown(text), [text]);
  return (
    <div class="md" dangerouslySetInnerHTML={{ __html: html }}
         onClick={e => {
           // Links leave the app: in a home-screen PWA a plain link would
           // replace the app itself with the page.
           const a = (e.target as HTMLElement).closest('a');
           if (a && a.href) { e.preventDefault(); window.open(a.href, '_blank', 'noopener'); }
         }} />
  );
}

function ToolCard({ item }: { item: Extract<Item, { role: 'tool' }> }) {
  const status = item.state === 'running' ? 'in corso' : item.state === 'error' ? 'non riuscito' : '';
  return (
    <div class={`tool is-${item.state}`}>
      <span class="tool-dot" aria-hidden="true" />
      <span class="tool-name">{toolLabel(item.name)}</span>
      {item.preview && <code class="tool-preview">{item.preview}</code>}
      {status && <span class="tool-status">{status}</span>}
    </div>
  );
}

function Bubble({ item }: { item: Item }) {
  switch (item.role) {
    case 'user':
      return (
        <div class="msg msg-user">
          {item.voice && <span class="via" title="detto a voce"><MicIcon /></span>}
          <p>{item.text}</p>
        </div>
      );
    case 'assistant':
      return (
        <div class={`msg msg-bot${item.streaming ? ' is-streaming' : ''}`}>
          <Markdown text={item.text} />
        </div>
      );
    case 'tool':
      return <ToolCard item={item} />;
    case 'note':
      return <div class="note">{item.text}</div>;
  }
}

function Composer({ running }: { running: boolean }) {
  const [text, setText] = useState('');
  const ref = useRef<HTMLTextAreaElement>(null);

  // Grow with the text up to a few lines, then scroll inside.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 140) + 'px';
  }, [text]);

  const send = () => {
    const t = text.trim();
    if (!t || running) return;
    setText('');
    chat.send(t);
  };

  return (
    <form class="composer" onSubmit={e => { e.preventDefault(); send(); }}>
      <button type="button" class="round mic" title="Parla" onClick={() => voice.start()}>
        <MicIcon />
      </button>
      <textarea ref={ref} rows={1} value={text} placeholder="Scrivi a Hermes"
                onInput={e => setText((e.target as HTMLTextAreaElement).value)}
                onKeyDown={e => {
                  // Enter sends on a keyboard; the phone's return key keeps
                  // its newline, and sending is the button.
                  if (e.key === 'Enter' && !e.shiftKey && !('ontouchstart' in window)) {
                    e.preventDefault();
                    send();
                  }
                }} />
      {running
        ? <button type="button" class="round stop" title="Ferma" onClick={() => chat.stop()}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2" /></svg>
          </button>
        : <button type="submit" class="round send" title="Invia" disabled={!text.trim()}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7" /></svg>
          </button>}
    </form>
  );
}

export function ChatTab() {
  const [snap, setSnap] = useState<ChatSnap>(chat.snapshot);
  const [voiceOn, setVoiceOn] = useState(voice.active);
  const scroller = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);

  useEffect(() => chat.subscribe(setSnap), []);
  useEffect(() => voice.subscribe(s => setVoiceOn(s.state !== 'off')), []);

  // Follow the conversation while the reader is at the bottom; leave them
  // alone once they scroll up to read something older.
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [snap.items, snap.running, voiceOn]);

  const onScroll = () => {
    const el = scroller.current!;
    pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  const last = snap.items[snap.items.length - 1];
  const waiting = snap.running && !(last && last.role === 'assistant' && last.streaming)
    && !(last && last.role === 'tool' && last.state === 'running');
  const empty = !snap.items.length && !snap.loading;
  const foreign = snap.source && snap.source !== 'api_server';

  return (
    <section class="chat">
      <header class="chat-head">
        <a class="head-btn" href="#/chat/sessioni" title="Conversazioni">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h10" /></svg>
        </a>
        <div class="chat-title">
          <span>{snap.title || (snap.sessionId ? 'Conversazione' : 'Nuova conversazione')}</span>
          {foreign && <small>da {sourceLabel(snap.source)}</small>}
        </div>
        <button class="head-btn" title="Nuova conversazione" disabled={!snap.sessionId}
                onClick={() => chat.newChat()}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
        </button>
      </header>

      <div class="thread" ref={scroller} onScroll={onScroll}>
        {empty && (
          <div class="empty">
            <h1>HERMES</h1>
            <span class="rule" />
            <p>scrivi o tocca il microfono</p>
          </div>
        )}
        {snap.loading && !snap.items.length && <div class="loading">carico la conversazione…</div>}
        {snap.items.map(item => <Bubble key={item.id} item={item} />)}
        {waiting && <div class="typing" aria-label="Hermes sta scrivendo"><i /><i /><i /></div>}
      </div>

      {voiceOn ? <VoiceDock /> : <Composer running={snap.running} />}
    </section>
  );
}
