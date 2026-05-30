import React, { useState, useRef, useCallback } from 'react';
import { Mic, MicOff, Loader2 } from 'lucide-react';
import { SarvamAIClient } from 'sarvamai';

type VoiceInputState = 'idle' | 'recording' | 'processing' | 'error';

interface VoiceInputProps {
  onTranscript: (text: string) => void;
  disabled?: boolean;
  autoSubmit?: boolean;
  onAutoSubmit?: () => void;
}

const SARVAM_API_KEY = import.meta.env.VITE_SARVAM_API_KEY as string | undefined;

export const VoiceInput: React.FC<VoiceInputProps> = ({
  onTranscript,
  disabled = false,
  autoSubmit = false,
  onAutoSubmit,
}) => {
  const [state, setState] = useState<VoiceInputState>('idle');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const onAutoSubmitRef = useRef<(() => void) | null>(null);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);

  if (onAutoSubmit) {
    onAutoSubmitRef.current = onAutoSubmit;
  }

  const startRecording = useCallback(async () => {
    try {
      setErrorMessage(null);
      audioChunksRef.current = [];

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          sampleRate: 16000,
        },
      });

      const mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
      mediaRecorderRef.current = mediaRecorder;

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          audioChunksRef.current.push(event.data);
        }
      };

      mediaRecorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop());

        if (audioChunksRef.current.length === 0) {
          setState('idle');
          return;
        }

        setState('processing');

        try {
          const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/webm' });
          const wavBlob = await convertToWav(audioBlob);
          const transcript = await transcribeAudio(wavBlob);

          if (transcript && transcript.trim()) {
            onTranscript(transcript.trim());
            if (autoSubmit && onAutoSubmitRef.current) {
              setTimeout(() => onAutoSubmitRef.current?.(), 50);
            }
          } else {
            setErrorMessage('No speech detected. Please try again.');
          }
        } catch (err) {
          setErrorMessage(parseSarvamError(err));
        } finally {
          setState('idle');
        }
      };

      mediaRecorder.start();
      setState('recording');

      setTimeout(() => {
        if (mediaRecorder.state === 'recording') {
          mediaRecorder.stop();
        }
      }, 30000);
    } catch (err) {
      const message =
        err instanceof Error
          ? err.name === 'NotAllowedError'
            ? 'Microphone access denied. Please allow microphone access in your browser settings.'
            : err.message
          : 'Could not access microphone';
      setErrorMessage(message);
      setState('idle');
    }
  }, [onTranscript, autoSubmit]);

  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current?.state === 'recording') {
      mediaRecorderRef.current.stop();
    }
  }, []);

  const handleClick = () => {
    if (state === 'idle') {
      startRecording();
    } else if (state === 'recording') {
      stopRecording();
    }
  };

  const getButtonContent = () => {
    switch (state) {
      case 'recording':
        return <MicOff size={16} strokeWidth={2.5} className="text-white" />;
      case 'processing':
        return <Loader2 size={16} strokeWidth={2.5} className="animate-spin" />;
      default:
        return <Mic size={16} strokeWidth={2.5} />;
    }
  };

  const getButtonClass = () => {
    const base =
      'w-9 h-9 rounded-full flex items-center justify-center transition-all flex-shrink-0 disabled:opacity-30 cursor-pointer';

    if (state === 'recording') {
      return `${base} bg-red-500 hover:bg-red-600 text-white animate-pulse shadow-md shadow-red-500/30`;
    }
    return `${base} text-slate-500 hover:text-slate-900 bg-slate-100/60 hover:bg-slate-100`;
  };

  return (
    <div className="relative flex-shrink-0">
      <button
        type="button"
        onClick={handleClick}
        disabled={disabled || state === 'processing'}
        aria-label={state === 'recording' ? 'Stop recording' : 'Voice input'}
        title={
          state === 'recording'
            ? 'Click to stop recording'
            : errorMessage || 'Click to speak'
        }
        className={getButtonClass()}
      >
        {getButtonContent()}
      </button>

      {state === 'recording' && (
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

async function convertToWav(webmBlob: Blob): Promise<Blob> {
  const arrayBuffer = await webmBlob.arrayBuffer();
  const audioContext = new AudioContext({ sampleRate: 16000 });
  const audioBuffer = await audioContext.decodeAudioData(arrayBuffer);
  audioContext.close();

  const wavBuffer = encodeWav(audioBuffer);
  return new Blob([wavBuffer], { type: 'audio/wav' });
}

function encodeWav(audioBuffer: AudioBuffer): ArrayBuffer {
  const numChannels = 1;
  const sampleRate = audioBuffer.sampleRate;
  const bitsPerSample = 16;
  const bytesPerSample = bitsPerSample / 8;
  const blockAlign = numChannels * bytesPerSample;
  const byteRate = sampleRate * blockAlign;
  const dataLength = audioBuffer.length * bytesPerSample;
  const buffer = new ArrayBuffer(44 + dataLength);
  const view = new DataView(buffer);

  writeString(view, 0, 'RIFF');
  view.setUint32(4, 36 + dataLength, true);
  writeString(view, 8, 'WAVE');
  writeString(view, 12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, numChannels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, byteRate, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, bitsPerSample, true);
  writeString(view, 36, 'data');
  view.setUint32(40, dataLength, true);

  const channelData = audioBuffer.getChannelData(0);
  let offset = 44;
  for (let i = 0; i < channelData.length; i++) {
    const sample = Math.max(-1, Math.min(1, channelData[i]));
    view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    offset += 2;
  }

  return buffer;
}

function writeString(view: DataView, offset: number, str: string) {
  for (let i = 0; i < str.length; i++) {
    view.setUint8(offset + i, str.charCodeAt(i));
  }
}

async function transcribeAudio(wavBlob: Blob): Promise<string> {
  if (!SARVAM_API_KEY) {
    throw new Error('Sarvam API key not configured. Set VITE_SARVAM_API_KEY in your .env file.');
  }

  const client = new SarvamAIClient({ apiSubscriptionKey: SARVAM_API_KEY });
  const arrayBuffer = await wavBlob.arrayBuffer();

  const response = await client.speechToText.transcribe({
    file: arrayBuffer,
    model: 'saaras:v3',
    mode: 'transcribe',
  });

  return response.transcript || '';
}

function parseSarvamError(err: unknown): string {
  if (err && typeof err === 'object' && 'message' in err) {
    const msg = (err as { message: unknown }).message;
    if (typeof msg === 'string') return msg;
  }
  if (err instanceof Error) return err.message;
  return 'Transcription failed. Please try again.';
}