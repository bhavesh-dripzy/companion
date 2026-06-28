import React, { useState } from 'react';
import { Mic, Square, Loader2 } from 'lucide-react';
import { useVoiceDictation } from './hooks/useVoiceDictation';

// Push-to-talk dictation: tap to record, tap to stop. With Web Speech the text streams into the
// composer live as you speak; otherwise it's transcribed on stop. No auto-send.
interface VoiceInputProps {
  // Fired when recording starts — the composer captures its current text as the base to append to.
  onStart?: () => void;
  // Live transcript so far (the composer renders base + this).
  onUpdate: (text: string) => void;
  onError?: (message: string) => void;
  disabled?: boolean;
}

export const VoiceInput: React.FC<VoiceInputProps> = ({ onStart, onUpdate, onError, disabled = false }) => {
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const { state, toggle } = useVoiceDictation({
    onStart,
    onUpdate,
    onError: (msg) => { setErrorMessage(msg); onError?.(msg); },
  });

  const recording = state === 'recording';
  const transcribing = state === 'transcribing';

  const icon = transcribing
    ? <Loader2 size={16} strokeWidth={2.5} className="animate-spin" />
    : recording
      ? <Square size={14} strokeWidth={2.5} className="text-white" />
      : <Mic size={16} strokeWidth={2.5} />;

  const base =
    'w-9 h-9 rounded-full flex items-center justify-center transition-all flex-shrink-0 disabled:opacity-30 cursor-pointer';
  const cls = recording
    ? `${base} bg-red-500 hover:bg-red-600 text-white animate-pulse shadow-md shadow-red-500/30`
    : `${base} text-slate-500 hover:text-slate-900 bg-slate-100/60 hover:bg-slate-100`;

  return (
    <div className="relative flex-shrink-0">
      <button
        type="button"
        onClick={() => { setErrorMessage(null); void toggle(); }}
        disabled={disabled || transcribing}
        aria-label={recording ? 'Stop recording' : 'Dictate a message'}
        title={errorMessage || (recording ? 'Tap to stop' : transcribing ? 'Transcribing…' : 'Tap to speak')}
        className={cls}
      >
        {icon}
      </button>

      {recording && (
        <span className="absolute -top-0.5 -right-0.5 w-3 h-3 bg-red-500 rounded-full animate-ping" />
      )}

      {errorMessage && state === 'idle' && (
        <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-3 py-1.5 bg-red-50 border border-red-200 rounded-lg text-xs text-red-600 whitespace-nowrap z-10">
          {errorMessage}
        </div>
      )}
    </div>
  );
};
