import { useRef, useCallback, useState } from 'react';

export type GeminiConnectionState =
  | 'disconnected'
  | 'connecting'
  | 'settingUp'
  | 'ready'
  | 'error';

export interface GeminiToolCall {
  functionCalls: Array<{
    name: string;
    args: Record<string, string>;
    id: string;
  }>;
}

export interface CreateUseGeminiLiveOptions {
  apiKey: string;
  systemInstruction: string;
  onAudioReceived?: (data: ArrayBuffer) => void;
  onInputTranscript?: (text: string) => void;
  onOutputTranscript?: (text: string) => void;
  onTurnComplete?: () => void;
  onInterrupted?: () => void;
  onError?: (message: string) => void;
  onToolCall?: (call: GeminiToolCall) => void;
  onToolCallCancellation?: (ids: string[]) => void;
}

interface ServerContent {
  interrupted?: boolean;
  turnComplete?: boolean;
  modelTurn?: {
    parts: Array<{
      inlineData?: {
        mimeType: string;
        data: string;
      };
      text?: string;
    }>;
  };
  inputTranscription?: {
    text: string;
  };
  outputTranscription?: {
    text: string;
  };
}

interface SetupCompleteMessage {
  setupComplete: Record<string, never>;
}

interface GoAwayMessage {
  goAway: {
    reason: string;
  };
}

interface ToolCallMessage {
  toolCall: GeminiToolCall;
}

interface ToolCallCancellationMessage {
  toolCallCancellation: {
    ids: string[];
  };
}

type IncomingMessage =
  | SetupCompleteMessage
  | GoAwayMessage
  | ToolCallMessage
  | ToolCallCancellationMessage
  | { serverContent?: ServerContent };

