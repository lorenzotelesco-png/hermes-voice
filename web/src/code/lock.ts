import { api, postJSON } from '../lib/sse';

// Face ID in front of the Code tab (server/hub/passkey.py). Safari only lets
// a page ask for a passkey straight from a tap, so the challenge is fetched
// beforehand and the tap goes directly to navigator.credentials.

export interface LockStatus {
  registered: boolean;
  unlocked_until: number | null;
  passkeys: { id: string; label: string; created: number; last_used: number | null }[];
}

const b64u = (buf: ArrayBuffer) =>
  btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const unb64u = (s: string) =>
  Uint8Array.from(atob(s.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - s.length % 4) % 4)), c => c.charCodeAt(0));

export const lockStatus = () => api<LockStatus>('/api/code/lock');

/** Options for the next tap, fetched ahead of it. */
export async function prepare(registered: boolean): Promise<any> {
  const r = await postJSON(registered ? '/api/code/unlock/options' : '/api/code/passkey/options', {});
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
  return { ...d, fetched: Date.now() };
}

export const stale = (opts: any) => !opts || Date.now() - opts.fetched > 4 * 60_000;

async function post(path: string, body: unknown) {
  const r = await postJSON(path, body);
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
  return d;
}

export async function registerPasskey(opts: any, label: string) {
  const cred = await navigator.credentials.create({
    publicKey: {
      ...opts,
      challenge: unb64u(opts.challenge),
      user: { ...opts.user, id: unb64u(opts.user.id) },
      excludeCredentials: (opts.excludeCredentials || []).map((c: any) => ({ ...c, id: unb64u(c.id) })),
    },
  }) as PublicKeyCredential | null;
  if (!cred) throw new Error('annullato');
  const res = cred.response as AuthenticatorAttestationResponse;
  return post('/api/code/passkey', {
    clientDataJSON: b64u(res.clientDataJSON), attestationObject: b64u(res.attestationObject), label,
  });
}

export async function unlock(opts: any) {
  const cred = await navigator.credentials.get({
    publicKey: {
      challenge: unb64u(opts.challenge),
      rpId: opts.rpId,
      userVerification: opts.userVerification,
      timeout: opts.timeout,
      allowCredentials: (opts.allowCredentials || []).map((c: any) => ({ ...c, id: unb64u(c.id) })),
    },
  }) as PublicKeyCredential | null;
  if (!cred) throw new Error('annullato');
  const res = cred.response as AuthenticatorAssertionResponse;
  return post('/api/code/unlock', {
    id: b64u(cred.rawId), clientDataJSON: b64u(res.clientDataJSON),
    authenticatorData: b64u(res.authenticatorData), signature: b64u(res.signature),
  });
}

export const lockNow = () => postJSON('/api/code/lock', {});

/** Anything under /api/code answers 401 once the unlock has run out. */
export class Locked extends Error {}

export async function code<T = any>(path: string, init?: RequestInit): Promise<T> {
  try {
    return await api<T>(path, init);
  } catch (e: any) {
    if (e.status === 401) throw new Locked('bloccato');
    throw e;
  }
}

export async function codePost<T = any>(path: string, body: unknown, method = 'POST'): Promise<T> {
  const r = await fetch(path, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const d = await r.json().catch(() => ({}));
  if (r.status === 401) throw new Locked('bloccato');
  if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
  return d as T;
}
