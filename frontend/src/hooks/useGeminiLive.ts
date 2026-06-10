import { useRef, useState, useCallback } from 'react';
import { api } from '../api';

// Drop-in replacement for useOpenAIRealtime — identical option/return shape so
// VoiceInput can switch providers without other changes. Instead of WebRTC,
// this connects directly to the Gemini Live API over a WebSocket using a
// short-lived ephemeral token minted by /api/voice/gemini-token/. Audio is
// handled manually: mic → PCM16 @16kHz upstream, model PCM @24kHz downstream.

export interface UseGeminiLiveOptions {
  onUserTranscript?: (text: string) => void;
  onAssistantTranscript?: (text: string) => void;
  // Live stage signals — phase transitions (listening / thinking / speaking)
  // and the accumulated caption text for the in-flight turn, for the
  // VoiceStage bar. Distinct from the per-delta transcript callbacks above.
  onPhase?: (phase: 'listening' | 'thinking' | 'speaking') => void;
  onCaption?: (role: 'user' | 'assistant', fullText: string) => void;
  onToolCall?: (name: string, args: Record<string, string>, callId: string) => void;
  // Fires once per completed turn with that turn's full transcript and whether
  // it invoked a tool. Used to render each turn live and to persist/leadgen
  // conversational (non-tool) turns.
  onTurnComplete?: (userText: string, assistantText: string, hadToolCall: boolean) => void;
  onError?: (message: string) => void;
  onStateChange?: (state: 'disconnected' | 'connecting' | 'connected') => void;
}

export interface UseGeminiLiveReturn {
  state: 'disconnected' | 'connecting' | 'connected';
  connect: () => Promise<void>;
  disconnect: () => void;
  isSpeaking: boolean;
  respondToToolCall: (callId: string, output: Record<string, unknown>) => void;
}

const WS_HOST =
  'wss://generativelanguage.googleapis.com/ws/' +
  'google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContentConstrained';

// NOTE: with the constrained endpoint, the session config (tools, system
// instruction, transcription, response modalities) is LOCKED INTO THE
// EPHEMERAL TOKEN server-side (see voice_gemini_token_endpoint). Anything we
// send here in `setup` beyond the model is ignored by the server — so the
// browser sends a minimal setup and relies on the token's baked-in config.

function floatTo16BitPCMBase64(input: Float32Array): string {
  const i16 = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]));
    i16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  const bytes = new Uint8Array(i16.buffer);
  let bin = '';
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin);
}

