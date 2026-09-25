// Photos go into the vault's git history for good: a 12 MP shot is cut down
// to what a note needs before it leaves the phone.
const MAX_SIDE = 2048;
const QUALITY = 0.85;
const SMALL = 1.2 * 2 ** 20;

function stamp() {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}-${p(d.getMinutes())}-${p(d.getSeconds())}`;
}

/** A name worth keeping: the camera calls every shot "image.jpg". */
export function uploadName(file: File, ext?: string) {
  const dot = file.name.lastIndexOf('.');
  const stem = dot > 0 ? file.name.slice(0, dot) : file.name;
  const extension = ext || (dot > 0 ? file.name.slice(dot) : '');
  return (/^(image|photo|img)$/i.test(stem) || !stem ? `Foto ${stamp()}` : stem) + extension.toLowerCase();
}

export async function shrink(file: File): Promise<{ blob: Blob; name: string }> {
  const keep = { blob: file as Blob, name: uploadName(file) };
  if (!/^image\/(jpeg|png|webp|heic|heif)$/.test(file.type) || file.size < SMALL) return keep;
  try {
    const bmp = await createImageBitmap(file);
    const scale = Math.min(1, MAX_SIDE / Math.max(bmp.width, bmp.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(bmp.width * scale);
    canvas.height = Math.round(bmp.height * scale);
    canvas.getContext('2d')!.drawImage(bmp, 0, 0, canvas.width, canvas.height);
    bmp.close();
    const blob = await new Promise<Blob | null>(r => canvas.toBlob(r, 'image/jpeg', QUALITY));
    if (!blob || blob.size >= file.size) return keep;
    return { blob, name: uploadName(file, '.jpg') };
  } catch {
    return keep;   // an image this browser cannot decode goes up as it is
  }
}

export async function uploadToVault(file: File): Promise<{ path: string; name: string; pushed: boolean; push_error: string | null; copies: string[] }> {
  const { blob, name } = await shrink(file);
  const fd = new FormData();
  fd.append('file', blob, name);
  fd.append('name', name);
  const res = await fetch('/api/vault/upload', { method: 'POST', body: fd });
  const d = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(d.error || `HTTP ${res.status}`);
  return d;
}
