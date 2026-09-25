export function bytes(n: number | null | undefined) {
  if (n == null) return '—';
  if (n >= 2 ** 30) return (n / 2 ** 30).toLocaleString('it-IT', { maximumFractionDigits: 1 }) + ' GB';
  return Math.round(n / 2 ** 20) + ' MB';
}

export function duration(s: number | null | undefined) {
  if (s == null) return '';
  if (s < 60) return `${Math.round(s)} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  if (s < 86400) return `${Math.floor(s / 3600)} h`;
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  return h ? `${d} g ${h} h` : `${d} g`;
}

export function money(usd: number | null | undefined) {
  if (usd == null) return '—';
  return usd.toLocaleString('it-IT', {
    style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: usd < 1 ? 3 : 2,
  });
}

export function clock(ts: number | string | null | undefined) {
  if (!ts) return '—';
  const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts);
  if (isNaN(d.getTime())) return String(ts);
  const sameDay = d.toDateString() === new Date().toDateString();
  return sameDay
    ? d.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' })
    : d.toLocaleString('it-IT', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}
