const API = '';
let audioCtx, analyser, stream, recorder, chunks = [];
let isSpeaking = false, isProcessing = false, isActive = false, isMuted = false;
let silenceTimer = null, rafId = null;
let history = [], currentAudio = null, sessionId = null;
let volume = 0;
let noiseFloor = 5;      // calibrated dynamically
let calibrating = true;
let calibSamples = [];

const blobs   = [0,1,2,3].map(i => document.getElementById('b'+i));
const label   = document.getElementById('label');
const errDiv  = document.getElementById('error');

// ── BLOB ANIMATION ────────────────────────────────────────────────
const phases = [0, 0.7, 1.4, 2.1];
let animTime = 0, blobState = 'idle';

function animBlobs(ts) {
  if (!isActive) return;
  animTime = ts / 1000;
  blobs.forEach((b, i) => {
    let sy = 1, sx = 1, op = 1;
    const p = phases[i];
    if (blobState === 'idle') {
      sy = 1 + 0.06 * Math.sin(animTime * 1.2 + p);
      sx = 1 - 0.03 * Math.sin(animTime * 1.2 + p);
      op = 0.85 + 0.1 * Math.sin(animTime + p);
    } else if (blobState === 'listening') {
      const boost = volume * (1 + 0.4 * Math.sin(animTime * 8 + p));
      sy = 1 + 0.12 * Math.sin(animTime * 3.5 + p) + boost * 0.7;
      sx = 1 / (0.95 + sy * 0.05);
      op = 0.7 + 0.3 * Math.min(1, volume * 3 + 0.2);
    } else if (blobState === 'speaking') {
      const wave = Math.sin(animTime * 9 + p * 1.5);
      sy = 1.1 + 0.45 * wave + 0.1 * Math.sin(animTime * 5 + p);
      sx = 1 / (0.92 + sy * 0.08);
      op = 1;
    } else if (blobState === 'thinking') {
      const t = (animTime * 0.6 + i * 0.18) % 1;
      op = 0.2 + 0.75 * Math.pow(Math.sin(t * Math.PI), 2);
      sy = 0.75 + 0.3 * Math.pow(Math.sin(t * Math.PI), 2);
    }
    b.style.transform = `scaleY(${sy.toFixed(3)}) scaleX(${sx.toFixed(3)})`;
    b.style.opacity   = op.toFixed(3);
  });
  rafId = requestAnimationFrame(animBlobs);
}

function setState(s, text) {
  blobState = s;
  document.body.className = s;
  label.textContent = text;
}

function showError(msg, ms = 6000) {
  errDiv.textContent = msg;
  errDiv.style.display = 'block';
  setTimeout(() => errDiv.style.display = 'none', ms);
}

// ── SESSION ───────────────────────────────────────────────────────
async function startSession() {
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
  } catch(e) {
    showError('Microfono non disponibile: ' + e.message, 8000); return;
  }
  document.getElementById('start-screen').style.display = 'none';
  document.getElementById('main-screen').style.display  = 'flex';
  isActive = true;

  // No custom sampleRate — let Safari use its native rate
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  if (audioCtx.state === 'suspended') await audioCtx.resume();
  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 1024;
  analyser.smoothingTimeConstant = 0.5;
  audioCtx.createMediaStreamSource(stream).connect(analyser);

  calibrating = true; calibSamples = [];
  setState('idle', 'calibrazione...');
  requestAnimationFrame(animBlobs);

  // Calibrate noise floor for 1.5 seconds
  setTimeout(() => {
    noiseFloor = calibSamples.length
      ? calibSamples.reduce((a,b) => a+b, 0) / calibSamples.length
      : 5;
    noiseFloor = Math.max(3, noiseFloor);
    calibrating = false;
    setState('listening', 'in ascolto');
    monitorLoop();
  }, 1500);

  monitorCalib();
}

function monitorCalib() {
  if (!calibrating) return;
  calibSamples.push(getRMS());
  setTimeout(monitorCalib, 80);
}

function stopSession() {
  isActive = false;
  cancelAnimationFrame(rafId);
  clearTimeout(silenceTimer);
  if (recorder && recorder.state !== 'inactive') recorder.stop();
  if (stream) stream.getTracks().forEach(t => t.stop());
  if (audioCtx) audioCtx.close();
  if (currentAudio) { currentAudio.pause(); currentAudio = null; }
  history = []; sessionId = null;
  blobs.forEach(b => { b.style.transform = ''; b.style.opacity = ''; });
  document.getElementById('main-screen').style.display  = 'none';
  document.getElementById('start-screen').style.display = 'flex';
}

