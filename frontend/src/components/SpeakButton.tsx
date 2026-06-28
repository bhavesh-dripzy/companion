import React, { useRef, useState } from 'react';
import { Volume2, Square, Loader2 } from 'lucide-react';
import { api } from '../api';

// On-demand read-aloud for one assistant message. Tap to fetch + play the reply via Sarvam TTS;
// tap again to stop. Nothing speaks unless the user asks for it.
export const SpeakButton: React.FC<{ text: string; className?: string }> = ({ text, className }) => {
  const [state, setState] = useState<'idle' | 'loading' | 'playing'>('idle');
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const stop = () => {
    if (audioRef.current) { audioRef.current.pause(); audioRef.current = null; }
    setState('idle');
  };

  const play = async () => {
    const clean = (text || '').trim();
    if (!clean) return;
    setState('loading');
    try {
      const blob = await api.postBlob('/api/voice/tts', { text: clean });
      if (blob.size === 0) { setState('idle'); return; }
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audioRef.current = audio;
      audio.onended = () => { URL.revokeObjectURL(url); audioRef.current = null; setState('idle'); };
      audio.onerror = () => { URL.revokeObjectURL(url); audioRef.current = null; setState('idle'); };
      setState('playing');
      await audio.play();
    } catch {
      setState('idle');
    }
  };

  const icon =
    state === 'loading' ? <Loader2 size={13} className="animate-spin" />
    : state === 'playing' ? <Square size={12} className="fill-current" />
    : <Volume2 size={14} />;

  return (
    <button
      type="button"
      onClick={() => (state === 'idle' ? void play() : stop())}
      aria-label={state === 'playing' ? 'Stop reading' : 'Read aloud'}
      title={state === 'playing' ? 'Stop' : 'Read aloud'}
      className={
        className ??
        'inline-flex items-center justify-center w-6 h-6 rounded-full text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors'
      }
    >
      {icon}
    </button>
  );
};
