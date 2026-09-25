const NL = String.fromCharCode(10);
const SEP = NL + NL;

// Parse an SSE body incrementally. EventSource cannot be used because the chat
// call is a POST, so the framing is handled here: events are separated by a
// blank line, and only "data:" lines carry payload.
export async function* sseEvents(res: Response): AsyncGenerator<any> {
  const reader = res.body!.getReader();
  const dec = new TextDecoder();
  let buf = '';
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf(SEP)) >= 0) {
        const block = buf.slice(0, i);
        buf = buf.slice(i + SEP.length);
        for (const line of block.split(NL)) {
          if (!line.startsWith('data:')) continue;
          try { yield JSON.parse(line.slice(5).trim()); } catch { /* comment or partial */ }
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

/** JSON from the hub, or a thrown Error carrying its message. */
export async function api<T = any>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(data.error || `HTTP ${res.status}`), { status: res.status });
  return data as T;
}

export function postJSON(path: string, body: unknown, signal?: AbortSignal) {
  return fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
}
