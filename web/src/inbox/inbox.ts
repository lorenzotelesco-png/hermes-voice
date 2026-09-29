import { api } from '../lib/sse';

export interface Account { id: string; network: string; name: string; status: string; status_text: string }

export interface Status {
  beeper: 'up' | 'down';
  setup: string | null;
  connected: boolean;
  pending: boolean;
  error: string | null;
  expires: number | null;
  accounts: Account[];
  notify: boolean;
  live: boolean;
}

export interface Chat {
  id: string;
  title: string;
  network: string;
  account: string | null;
  group: boolean;
  unread: number;
  mentions: number;
  marked_unread: boolean;
  muted: boolean;
  pinned: boolean;
  archived: boolean;
  readonly: boolean;
  time: number | null;
  avatar: string | null;
  preview: { text: string; mine: boolean; sender: string } | null;
}

export interface Attachment {
  kind: string;
  url: string | null;
  mime: string | null;
  name: string | null;
  bytes: number | null;
  w: number | null;
  h: number | null;
  voice: boolean;
  gif: boolean;
  sticker: boolean;
  duration: number | null;
  poster: string | null;
  transcript: string | null;
}

export interface Message {
  id: string;
  sender: string;
  sender_id: string | null;
  mine: boolean;
  time: number | null;
  sort: string | null;
  type: string;
  text: string;
  edited: boolean;
  deleted: boolean;
  reply_to: string | null;
  status: string | null;
  failed: string | null;
  attachments: Attachment[];
  reactions: { key: string; count: number; mine: boolean }[];
  /** Sent from here, not yet back from Beeper. */
  pending?: boolean;
}

// What Beeper on the server is doing before its chats can be read.
export const SETUP: Record<string, string> = {
  'needs-login': 'Beeper sul server aspetta il tuo accesso.',
  initializing: 'Beeper si sta avviando.',
  'needs-cross-signing-setup': 'Beeper chiede di completare la sicurezza dell\'account.',
  'needs-verification': 'Beeper chiede di verificare questo dispositivo (con la chiave di recupero o un altro dispositivo).',
  'needs-secrets': 'Beeper chiede la chiave di recupero.',
  'needs-first-sync': 'Beeper sta scaricando le chat: qualche minuto.',
  unknown: 'Non capisco in che stato è Beeper.',
};

// An account that needs you, in words; connected and working ones say nothing.
export const ACCOUNT: Record<string, string> = {
  connecting: 'si sta collegando',
  backfilling: 'sta scaricando i messaggi',
  connection_required: 'da collegare',
  reconnect_required: 'da ricollegare',
  attention_required: 'chiede attenzione',
  disconnected: 'scollegato',
  disabled: 'disattivato',
};

// Each network's color and the letters on its badge, as Beeper marks them.
const NETS: [RegExp, string, string][] = [
  [/whatsapp/i, '#7fdc9a', 'W'],
  [/instagram/i, '#f59ac0', 'IG'],
  [/linkedin/i, '#8cb8ff', 'in'],
  [/telegram/i, '#7cc7f2', 'T'],
  [/signal/i, '#9fb4ff', 'S'],
  [/messenger|facebook/i, '#b3a6ff', 'M'],
  [/discord/i, '#b9c0ff', 'D'],
  [/slack/i, '#e7b3ff', 'SL'],
  [/google|sms|rcs/i, '#b6e3a1', 'G'],
  [/beeper|matrix/i, '#c9c6f2', 'B'],
];

export function net(network: string): { color: string; abbr: string } {
  for (const [re, color, abbr] of NETS) if (re.test(network)) return { color, abbr };
  return { color: 'rgba(255, 255, 255, .5)', abbr: (network[0] || '?').toUpperCase() };
}

export function initials(title: string) {
  const words = title.replace(/[^\p{L}\p{N} ]/gu, ' ').trim().split(/\s+/).filter(Boolean);
  if (!words.length) return '?';
  return (words[0][0] + (words.length > 1 ? words[words.length - 1][0] : '')).toUpperCase();
}

export const chatHref = (id: string) => `#/inbox/${encodeURIComponent(id)}`;

export async function post<T = any>(path: string, body: unknown, method = 'POST'): Promise<T> {
  return api<T>(path, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}

/** 409 from the hub: its access to Beeper is missing or was revoked. */
export const needsConnect = (e: any) => e?.status === 409;

// Remembered across tab switches, so coming back paints at once.
export const cache: { status: Status | null; chats: Chat[] | null; older: string | null } = {
  status: null, chats: null, older: null,
};

/** Messages in the order they were sent; a later copy of one replaces it. */
export function merge(list: Message[], incoming: Message[]) {
  const byId = new Map(list.map(m => [m.id, m]));
  for (const m of incoming) byId.set(m.id, m);
  return [...byId.values()].sort((a, b) =>
    (a.time ?? 0) - (b.time ?? 0) || (a.sort ?? '').localeCompare(b.sort ?? '', undefined, { numeric: true }));
}
