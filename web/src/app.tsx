import { useEffect, useState } from 'preact/hooks';
import type { JSX } from 'preact';
import { voice } from './voice/engine';
import { VoiceTab } from './tabs/VoiceTab';
import { SoonTab } from './tabs/SoonTab';
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

function currentTab(): TabId {
  const id = location.hash.replace(/^#\/?/, '') as TabId;
  return TABS.some(t => t.id === id) ? id : 'chat';
}

export function App() {
  const [tab, setTab] = useState<TabId>(currentTab);
  const [error, setError] = useState<string | null>(null);
  const [voiceOn, setVoiceOn] = useState(voice.active);

  useEffect(() => {
    const onHash = () => setTab(currentTab());
    addEventListener('hashchange', onHash);
    return () => removeEventListener('hashchange', onHash);
  }, []);

  useEffect(() => voice.subscribe(s => setVoiceOn(s.state !== 'off')), []);

  useEffect(() => {
    let timer: number | undefined;
    voice.onError = msg => {
      setError(msg);
      clearTimeout(timer);
      timer = window.setTimeout(() => setError(null), 6000);
    };
  }, []);

  return (
    <div class="app">
      {error && <div class="toast" role="alert" onClick={() => setError(null)}>{error}</div>}
      <main class="content">
        {/* The voice session lives in the engine, not in this component, so
            leaving the tab does not end the conversation. */}
        {tab === 'chat' && <VoiceTab />}
        {tab === 'server' && (
          <SoonTab title="Server" phase={2}>
            Stato dei servizi, CPU, RAM e disco, log, cron, costi e notifiche quando qualcosa si ferma.
          </SoonTab>
        )}
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
            {t.id === 'chat' && voiceOn && tab !== 'chat' && <i class="live-dot" title="Conversazione attiva" />}
          </a>
        ))}
      </nav>
    </div>
  );
}
