import React, { useState, useCallback, useRef } from 'react';
import { Mic, MicOff, Loader2 } from 'lucide-react';
import { useOpenAIRealtime } from './hooks/useOpenAIRealtime';
import { useGeminiLive } from './hooks/useGeminiLive';
import type { VoiceSessionState } from './types';

// Which realtime voice backend to use. Defaults to Gemini Live; set
// VITE_VOICE_PROVIDER=openai to fall back to the OpenAI Realtime path.
const VOICE_PROVIDER = (import.meta.env.VITE_VOICE_PROVIDER as string) || 'gemini';

interface VoiceInputProps {
  onVoiceTurn?: (userText: string, assistantText: string) => void;
  // Per-turn callback (Gemini path) — fires once per completed turn.
  onVoiceTurnComplete?: (userText: string, assistantText: string, hadToolCall: boolean) => void;
  onVoiceStateChange?: (state: VoiceSessionState) => void;
  onVoiceToolCall?: (intent: string) => Promise<string>;
  onVoiceError?: (message: string) => void;
  disabled?: boolean;
}

export const VoiceInput: React.FC<VoiceInputProps> = ({
  onVoiceTurn,
  onVoiceTurnComplete,
  onVoiceStateChange,
  onVoiceToolCall,
  onVoiceError,
  disabled = false,
}) => {
  const [state, setState] = useState<VoiceSessionState>('inactive');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const userTranscriptRef = useRef('');
  const assistantTranscriptRef = useRef('');

  // Both hooks share an identical interface and neither opens any connection
  // until connect() is called, so calling both and selecting one is safe and
  // lets us A/B providers at runtime via VITE_VOICE_PROVIDER.
  const voiceOptions = {
    onUserTranscript: (text: string) => {
      userTranscriptRef.current += ' ' + text;
    },
    onAssistantTranscript: (text: string) => {
      assistantTranscriptRef.current += ' ' + text;
    },
    onToolCall: async (name: string, args: Record<string, string>, callId: string) => {
      if (name === 'companion_execute' && onVoiceToolCall) {
        try {
          const resultText = await onVoiceToolCall(args.intent || '');
          realtime.respondToToolCall(callId, { result: resultText });
        } catch {
          realtime.respondToToolCall(callId, { result: 'Tool execution failed. Please try again.' });
        }
      }
    },
    onTurnComplete: (userText: string, assistantText: string, hadToolCall: boolean) => {
      onVoiceTurnComplete?.(userText, assistantText, hadToolCall);
    },
    onError: (msg: string) => {
      setErrorMessage(msg);
      onVoiceError?.(msg);
    },
    onStateChange: (newState: 'disconnected' | 'connecting' | 'connected') => {
      const voiceState: VoiceSessionState =
        newState === 'connected' ? 'active' :
        newState === 'connecting' ? 'connecting' :
        newState === 'disconnected' ? 'inactive' : 'inactive';
      setState(voiceState);
      onVoiceStateChange?.(voiceState);

      if (newState === 'disconnected') {
        // Gemini renders per-turn via onTurnComplete; only the OpenAI path
        // uses this disconnect-time batch dump (avoids double-rendering).
        if (VOICE_PROVIDER === 'openai') {
          const userText = userTranscriptRef.current.trim();
          const assistantText = assistantTranscriptRef.current.trim();
          if (userText && assistantText) {
            onVoiceTurn?.(userText, assistantText);
          }
        }
        userTranscriptRef.current = '';
        assistantTranscriptRef.current = '';
      }
    },
  };

  const gemini = useGeminiLive(voiceOptions);
  const openai = useOpenAIRealtime(voiceOptions);
  const realtime = VOICE_PROVIDER === 'openai' ? openai : gemini;

  const handleClick = useCallback(async () => {
    if (disabled) return;

    if (state === 'inactive' || state === 'error') {
      setErrorMessage(null);
      setState('connecting');
      try {
        await realtime.connect();
      } catch (err) {
        setState('error');
        const msg = err instanceof Error ? err.message : 'Connection failed';
        setErrorMessage(msg);
        onVoiceError?.(msg);
      }
    } else if (state === 'active' || state === 'connecting') {
      realtime.disconnect();
      setState('inactive');
      userTranscriptRef.current = '';
      assistantTranscriptRef.current = '';
    }
  }, [state, disabled, realtime, onVoiceError]);

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
