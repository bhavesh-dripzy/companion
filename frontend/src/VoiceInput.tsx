import React, { useState, useCallback, useRef } from 'react';
import { Mic, MicOff, Loader2 } from 'lucide-react';
import { useOpenAIRealtime } from './hooks/useOpenAIRealtime';
import type { VoiceSessionState } from './types';

interface VoiceInputProps {
  onVoiceTurn?: (userText: string, assistantText: string) => void;
  onVoiceStateChange?: (state: VoiceSessionState) => void;
  onVoiceToolCall?: (intent: string) => Promise<string>;
  onVoiceError?: (message: string) => void;
  disabled?: boolean;
}

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

  const realtime = useOpenAIRealtime({
    onUserTranscript: (text) => {
      userTranscriptRef.current += ' ' + text;
    },
    onAssistantTranscript: (text) => {
      assistantTranscriptRef.current += ' ' + text;
    },
    onToolCall: async (name, args, callId) => {
      if (name === 'companion_execute' && onVoiceToolCall) {
        try {
          const resultText = await onVoiceToolCall(args.intent || '');
          realtime.respondToToolCall(callId, { result: resultText });
        } catch {
          realtime.respondToToolCall(callId, { result: 'Tool execution failed. Please try again.' });
        }
      }
    },
    onError: (msg) => {
      setErrorMessage(msg);
      onVoiceError?.(msg);
    },
    onStateChange: (newState) => {
      const voiceState: VoiceSessionState =
        newState === 'connected' ? 'active' :
        newState === 'connecting' ? 'connecting' :
        newState === 'disconnected' ? 'inactive' : 'inactive';
      setState(voiceState);
      onVoiceStateChange?.(voiceState);

      if (newState === 'disconnected') {
        const userText = userTranscriptRef.current.trim();
        const assistantText = assistantTranscriptRef.current.trim();
        if (userText && assistantText) {
          onVoiceTurn?.(userText, assistantText);
        }
        userTranscriptRef.current = '';
        assistantTranscriptRef.current = '';
      }
    },
  });

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