function toggleMute() {
  isMuted = !isMuted;
  stream.getAudioTracks().forEach(t => t.enabled = !isMuted);
  document.getElementById('btn-mute').style.opacity = isMuted ? '0.35' : '1';
}

// ── VAD ───────────────────────────────────────────────────────────
const SILENCE_MS = 1500;

function getRMS() {
  const d = new Uint8Array(analyser.frequencyBinCount);
  analyser.getByteTimeDomainData(d);
  let sum = 0;
  for (let i = 0; i < d.length; i++) { const v = (d[i] - 128) / 128; sum += v * v; }
  return Math.sqrt(sum / d.length) * 100;
}

function monitorLoop() {
  if (!isActive || calibrating) return;
  const rms = getRMS();
  const threshold = noiseFloor * 2.8;  // adaptive: 2.8x background noise
  volume = volume * 0.75 + (rms / (noiseFloor * 8)) * 0.25;

  if (!isMuted && !isProcessing) {
    if (rms > threshold) {
      if (!isSpeaking) {
        isSpeaking = true; chunks = [];
        recorder = new MediaRecorder(stream, { mimeType: getAudioMime() });
        recorder.ondataavailable = e => { if (e.data.size > 0) chunks.push(e.data); };
        recorder.start(100);
      }
      clearTimeout(silenceTimer);
      silenceTimer = setTimeout(endSpeech, SILENCE_MS);
    }
  }
  setTimeout(monitorLoop, 40);
}

function getAudioMime() {
  const types = ['audio/mp4', 'audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', ''];
  return types.find(t => !t || MediaRecorder.isTypeSupported(t)) || '';
}

function endSpeech() {
  if (!isSpeaking || isProcessing) return;
  isSpeaking = false; isProcessing = true;
  setState('thinking', 'elaboro...');
  recorder.onstop = async () => {
    const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
    await handleAudio(blob);
    isProcessing = false;
    if (isActive) { volume = 0; setState('listening', 'in ascolto'); }
  };
  if (recorder.state !== 'inactive') recorder.stop();
}

// ── PIPELINE ──────────────────────────────────────────────────────
function mimeToExt(mime) {
  if (!mime) return '.webm';
  if (mime.includes('mp4'))  return '.m4a';
  if (mime.includes('ogg'))  return '.ogg';
  if (mime.includes('mpeg')) return '.mp3';
  return '.webm';
}

async function handleAudio(blob) {
  try {
    const fd  = new FormData();
    const ext = mimeToExt(recorder.mimeType);
    fd.append('audio', blob, 'speech' + ext);

    const sttRes  = await fetch(API + '/transcribe', { method: 'POST', body: fd });
    const sttData = await sttRes.json();
    if (sttData.error) { showError('STT: ' + sttData.error); return; }
    const text = (sttData.text || '').trim();
    if (text.length < 2) return;   // silent or noise

    history.push({ role: 'user', content: text });

    const chatRes  = await fetch(API + '/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ history: history.slice(-12), session_id: sessionId })
    });
    const chatData = await chatRes.json();
    if (chatData.error) { showError('Chat: ' + chatData.error); return; }
    if (chatData.session_id) sessionId = chatData.session_id;
    const reply = chatData.reply;
    if (!reply) return;
    history.push({ role: 'assistant', content: reply });

    await playTTS(reply);
  } catch(e) {
    showError('Errore: ' + e.message);
    console.error(e);
  }
}

async function playTTS(text) {
  setState('speaking', 'hermes');
  try {
    const ttsRes  = await fetch(API + '/tts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text })
    });
    const ttsData = await ttsRes.json();
    if (ttsData.error) { showError(ttsData.error); return; }

    // Decode WAV via AudioContext (already unlocked by user gesture — works on iOS)
    const bytes  = Uint8Array.from(atob(ttsData.audio), c => c.charCodeAt(0)).buffer;
    const decoded = await audioCtx.decodeAudioData(bytes);

    await new Promise(res => {
      const src = audioCtx.createBufferSource();
      src.buffer = decoded;
      src.connect(audioCtx.destination);
      src.onended = res;
      currentAudio = src;

      // Tap to interrupt
      const onTap = () => { try { src.stop(); } catch(_) {} };
      document.getElementById('main-screen').addEventListener('click', onTap, { once: true });
      src.onended = () => {
        document.getElementById('main-screen').removeEventListener('click', onTap);
        currentAudio = null;
        res();
      };

      src.start(0);
    });
  } catch(e) {
    showError('TTS: ' + e.message);
    console.error(e);
  }
}