export function useGeminiLive(options: UseGeminiLiveOptions): UseGeminiLiveReturn {
  const { onUserTranscript, onAssistantTranscript, onPhase, onCaption, onToolCall, onTurnComplete, onError, onStateChange } = options;
  const [state, setState] = useState<'disconnected' | 'connecting' | 'connected'>('disconnected');
  const [isSpeaking, setIsSpeaking] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const micStreamRef = useRef<MediaStream | null>(null);
  const inCtxRef = useRef<AudioContext | null>(null);
  const procRef = useRef<ScriptProcessorNode | null>(null);
  const outCtxRef = useRef<AudioContext | null>(null);
  const playHeadRef = useRef(0);
  const speakingRef = useRef(false);
  const toolNamesRef = useRef<Record<string, string>>({});
  const turnUserRef = useRef('');
  const turnAsstRef = useRef('');
  const turnHadToolRef = useRef(false);
  // True between receiving a toolCall and the turn completing. While a tool
  // call is in flight (backend run_agent_turn can take 10-30s), we must NOT
  // keep streaming mic audio — stray speech/noise triggers VAD, starts a new
  // turn, and cancels/cuts off the pending tool reply.
  const toolPendingRef = useRef(false);

  const disconnect = useCallback(() => {
    micStreamRef.current?.getTracks().forEach((t) => t.stop());
    micStreamRef.current = null;
    procRef.current?.disconnect();
    procRef.current = null;
    inCtxRef.current?.close().catch(() => {});
    inCtxRef.current = null;
    outCtxRef.current?.close().catch(() => {});
    outCtxRef.current = null;
    playHeadRef.current = 0;
    speakingRef.current = false;
    toolPendingRef.current = false;
    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.close();
      wsRef.current = null;
    }
    setState('disconnected');
    setIsSpeaking(false);
    onStateChange?.('disconnected');
  }, [onStateChange]);

  const playAudioChunk = useCallback((b64: string) => {
    let ctx = outCtxRef.current;
    if (!ctx) {
      ctx = new AudioContext({ sampleRate: 24000 });
      outCtxRef.current = ctx;
      playHeadRef.current = ctx.currentTime;
    }
    if (ctx.state === 'suspended') ctx.resume().catch(() => {});
    const raw = atob(b64);
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    const i16 = new Int16Array(bytes.buffer);
    const f32 = new Float32Array(i16.length);
    for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 32768;
    const buf = ctx.createBuffer(1, f32.length, 24000);
    buf.getChannelData(0).set(f32);
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(ctx.destination);
    const at = Math.max(ctx.currentTime, playHeadRef.current);
    src.start(at);
    playHeadRef.current = at + f32.length / 24000;
  }, []);

  const startMic = useCallback(async () => {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { sampleRate: 16000, channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    micStreamRef.current = stream;
    const ctx = new AudioContext({ sampleRate: 16000 });
    inCtxRef.current = ctx;
    if (ctx.state === 'suspended') await ctx.resume();
    const src = ctx.createMediaStreamSource(stream);
    const proc = ctx.createScriptProcessor(4096, 1, 1);
    procRef.current = proc;
    proc.onaudioprocess = (e) => {
      const ws = wsRef.current;
      // Gate while the model is speaking (echo) OR while a tool call is in
      // flight (stray audio would cancel/cut off the pending tool reply).
      if (!ws || ws.readyState !== WebSocket.OPEN || speakingRef.current || toolPendingRef.current) return;
      const data = floatTo16BitPCMBase64(e.inputBuffer.getChannelData(0));
      ws.send(JSON.stringify({ realtimeInput: { audio: { mimeType: 'audio/pcm;rate=16000', data } } }));
    };
    src.connect(proc);
    // ScriptProcessor needs a sink to fire; route through a muted gain node.
    const g = ctx.createGain();
    g.gain.value = 0;
    proc.connect(g);
    g.connect(ctx.destination);
  }, []);

  const handleMessage = useCallback(
    (obj: any) => {
      if (obj.setupComplete !== undefined) {
        setState('connected');
        onStateChange?.('connected');
        onPhase?.('listening');
        void startMic();
        // Nudge a greeting; text turns don't produce inputTranscription, so this
        // won't pollute the captured user transcript.
        wsRef.current?.send(
          JSON.stringify({
            clientContent: {
              turns: [{ role: 'user', parts: [{ text: 'Greet me warmly in one short Hindi line.' }] }],
              turnComplete: true,
            },
          }),
        );
        return;
      }

      if (obj.toolCall) {
        const calls = obj.toolCall.functionCalls || [];
        turnHadToolRef.current = true;
        toolPendingRef.current = true; // mute mic until this turn completes
        onPhase?.('thinking');
        for (const fc of calls) {
          if (fc?.id) toolNamesRef.current[fc.id] = fc.name;
          onToolCall?.(fc.name, (fc.args || {}) as Record<string, string>, fc.id);
        }
        // companion_execute is NON_BLOCKING, but in practice the model calls it
        // and goes silent for the 10-15s the backend needs — dead air. Nudge a
        // spoken one-liner so it talks WHILE the tool runs; the toolResponse
        // arrives later with scheduling=INTERRUPT and cuts straight to results.
        wsRef.current?.send(
          JSON.stringify({
            clientContent: {
              turns: [
                {
                  role: 'user',
                  parts: [
                    {
                      text:
                        '(system: the tool is still running. Speak ONE short, natural Hinglish '
                        + 'status line right now — e.g. "Ek second, dekh raha hoon…" — tailored to '
                        + 'what the user asked. Do NOT call any tool. Do NOT invent results.)',
                    },
                  ],
                },
              ],
              turnComplete: true,
            },
          }),
        );
        return;
      }

      const sc = obj.serverContent;
      if (!sc) return;
      if (sc.inputTranscription?.text) {
        turnUserRef.current += sc.inputTranscription.text;
        onUserTranscript?.(sc.inputTranscription.text);
        onCaption?.('user', turnUserRef.current);
      }
      if (sc.outputTranscription?.text) {
        turnAsstRef.current += sc.outputTranscription.text;
        onAssistantTranscript?.(sc.outputTranscription.text);
        onCaption?.('assistant', turnAsstRef.current);
      }
      const parts = sc.modelTurn?.parts || [];
      for (const p of parts) {
        if (p.inlineData?.mimeType?.startsWith('audio/pcm')) {
          if (!speakingRef.current) {
            speakingRef.current = true;
            setIsSpeaking(true);
            onPhase?.('speaking');
          }
          playAudioChunk(p.inlineData.data);
        }
      }
      if (sc.interrupted) {
        speakingRef.current = false;
        setIsSpeaking(false);
      }
      if (sc.turnComplete) {
        speakingRef.current = false;
        toolPendingRef.current = false; // re-enable mic for the next user turn
        setIsSpeaking(false);
        onPhase?.('listening');
        onCaption?.('user', '');
        onCaption?.('assistant', '');
        const u = turnUserRef.current.trim();
        const a = turnAsstRef.current.trim();
        const hadTool = turnHadToolRef.current;
        turnUserRef.current = '';
        turnAsstRef.current = '';
        turnHadToolRef.current = false;
        if (u || a) onTurnComplete?.(u, a, hadTool);
      }
    },
    [onStateChange, onToolCall, onUserTranscript, onAssistantTranscript, onPhase, onCaption, onTurnComplete, startMic, playAudioChunk],
  );

  const connect = useCallback(async () => {
    setState('connecting');
    onStateChange?.('connecting');
    try {
      const tokenData = await api.post('/api/voice/gemini-token/');
      const token = tokenData.token;
      if (!token) throw new Error(tokenData.error || 'Failed to get Gemini token');
      const model = tokenData.model || 'gemini-3.1-flash-live-preview';

      const ws = new WebSocket(`${WS_HOST}?access_token=${encodeURIComponent(token)}`);
      ws.binaryType = 'arraybuffer';
      wsRef.current = ws;

      ws.onopen = () => {
        // Minimal setup — tools/system/transcription are baked into the token.
        ws.send(JSON.stringify({ setup: { model: `models/${model}` } }));
      };

      ws.onmessage = async (e) => {
        let text: string;
        if (typeof e.data === 'string') text = e.data;
        else if (e.data instanceof Blob) text = await e.data.text();
        else text = new TextDecoder().decode(e.data);
        try {
          handleMessage(JSON.parse(text));
        } catch (err) {
          console.debug('[gemini-live] non-JSON / parse skip', err);
        }
      };

      ws.onerror = () => {
        // onerror fires before onclose; let onclose surface the code/reason.
        console.error('[gemini-live] ws error');
      };
      ws.onclose = (ev) => {
        console.warn('[gemini-live] ws closed', ev.code, ev.reason);
        if (ev.code !== 1000 && state !== 'disconnected') {
          onError?.(`Voice socket closed [${ev.code}]${ev.reason ? ': ' + ev.reason : ''}`);
        }
        disconnect();
      };
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Connection failed';
      onError?.(msg);
      disconnect();
      throw err;
    }
  }, [onStateChange, onError, handleMessage, disconnect, state]);

  const respondToToolCall = useCallback((callId: string, output: Record<string, unknown>) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(
      JSON.stringify({
        toolResponse: {
          functionResponses: [
            {
              id: callId,
              name: toolNamesRef.current[callId] || 'companion_execute',
              response: output,
              // companion_execute is declared NON_BLOCKING (so the model can
              // speak a status line while it runs); INTERRUPT makes the model
              // promptly speak the real result once it's ready.
              scheduling: 'INTERRUPT',
            },
          ],
        },
      }),
    );
    delete toolNamesRef.current[callId];
  }, []);

  return { state, connect, disconnect, isSpeaking, respondToToolCall };
}
