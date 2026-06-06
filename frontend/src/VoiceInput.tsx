import React, { useState, useRef, useCallback, useEffect } from 'react';
import { Mic, MicOff, Loader2 } from 'lucide-react';
import { createUseGeminiLive } from './hooks/useGeminiLive';
import type { GeminiToolCall } from './hooks/useGeminiLive';
import { useAudioStream } from './hooks/useAudioStream';
import type { VoiceSessionState } from './types';

interface VoiceInputProps {
  onVoiceTurn?: (userText: string, assistantText: string) => void;
  onVoiceStateChange?: (state: VoiceSessionState) => void;
  onVoiceToolCall?: (intent: string) => Promise<string>;
  onVoiceError?: (message: string) => void;
  disabled?: boolean;
}

const GEMINI_API_KEY = import.meta.env.VITE_GEMINI_API_KEY as string | undefined;
console.log('[VoiceInput] init, VITE_GEMINI_API_KEY:', GEMINI_API_KEY ? 'present (len=' + GEMINI_API_KEY.length + ')' : 'MISSING');

export const VoiceInput: React.FC<VoiceInputProps> = ({
  onVoiceTurn,
  onVoiceStateChange,
  onVoiceToolCall,
  onVoiceError,
  disabled = false,
}) => {
  const [state, setState] = useState<VoiceSessionState>('inactive');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const userTranscriptRef = useRef('');
  const assistantTranscriptRef = useRef('');
  const pendingToolCallsRef = useRef<Map<string, GeminiToolCall>>(new Map());

  const geminiLive = createUseGeminiLive({
    apiKey: GEMINI_API_KEY || '',
    systemInstruction:
      "You are Companion, a helpful personal assistant. When the user connects, greet them warmly and introduce yourself. Ask what you can help with today. Answer clearly and concisely in plain language. Stay factual; say when you are unsure rather than guessing. You can help with food ordering, cab booking, finding events, managing reminders, and answering questions. When the user asks you to perform an action, use the companion_execute function.",
    onAudioReceived: (data) => audioStream.playAudio(data),
    onInputTranscript: (text) => {
      userTranscriptRef.current += text;
    },
    onOutputTranscript: (text) => {
      assistantTranscriptRef.current += text;
    },
    onTurnComplete: () => {
      const userText = userTranscriptRef.current.trim();
      const assistantText = assistantTranscriptRef.current.trim();
      if (userText && assistantText) {
        onVoiceTurn?.(userText, assistantText);
      }
      userTranscriptRef.current = '';
      assistantTranscriptRef.current = '';
    },
    onInterrupted: () => {
      audioStream.stopPlayback();
      userTranscriptRef.current = '';
      assistantTranscriptRef.current = '';
    },
    onError: (msg) => {
      setErrorMessage(msg);
      onVoiceError?.(msg);
    },
    onToolCall: async (call) => {
      for (const functionCall of call.functionCalls) {
        if (functionCall.name === 'companion_execute') {
          const intent = functionCall.args.intent;
          const callId = functionCall.id;
          pendingToolCallsRef.current.set(callId, call);

          try {
            const resultText = await onVoiceToolCall?.(intent) ?? '';
            const toolResponse = {
              functionResponses: [
                {
                  name: 'companion_execute',
                  id: callId,
                  response: { result: resultText },
                },
              ],
            };
            geminiLive.sendToolResponse(toolResponse);
          } catch {
            const errorResponse = {
              functionResponses: [
                {
                  name: 'companion_execute',
                  id: callId,
                  response: { result: 'Tool execution failed. Please try again.' },
                },
              ],
            };
            geminiLive.sendToolResponse(errorResponse);
          } finally {
            pendingToolCallsRef.current.delete(callId);
          }
        }
      }
    },
    onToolCallCancellation: (ids) => {
      for (const id of ids) {
        pendingToolCallsRef.current.delete(id);
      }
    },
  });

  const audioStream = useAudioStream({
    onAudioCaptured: (data) => geminiLive.sendAudio(data),
    onCaptureError: (msg) => {
      setErrorMessage(msg);
      onVoiceError?.(msg);
    },
  });

  useEffect(() => {
    let newState: VoiceSessionState;
    switch (geminiLive.connectionState) {
      case 'ready':
        newState = 'active';
        break;
      case 'disconnected':
        newState = 'inactive';
        break;
      case 'connecting':
      case 'settingUp':
        newState = 'connecting';
        break;
      case 'error':
        newState = 'error';
        break;
      default:
        newState = 'inactive';
    }
    setState(newState);
    onVoiceStateChange?.(newState);
  }, [geminiLive.connectionState, onVoiceStateChange]);

  useEffect(() => {
    audioStream.setMicMuted(geminiLive.isModelSpeaking);
  }, [geminiLive.isModelSpeaking, audioStream]);

  useEffect(() => {
    return () => {
      audioStream.stopCapture();
      audioStream.stopPlayback();
      geminiLive.disconnect();
    };
  }, []);

  const handleClick = useCallback(async () => {
    console.log('[VoiceInput] handleClick', { disabled, state, geminiLiveExists: !!geminiLive, connectFn: typeof geminiLive?.connect });
    if (disabled) {
      console.log('[VoiceInput] handleClick - blocked by disabled');
      return;
    }

    const currentState = state;
    console.log('[VoiceInput] handleClick currentState:', currentState);

    if (currentState === 'inactive' || currentState === 'error') {
      console.log('[VoiceInput] Starting connection...');
      setErrorMessage(null);
      setState('connecting');

      try {
        console.log('[VoiceInput] Calling geminiLive.connect()...');
        const success = await geminiLive.connect();
        console.log('[VoiceInput] geminiLive.connect() returned:', success);
        if (success) {
          console.log('[VoiceInput] Sending greeting...');
          geminiLive.sendTextMessage('Hello! Start the conversation. Introduce yourself as Companion and ask how you can help today.');
          console.log('[VoiceInput] Starting audio capture...');
          await audioStream.startCapture();
          console.log('[VoiceInput] Audio capture started - speak now');
          setState('active');
        } else {
          console.log('[VoiceInput] connect returned false');
          setState('error');
          setErrorMessage('Failed to connect to Gemini');
        }
      } catch (err) {
        console.error('[VoiceInput] connect threw:', err);
        setState('error');
        const msg = err instanceof Error ? err.message : 'Connection failed';
        setErrorMessage(msg);
        onVoiceError?.(msg);
      }
    } else if (currentState === 'active' || currentState === 'connecting') {
      audioStream.stopCapture();
      audioStream.stopPlayback();
      geminiLive.disconnect();
      setState('inactive');
      userTranscriptRef.current = '';
      assistantTranscriptRef.current = '';
    }
  }, [state, disabled, geminiLive, audioStream, onVoiceError]);

  const getButtonContent = () => {
    if (state === 'connecting') {
      return <Loader2 size={16} strokeWidth={2.5} className="animate-spin" />;
    }
    if (state === 'active') {
      return <MicOff size={16} strokeWidth={2.5} className="text-white" />;
    }
    return <Mic size={16} strokeWidth={2.5} />;
  };

  const getButtonClass = () => {
    const base =
      'w-9 h-9 rounded-full flex items-center justify-center transition-all flex-shrink-0 disabled:opacity-30 cursor-pointer';

    if (state === 'active') {
      return `${base} bg-red-500 hover:bg-red-600 text-white animate-pulse shadow-md shadow-red-500/30`;
    }
    return `${base} text-slate-500 hover:text-slate-900 bg-slate-100/60 hover:bg-slate-100`;
  };

  return (
    <div className="relative flex-shrink-0">
      <button
        type="button"
        onClick={handleClick}
        disabled={disabled}
        aria-label={state === 'active' ? 'End voice session' : 'Start voice session'}
        title={errorMessage || 'Click to speak'}
        className={getButtonClass()}
      >
        {getButtonContent()}
      </button>

      {state === 'active' && (
        <span className="absolute -top-0.5 -right-0.5 w-3 h-3 bg-red-500 rounded-full animate-ping" />
      )}

      {errorMessage && state === 'inactive' && (
        <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-3 py-1.5 bg-red-50 border border-red-200 rounded-lg text-xs text-red-600 whitespace-nowrap z-10">
          {errorMessage}
        </div>
      )}
    </div>
  );
};