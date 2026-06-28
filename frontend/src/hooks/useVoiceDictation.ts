import { useCallback, useRef, useState } from 'react';
import { apiUrl, getToken } from '../api';

// Near-realtime dictation that works EVERYWHERE (Linux/Chromium, mobile browsers, the Capacitor
// WebView) — pure REST, no Web Speech, no WebSocket, no EC2.
//
// While you talk we keep the mic open and re-transcribe the clip-so-far via Sarvam STT every
// ~1.2s, updating the composer live. On stop we do one final pass to catch the tail. So text
// appears as you speak (≈1.2s granularity) instead of a 2-4s wait after you finish. No auto-send.

const SAMPLE_RATE = 16000;
const POLL_MS = 600;           // how often we re-transcribe the growing clip (self-throttles via
                               // inFlightRef — if a call is slower than this, polls just skip)
const MIN_PARTIAL_S = 0.3;     // don't bother transcribing less than this
const MIN_UTTERANCE_S = 0.3;   // ignore an accidental tap

/** Pack captured Float32 frames into a 16 kHz mono PCM-16 WAV blob. */
function encodeWav(chunks: Float32Array[]): Blob {
  let len = 0;
  for (const c of chunks) len += c.length;
  const pcm = new Int16Array(len);
  let o = 0;
  for (const c of chunks) {
    for (let i = 0; i < c.length; i++) {
      const s = Math.max(-1, Math.min(1, c[i]));
      pcm[o++] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
  }
  const buf = new ArrayBuffer(44 + pcm.length * 2);
  const dv = new DataView(buf);
  const str = (off: number, s: string) => { for (let i = 0; i < s.length; i++) dv.setUint8(off + i, s.charCodeAt(i)); };
  str(0, 'RIFF'); dv.setUint32(4, 36 + pcm.length * 2, true); str(8, 'WAVE'); str(12, 'fmt ');
  dv.setUint32(16, 16, true); dv.setUint16(20, 1, true); dv.setUint16(22, 1, true);
  dv.setUint32(24, SAMPLE_RATE, true); dv.setUint32(28, SAMPLE_RATE * 2, true);
  dv.setUint16(32, 2, true); dv.setUint16(34, 16, true); str(36, 'data'); dv.setUint32(40, pcm.length * 2, true);
  new Int16Array(buf, 44).set(pcm);
  return new Blob([buf], { type: 'audio/wav' });
}

async function transcribe(chunks: Float32Array[]): Promise<string> {
  const fd = new FormData();
  fd.append('file', encodeWav(chunks), 'utterance.wav');
  const token = getToken();
  const resp = await fetch(apiUrl('/api/voice/stt'), {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: fd,
  });
  if (!resp.ok) throw new Error(`stt failed: ${resp.status}`);
  return ((await resp.json())?.text || '').trim();
}

export type DictationState = 'idle' | 'recording' | 'transcribing';

export interface UseVoiceDictationOptions {
  // Fired when recording begins — capture the current composer text as the base to append to.
  onStart?: () => void;
  // Live transcript so far (full text since start). Caller renders base + this.
  onUpdate: (text: string) => void;
  onError?: (message: string) => void;
}

export function useVoiceDictation({ onStart, onUpdate, onError }: UseVoiceDictationOptions) {
  const [state, setState] = useState<DictationState>('idle');

  const micStreamRef = useRef<MediaStream | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const procRef = useRef<ScriptProcessorNode | null>(null);
  const chunksRef = useRef<Float32Array[]>([]);
  const timerRef = useRef<number | null>(null);
  const inFlightRef = useRef(false);   // a transcribe call is outstanding
  const lastLenRef = useRef(0);        // chunk count at last transcribe (skip if unchanged)
  const stoppedRef = useRef(false);    // stop began — late polls must not clobber the final text

  const teardown = useCallback(() => {
    if (timerRef.current !== null) { clearInterval(timerRef.current); timerRef.current = null; }
    procRef.current?.disconnect();
    procRef.current = null;
    ctxRef.current?.close().catch(() => {});
    ctxRef.current = null;
    micStreamRef.current?.getTracks().forEach((t) => t.stop());
    micStreamRef.current = null;
  }, []);

  // Re-transcribe the clip-so-far and push the live text. Serialized via inFlightRef so calls
  // never pile up; late results are dropped once stop() has begun.
  const poll = useCallback(async () => {
    if (inFlightRef.current || stoppedRef.current) return;
    const chunks = chunksRef.current.slice();
    if (chunks.length <= lastLenRef.current) return;  // no new audio since last time
    lastLenRef.current = chunks.length;
    const secs = chunks.reduce((n, c) => n + c.length, 0) / SAMPLE_RATE;
    if (secs < MIN_PARTIAL_S) return;
    inFlightRef.current = true;
    try {
      const text = await transcribe(chunks);
      if (text && !stoppedRef.current) onUpdate(text);
    } catch { /* a dropped partial is fine — the next poll / final pass recovers */ }
    finally { inFlightRef.current = false; }
  }, [onUpdate]);

  const start = useCallback(async () => {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { sampleRate: SAMPLE_RATE, channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    micStreamRef.current = stream;
    const ctx = new AudioContext({ sampleRate: SAMPLE_RATE });
    ctxRef.current = ctx;
    if (ctx.state === 'suspended') await ctx.resume();
    const src = ctx.createMediaStreamSource(stream);
    const proc = ctx.createScriptProcessor(4096, 1, 1);
    procRef.current = proc;
    chunksRef.current = [];
    lastLenRef.current = 0;
    inFlightRef.current = false;
    stoppedRef.current = false;
    proc.onaudioprocess = (e) => { chunksRef.current.push(new Float32Array(e.inputBuffer.getChannelData(0))); };
    src.connect(proc);
    const g = ctx.createGain();
    g.gain.value = 0;  // ScriptProcessor needs a sink to fire; keep it silent
    proc.connect(g);
    g.connect(ctx.destination);
    onStart?.();
    timerRef.current = window.setInterval(() => void poll(), POLL_MS);
    setState('recording');
  }, [onStart, poll]);

  const stop = useCallback(async () => {
    stoppedRef.current = true;
    const chunks = chunksRef.current.slice();
    chunksRef.current = [];
    teardown();
    const secs = chunks.reduce((n, c) => n + c.length, 0) / SAMPLE_RATE;
    if (secs < MIN_UTTERANCE_S) { setState('idle'); return; }
    setState('transcribing');  // brief — usually most text is already on screen from the polls
    try {
      const text = await transcribe(chunks);
      if (text) onUpdate(text);
    } catch (e) {
      onError?.(e instanceof Error ? e.message : 'Could not transcribe.');
    } finally {
      setState('idle');
    }
  }, [teardown, onUpdate, onError]);

  const toggle = useCallback(async () => {
    if (state === 'idle') {
      try { await start(); }
      catch { onError?.('Microphone access denied.'); teardown(); setState('idle'); }
    } else if (state === 'recording') {
      await stop();
    }
    // 'transcribing' → ignore taps until it resolves
  }, [state, start, stop, teardown, onError]);

  return { state, toggle };
}
