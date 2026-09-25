import { useEffect, useState } from 'preact/hooks';
import type { JSX } from 'preact';
import { voice } from './voice/engine';
import { chat } from './chat/store';
import { ChatTab } from './chat/ChatTab';
import { Sessions } from './chat/Sessions';
import { ApprovalSheet } from './chat/ApprovalSheet';
import { VoiceStage } from './chat/VoiceStage';
import { SoonTab } from './tabs/SoonTab';
import { ServerTab } from './server/ServerTab';
import { Logs } from './server/Logs';
import { Cron } from './server/Cron';
import { MoreTab } from './tabs/MoreTab';

type TabId = 'chat' | 'server' | 'file' | 'altro';

const ICONS: Record<TabId, JSX.Element> = {
  chat: <path d="M4 5h16v11H9l-5 4z" />,
  server: <g><rect x="4" y="4" width="16" height="6" rx="1.5" /><rect x="4" y="14" width="16" height="6" rx="1.5" /><circle cx="8" cy="7" r=".6" /><circle cx="8" cy="17" r=".6" /></g>,
  file: <path d="M4 6.5A1.5 1.5 0 0 1 5.5 5H10l2 2h6.5A1.5 1.5 0 0 1 20 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 17.5z" />,
  altro: <g><circle cx="6" cy="12" r="1.2" /><circle cx="12" cy="12" r="1.2" /><circle cx="18" cy="12" r="1.2" /></g>,
};

const TABS: { id: TabId; label: string }[] = [
  { id: 'chat', label: 'Chat' },
  { id: 'server', label: 'Server' },
  { id: 'file', label: 'File' },
  { id: 'altro', label: 'Altro' },
];

// #/chat, #/chat/sessioni, #/server, #/server/log, #/server/cron ...
function currentRoute(): { tab: TabId; sub: string } {
  const [id, sub = ''] = location.hash.replace(/^#\/?/, '').split('/');
  return TABS.some(t => t.id === id) ? { tab: id as TabId, sub } : { tab: 'chat', sub: '' };
}

export function App() {
  const [route, setRoute] = useState(currentRoute);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [voiceOn, setVoiceOn] = useState(voice.active);

  useEffect(() => {
    const onHash = () => setRoute(currentRoute());
    addEventListener('hashchange', onHash);
    return () => removeEventListener('hashchange', onHash);
  }, []);

  // Voice on, or Hermes still working: worth a dot on the tab from elsewhere.
  useEffect(() => {
    const update = () => { setBusy(voice.active || chat.snapshot.running); setVoiceOn(voice.active); };
    const a = voice.subscribe(update);
    const b = chat.subscribe(update);
    return () => { a(); b(); };
  }, []);

  useEffect(() => {
    let timer: number | undefined;
    const show = (msg: string) => {
      setError(msg);
      clearTimeout(timer);
      timer = window.setTimeout(() => setError(null), 6000);
    };
    voice.onError = show;
    chat.onError = show;
    chat.init();
  }, []);

  const { tab, sub } = route;
  return (
    <div class="app">
      {error && <div class="toast" role="alert" onClick={() => setError(null)}>{error}</div>}
      <main class="content">
        {/* The voice session lives in the engine and the conversation in the
            store, not in these components: leaving the tab ends neither. */}
        {tab === 'chat' && (sub === 'sessioni' ? <Sessions /> : <ChatTab />)}
        {tab === 'server' && (sub === 'log' ? <Logs /> : sub === 'cron' ? <Cron /> : <ServerTab />)}
        {tab === 'file' && (
          <SoonTab title="File" phase={3}>
            Il vault Obsidian e i file di lavoro, da leggere e modificare dal telefono.
          </SoonTab>
        )}
        {tab === 'altro' && <MoreTab />}
      </main>
      <nav class="tabbar">
        {TABS.map(t => (
          <a key={t.id} href={`#/${t.id}`} class={tab === t.id ? 'is-active' : ''}
             aria-current={tab === t.id ? 'page' : undefined}>
            <svg viewBox="0 0 24 24" aria-hidden="true">{ICONS[t.id]}</svg>
            <span>{t.label}</span>
            {t.id === 'chat' && busy && tab !== 'chat' && <i class="live-dot" title="Hermes è attivo" />}
          </a>
        ))}
      </nav>
      {/* Voice mode covers everything; closing it shows the chat, where the
          transcript of what was said has been building up underneath. */}
      {voiceOn && <VoiceStage />}
      <ApprovalSheet />
    </div>
  );
}