export function createUseGeminiLive(options: CreateUseGeminiLiveOptions) {
  const {
    apiKey,
    onAudioReceived,
    onInputTranscript,
    onOutputTranscript,
    onTurnComplete,
    onInterrupted,
    onError,
    onToolCall,
    onToolCallCancellation,
  } = options;

  const [connectionState, setConnectionState] =
    useState<GeminiConnectionState>('disconnected');
  const [isModelSpeaking, setIsModelSpeaking] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const optionsRef = useRef(options);
  const connectResolveRef = useRef<((value: boolean) => void) | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isActiveRef = useRef(false);
  const userDisconnectedRef = useRef(false);
  const reconnectAttemptsRef = useRef(0);
  const silenceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastActivityRef = useRef(Date.now());
  const sessionResumeHandleRef = useRef<string | null>(null);

  optionsRef.current = options;

  function clearTimeoutTimer() {
    if (timeoutRef.current !== null) {
      window.clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
  }

  function base64ToArrayBuffer(base64: string): ArrayBuffer {
    const binaryString = atob(base64);
    const len = binaryString.length;
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) {
      bytes[i] = binaryString.charCodeAt(i);
    }
    return bytes.buffer;
  }

  function parseMessage(data: string): void {
    try {
      const message: IncomingMessage = JSON.parse(data);

      if ('setupComplete' in message && message.setupComplete !== undefined) {
        console.log('[GeminiLive] setupComplete received');
        if (silenceTimerRef.current) {
          clearTimeout(silenceTimerRef.current);
          silenceTimerRef.current = null;
        }
        setConnectionState('ready');
        if (connectResolveRef.current) {
          connectResolveRef.current(true);
          connectResolveRef.current = null;
        }
        return;
      }

      if ('goAway' in message && message.goAway) {
        const reason = (message.goAway as { reason?: string; timeLeft?: { seconds?: number } }).reason ?? 'unknown';
        console.log('[GeminiLive] goAway received, reconnecting...', reason);
        wsRef.current?.close();
        onError?.(`Session ending: ${reason}`);
        return;
      }

      if ('toolCall' in message && message.toolCall) {
        onToolCall?.(message.toolCall as GeminiToolCall);
        return;
      }

      if (
        'toolCallCancellation' in message &&
        message.toolCallCancellation
      ) {
        const ids = message.toolCallCancellation.ids ?? [];
        onToolCallCancellation?.(ids);
        return;
      }

      const serverContent = (message as { serverContent?: ServerContent }).serverContent;
      if (serverContent) {
        lastActivityRef.current = Date.now();
        if (silenceTimerRef.current) {
          clearTimeout(silenceTimerRef.current);
          silenceTimerRef.current = null;
        }

        if (serverContent.interrupted === true) {
          setIsModelSpeaking(false);
          onInterrupted?.();
        }

        const parts = serverContent.modelTurn?.parts ?? [];
        for (const part of parts) {
          if (part.inlineData?.mimeType.startsWith('audio/pcm')) {
            const audioData = base64ToArrayBuffer(part.inlineData.data);
            onAudioReceived?.(audioData);
            setIsModelSpeaking(true);
          }

          if (part.text) {
            console.log('[GeminiLive] Model text response:', part.text);
          }
        }

        if (
          serverContent.inputTranscription?.text &&
          serverContent.inputTranscription.text.trim() !== ''
        ) {
          onInputTranscript?.(serverContent.inputTranscription.text);
        }

        if (
          serverContent.outputTranscription?.text &&
          serverContent.outputTranscription.text.trim() !== ''
        ) {
          onOutputTranscript?.(serverContent.outputTranscription.text);
        }

        if (serverContent.turnComplete === true) {
          setIsModelSpeaking(false);
          onTurnComplete?.();
          startStallWatchdog();
        }
        return;
      }

      if ('sessionResumptionUpdate' in message) {
        const sru = (message as { sessionResumptionUpdate: { newHandle?: string; resumable?: boolean } }).sessionResumptionUpdate;
        if (sru?.newHandle) {
          sessionResumeHandleRef.current = sru.newHandle;
        }
        return;
      }

      if ('usageMetadata' in message) {
        return;
      }

      console.warn('[GeminiLive] Unhandled message type, raw JSON:', data.substring(0, 200));
      if (message && typeof message === 'object' && 'error' in message) {
        const errInfo = (message as Record<string, unknown>).error;
        const msg = typeof errInfo === 'object' && errInfo !== null
          ? String((errInfo as Record<string, unknown>).message || JSON.stringify(errInfo))
          : String(errInfo);
        onError?.(`Setup rejected: ${msg}`);
        disconnect();
        if (connectResolveRef.current) {
          connectResolveRef.current(false);
          connectResolveRef.current = null;
        }
      }
    } catch (err) {
      console.error('[GeminiLive] Failed to parse message:', err);
    }
  }

  function startStallWatchdog() {
    if (silenceTimerRef.current) {
      clearTimeout(silenceTimerRef.current);
    }
    lastActivityRef.current = Date.now();
    silenceTimerRef.current = setTimeout(() => {
      if (!isActiveRef.current || userDisconnectedRef.current) return;
      const silentMs = Date.now() - lastActivityRef.current;
      if (silentMs >= 8000 && isActiveRef.current) {
        console.log('[GeminiLive] Stall detected (', (silentMs / 1000).toFixed(0), 's silence), reconnecting...');
        if (wsRef.current) {
          wsRef.current.close(4000, 'Stall recovery');
        }
      }
    }, 8000);
  }

  const connect = useCallback((): Promise<boolean> => {
    console.log('[GeminiLive] connect() called, apiKey present:', !!apiKey, 'len:', apiKey?.length || 0);
    if (
      wsRef.current !== null &&
      wsRef.current.readyState === WebSocket.OPEN
    ) {
      console.log('[GeminiLive] Already connected, returning true');
      return Promise.resolve(true);
    }

    isActiveRef.current = true;
    setConnectionState('connecting');

    const wsUrl = `wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key=${apiKey}`;
    console.log('[GeminiLive] Creating WebSocket to:', wsUrl.replace(apiKey, '***'));

    const ws = new WebSocket(wsUrl);
    console.log('[GeminiLive] WebSocket created, readyState:', ws.readyState);
    wsRef.current = ws;

    return new Promise<boolean>((resolve) => {
      connectResolveRef.current = resolve;

      ws.onopen = () => {
        console.log('[GeminiLive] WebSocket onopen');
        if (!isActiveRef.current) {
          console.log('[GeminiLive] onopen - not active, returning');
          return;
        }

        setConnectionState('settingUp');
        console.log('[GeminiLive] Sending setup message...');

        const resumeHandle = sessionResumeHandleRef.current;
        const setupMessage: Record<string, unknown> = {
          setup: {
            model: 'models/gemini-2.5-flash-native-audio-latest',
            generationConfig: {
              responseModalities: ['AUDIO'],
            },
            systemInstruction: {
              parts: [{ text: optionsRef.current.systemInstruction }],
            },
            tools: [
              {
                functionDeclarations: [
                  {
                    name: 'companion_execute',
                    description:
                      'Execute actions for the user such as ordering food, booking cabs, finding events, managing reminders, answering questions, and more. Call this when the user wants to perform an action or needs information beyond conversational knowledge.',
                    parameters: {
                      type: 'object',
                      properties: {
                        intent: {
                          type: 'string',
                          description:
                            'The full natural language intent of what the user wants to accomplish. Include all relevant details: restaurant names, food items, quantities, addresses, event names, etc.',
                        },
                      },
                      required: ['intent'],
                    },
                  },
                ],
              },
            ],
            realtimeInputConfig: {
              automaticActivityDetection: {
                disabled: false,
                startOfSpeechSensitivity: 'START_SENSITIVITY_HIGH',
                endOfSpeechSensitivity: 'END_SENSITIVITY_LOW',
                silenceDurationMs: 500,
                prefixPaddingMs: 40,
              },
              activityHandling: 'START_OF_ACTIVITY_INTERRUPTS',
            },
            inputAudioTranscription: {},
            outputAudioTranscription: {},
          },
        };

        if (resumeHandle) {
          (setupMessage.setup as Record<string, unknown>).sessionResumption = { handle: resumeHandle };
          console.log('[GeminiLive] Resuming session with handle:', resumeHandle.substring(0, 8) + '...');
        }

        ws.send(JSON.stringify(setupMessage));

        clearTimeoutTimer();
        timeoutRef.current = setTimeout(() => {
          if (isActiveRef.current && ws.readyState !== WebSocket.OPEN) {
            ws.close();
            setConnectionState('error');
            onError?.('Connection timeout');
            if (connectResolveRef.current) {
              connectResolveRef.current(false);
              connectResolveRef.current = null;
            }
          }
        }, 15000);
      };

      ws.onclose = (event) => {
        console.log('[GeminiLive] WebSocket onclose, code:', event.code, 'reason:', event.reason, 'wasClean:', event.wasClean);
        wsRef.current = null;
        if (connectResolveRef.current) {
          connectResolveRef.current(false);
          connectResolveRef.current = null;
        }

        if (userDisconnectedRef.current) {
          userDisconnectedRef.current = false;
          isActiveRef.current = false;
          setConnectionState('disconnected');
          reconnectAttemptsRef.current = 0;
          return;
        }

        if (reconnectAttemptsRef.current < 3) {
          const delay = Math.pow(2, reconnectAttemptsRef.current) * 1000;
          reconnectAttemptsRef.current++;
          console.log('[GeminiLive] Auto-reconnect attempt', reconnectAttemptsRef.current, 'in', delay, 'ms');
          setConnectionState('connecting');
          setTimeout(() => {
            if (!userDisconnectedRef.current) {
              connect();
            }
          }, delay);
        } else {
          console.log('[GeminiLive] Max reconnect attempts reached');
          isActiveRef.current = false;
          setConnectionState('disconnected');
          reconnectAttemptsRef.current = 0;
          if (event.reason) {
            onError?.(`Disconnected after retries: ${event.reason}`);
          }
        }
      };

      ws.onerror = () => {
        console.log('[GeminiLive] WebSocket onerror');
        if (!isActiveRef.current) return;

        clearTimeoutTimer();
        setConnectionState('error');
        onError?.('WebSocket error');
        if (connectResolveRef.current) {
          connectResolveRef.current(false);
          connectResolveRef.current = null;
        }
      };

      ws.onmessage = async (event) => {
        console.log('[GeminiLive] onmessage, type:', typeof event.data, 'size:', event.data instanceof Blob ? event.data.size : event.data instanceof ArrayBuffer ? event.data.byteLength : typeof event.data === 'string' ? event.data.length : '?');
        if (!isActiveRef.current) return;
        let text: string;
        if (typeof event.data === 'string') {
          text = event.data;
        } else if (event.data instanceof Blob) {
          text = await event.data.text();
          console.log('[GeminiLive] Blob resolved to text:', text.substring(0, 300));
        } else if (event.data instanceof ArrayBuffer) {
          text = new TextDecoder().decode(event.data);
          console.log('[GeminiLive] ArrayBuffer decoded to text:', text.substring(0, 300));
        } else {
          console.warn('[GeminiLive] onmessage: unknown data type, skipping');
          return;
        }
        parseMessage(text);
      };
    });
  }, [apiKey, onError, onAudioReceived, onInputTranscript, onOutputTranscript, onTurnComplete, onInterrupted, onToolCall, onToolCallCancellation]);

  const disconnect = useCallback(() => {
    userDisconnectedRef.current = true;
    reconnectAttemptsRef.current = 0;
    isActiveRef.current = false;
    clearTimeoutTimer();
    if (silenceTimerRef.current) {
      clearTimeout(silenceTimerRef.current);
      silenceTimerRef.current = null;
    }

    if (wsRef.current) {
      wsRef.current.close(1000, 'User initiated disconnect');
      wsRef.current = null;
    }

    setConnectionState('disconnected');
    setIsModelSpeaking(false);
    connectResolveRef.current = null;
  }, []);

  const sendAudio = useCallback((pcmData: ArrayBuffer) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      console.warn('[GeminiLive] sendAudio: WS not open, state:', ws?.readyState);
      return;
    }

    const uint8Array = new Uint8Array(pcmData);
    let binaryString = '';
    for (let i = 0; i < uint8Array.length; i++) {
      binaryString += String.fromCharCode(uint8Array[i]);
    }
    const base64 = btoa(binaryString);

    const message = {
      realtimeInput: {
        audio: {
          mimeType: 'audio/pcm;rate=16000',
          data: base64,
        },
      },
    };

    console.log('[GeminiLive] sendAudio:', pcmData.byteLength, 'bytes PCM');
    ws.send(JSON.stringify(message));
  }, []);

  const sendToolResponse = useCallback(
    (
      response: {
        functionResponses: Array<{
          name: string;
          id: string;
          response: Record<string, string>;
        }>;
      }
    ) => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;

      const message = {
        toolResponse: {
          functionResponses: response.functionResponses.map((fr) => ({
            name: fr.name,
            id: fr.id,
            response: fr.response,
          })),
        },
      };

      ws.send(JSON.stringify(message));
    },
    []
  );

  const sendTextMessage = useCallback((text: string) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      console.warn('[GeminiLive] sendTextMessage: WS not open, readyState:', ws?.readyState);
      return;
    }

    const message = {
      clientContent: {
        turns: [
          {
            role: 'user',
            parts: [{ text }],
          },
        ],
        turnComplete: true,
      },
    };

    console.log('[GeminiLive] sendTextMessage sent:', text.substring(0, 60));
    ws.send(JSON.stringify(message));
  }, []);

  return {
    connectionState,
    isModelSpeaking,
    connect,
    disconnect,
    sendAudio,
    sendToolResponse,
    sendTextMessage,
  };
}

export type UseGeminiLive = ReturnType<typeof createUseGeminiLive>;