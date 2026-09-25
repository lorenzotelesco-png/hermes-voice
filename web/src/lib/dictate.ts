import { getAudioMime, mimeToExt } from '../voice/engine';

/** Tap to start, tap to stop, text back: a short dictation through the
 *  hub's transcription relay, for places that take a line of text. */
export class Dictation {
  private recorder: MediaRecorder | null = null;
  private chunks: Blob[] = [];
  private stream: MediaStream | null = null;

  get recording() { return !!this.recorder; }

  async start() {
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    this.chunks = [];
    this.recorder = new MediaRecorder(this.stream, { mimeType: getAudioMime() });
    this.recorder.ondataavailable = e => { if (e.data.size > 0) this.chunks.push(e.data); };
    this.recorder.start(200);
  }

  async stop(): Promise<string> {
    const recorder = this.recorder;
    if (!recorder) return '';
    const done = new Promise<void>(resolve => { recorder.onstop = () => resolve(); });
    if (recorder.state !== 'inactive') recorder.stop();
    await done;
    this.release();
    const mime = recorder.mimeType || 'audio/webm';
    const fd = new FormData();
    fd.append('audio', new Blob(this.chunks, { type: mime }), 'nota' + mimeToExt(mime));
    const res = await fetch('/api/transcribe', { method: 'POST', body: fd });
    const d = await res.json().catch(() => ({}));
    if (!res.ok || d.error) throw new Error(d.error || `HTTP ${res.status}`);
    return (d.text || '').trim();
  }

  cancel() {
    if (this.recorder && this.recorder.state !== 'inactive') this.recorder.stop();
    this.release();
  }

  private release() {
    this.stream?.getTracks().forEach(t => t.stop());
    this.stream = null;
    this.recorder = null;
  }
}
