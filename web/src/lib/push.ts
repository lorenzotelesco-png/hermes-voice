// Push subscription on this device.
//
// iOS only offers push to a web app opened from the home screen (16.4+), and
// only asks for permission inside a tap: enablePush() must be the first thing
// a click handler calls.

import { api, postJSON } from './sse';

export type PushSupport = 'ok' | 'install' | 'unsupported';

function standalone() {
  return matchMedia('(display-mode: standalone)').matches || (navigator as any).standalone === true;
}

export function pushSupport(): PushSupport {
  if ('serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window) return 'ok';
  return /iPhone|iPad/.test(navigator.userAgent) && !standalone() ? 'install' : 'unsupported';
}

export function registerWorker() {
  if (!('serviceWorker' in navigator)) return;
  navigator.serviceWorker.register('/sw.js').catch(e => console.warn('service worker:', e));
}

function keyBytes(b64url: string) {
  const b64 = (b64url + '='.repeat((4 - (b64url.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/');
  return Uint8Array.from(atob(b64), c => c.charCodeAt(0));
}

export async function currentSubscription() {
  if (pushSupport() !== 'ok') return null;
  const reg = await navigator.serviceWorker.ready;
  return reg.pushManager.getSubscription();
}

export async function enablePush() {
  const permission = await Notification.requestPermission();
  if (permission !== 'granted') {
    throw new Error('permesso negato: si riattiva da Impostazioni › Notifiche › Hermes');
  }
  const { key } = await api<{ key: string }>('/api/push/key');
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(key) });
  const res = await postJSON('/api/push/subscribe', { subscription: sub.toJSON() });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || `HTTP ${res.status}`);
}

export async function disablePush() {
  const sub = await currentSubscription();
  if (!sub) return;
  await postJSON('/api/push/unsubscribe', { endpoint: sub.endpoint });
  await sub.unsubscribe();
}
