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
  onToolCall?: (name: string, args: Record<string, string>, callId: string) => void;
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

const SYSTEM_INSTRUCTION =
  'You are Companion, a warm, funny Hindi voice assistant for users in India. ' +
  'Always speak in Hindi (Hinglish is fine). Keep replies short and natural, like a dost. ' +
  'When the user wants to do something actionable — order food, book a cab, find events, ' +
  'manage reminders, or anything needing live data — CALL the companion_execute function ' +
  'with a complete natural-language intent. Do not answer such requests from your own knowledge.';

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
  const { onUserTranscript, onAssistantTranscript, onToolCall, onError, onStateChange } = options;
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
      // Gate while the model is speaking to avoid echo feeding back as input.
      if (!ws || ws.readyState !== WebSocket.OPEN || speakingRef.current) return;
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
        for (const fc of calls) {
          if (fc?.id) toolNamesRef.current[fc.id] = fc.name;
          onToolCall?.(fc.name, (fc.args || {}) as Record<string, string>, fc.id);
        }
        return;
      }

      const sc = obj.serverContent;
      if (!sc) return;
      if (sc.inputTranscription?.text) onUserTranscript?.(sc.inputTranscription.text);
      if (sc.outputTranscription?.text) onAssistantTranscript?.(sc.outputTranscription.text);
      const parts = sc.modelTurn?.parts || [];
      for (const p of parts) {
        if (p.inlineData?.mimeType?.startsWith('audio/pcm')) {
          if (!speakingRef.current) {
            speakingRef.current = true;
            setIsSpeaking(true);
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
        setIsSpeaking(false);
      }
    },
    [onStateChange, onToolCall, onUserTranscript, onAssistantTranscript, startMic, playAudioChunk],
  );

  const connect = useCallback(async () => {
    setState('connecting');
    onStateChange?.('connecting');
    try {
      const tokenData = await api.post('/api/voice/gemini-token/');
      const token = tokenData.token;
      if (!token) throw new Error(tokenData.error || 'Failed to get Gemini token');
      const model = tokenData.model || 'gemini-2.5-flash-native-audio-latest';

      const ws = new WebSocket(`${WS_HOST}?access_token=${encodeURIComponent(token)}`);
      ws.binaryType = 'arraybuffer';
      wsRef.current = ws;

      ws.onopen = () => {
        ws.send(
          JSON.stringify({
            setup: {
              model: `models/${model}`,
              generationConfig: { responseModalities: ['AUDIO'] },
              systemInstruction: { parts: [{ text: SYSTEM_INSTRUCTION }] },
              tools: [
                {
                  functionDeclarations: [
                    {
                      name: 'companion_execute',
                      description:
                        'Execute actions for the user: order food, book cabs, find events, manage reminders, answer questions.',
                      parameters: {
                        type: 'OBJECT',
                        properties: {
                          intent: { type: 'STRING', description: 'Full natural-language intent with all details.' },
                        },
                        required: ['intent'],
                      },
                    },
                  ],
                },
              ],
              inputAudioTranscription: {},
              outputAudioTranscription: {},
              realtimeInputConfig: {
                automaticActivityDetection: { disabled: false, silenceDurationMs: 600, prefixPaddingMs: 50 },
              },
            },
          }),
        );
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
            { id: callId, name: toolNamesRef.current[callId] || 'companion_execute', response: output },
          ],
        },
      }),
    );
    delete toolNamesRef.current[callId];
  }, []);

  return { state, connect, disconnect, isSpeaking, respondToToolCall };
}
