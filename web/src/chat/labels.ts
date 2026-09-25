const TOOLS: Record<string, string> = {
  terminal: 'Terminale',
  process: 'Processo',
  execute_code: 'Codice',
  web_search: 'Ricerca web',
  web_extract: 'Pagina web',
  browser_navigate: 'Browser',
  read_file: 'Lettura file',
  write_file: 'Scrittura file',
  patch: 'Modifica file',
  search_files: 'Ricerca file',
  memory: 'Memoria',
  session_search: 'Ricerca conversazioni',
  delegate_task: 'Sotto-agente',
  vision_analyze: 'Immagine',
  image_generate: 'Immagine',
  text_to_speech: 'Voce',
  todo: 'Lista',
  clarify: 'Domanda',
  cronjob: 'Cron',
  send_message: 'Messaggio',
  skill_view: 'Skill',
  skills_list: 'Skill',
};

export function toolLabel(name: string) {
  if (TOOLS[name]) return TOOLS[name];
  if (name.startsWith('browser_')) return 'Browser';
  if (name.startsWith('mcp_')) return name.slice(4).replace(/_/g, ' ');
  const words = name.replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

const SOURCES: Record<string, string> = {
  api_server: 'app',
  discord: 'Discord',
  telegram: 'Telegram',
  whatsapp: 'WhatsApp',
  slack: 'Slack',
  weixin: 'WeChat',
  cli: 'terminale',
  tui: 'terminale',
  webui: 'web',
  hermes_browser: 'web',
  desktop: 'desktop',
  dashboard: 'dashboard',
  cron: 'cron',
};

export function sourceLabel(source: string | null | undefined) {
  if (!source) return 'app';
  return SOURCES[source] || source;
}

const DAY = 86400;

export function when(ts: number | null | undefined) {
  if (!ts) return '';
  const secs = Date.now() / 1000 - ts;
  if (secs < 60) return 'ora';
  if (secs < 3600) return `${Math.floor(secs / 60)} min`;
  const d = new Date(ts * 1000);
  const today = new Date();
  if (d.toDateString() === today.toDateString()) {
    return d.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' });
  }
  if (secs < 2 * DAY && new Date(Date.now() - DAY).toDateString() === d.toDateString()) return 'ieri';
  if (secs < 6 * DAY) return d.toLocaleDateString('it-IT', { weekday: 'short' });
  return d.toLocaleDateString('it-IT', { day: 'numeric', month: 'short' });
}
