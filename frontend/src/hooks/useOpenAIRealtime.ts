import { useRef, useState, useCallback } from 'react';
import { api } from '../api';

export interface UseOpenAIRealtimeOptions {
  onUserTranscript?: (text: string) => void;
  onAssistantTranscript?: (text: string) => void;
  onToolCall?: (name: string, args: Record<string, string>, callId: string) => void;
  onError?: (message: string) => void;
  onStateChange?: (state: 'disconnected' | 'connecting' | 'connected') => void;
}

export interface UseOpenAIRealtimeReturn {
  state: 'disconnected' | 'connecting' | 'connected';
  connect: () => Promise<void>;
  disconnect: () => void;
  isSpeaking: boolean;
  respondToToolCall: (callId: string, output: Record<string, unknown>) => void;
}

export function useOpenAIRealtime(options: UseOpenAIRealtimeOptions): UseOpenAIRealtimeReturn {
  const { onUserTranscript, onAssistantTranscript, onToolCall, onError, onStateChange } = options;
  const [state, setState] = useState<'disconnected' | 'connecting' | 'connected'>('disconnected');
  const [isSpeaking, setIsSpeaking] = useState(false);

  const pcRef = useRef<RTCPeerConnection | null>(null);
  const dcRef = useRef<RTCDataChannel | null>(null);
  const micStreamRef = useRef<MediaStream | null>(null);
  const audioElRef = useRef<HTMLAudioElement | null>(null);

  const connect = useCallback(async () => {
    setState('connecting');
    onStateChange?.('connecting');

    try {
      // 1. Get ephemeral token from backend
      const tokenData = await api.post('/api/voice/token/');
      const ephemeralKey = tokenData.token;
      if (!ephemeralKey) throw new Error('Failed to get OpenAI token');

      // 2. Create RTCPeerConnection
      const pc = new RTCPeerConnection();
      pcRef.current = pc;

      // 3. Create audio element for playback
      const audioEl = document.createElement('audio');
      audioEl.autoplay = true;
      audioEl.setAttribute('aria-hidden', 'true');
      audioEl.style.display = 'none';
      document.body.appendChild(audioEl);
      audioElRef.current = audioEl;

      pc.ontrack = (e) => {
        audioEl.srcObject = e.streams[0];
        audioEl.play?.().catch(() => {});
      };

      // 4. Get microphone and add to peer connection
      const micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      micStreamRef.current = micStream;
      micStream.getTracks().forEach((t) => pc.addTrack(t, micStream));

      // 5. Create data channel for OpenAI events
      const dc = pc.createDataChannel('oai-events');
      dcRef.current = dc;

      dc.onopen = () => {
        setState('connected');
        onStateChange?.('connected');
      };

      dc.onmessage = (e) => {
        try {
          const evt = JSON.parse(e.data);
          switch (evt.type) {
            case 'conversation.item.input_audio_transcription.completed':
              if (evt.transcript) onUserTranscript?.(evt.transcript);
              break;
            case 'response.audio_transcript.done':
              if (evt.transcript) onAssistantTranscript?.(evt.transcript);
              break;
            case 'response.function_call_arguments.done':
              onToolCall?.(evt.name, JSON.parse(evt.arguments || '{}'), evt.call_id);
              break;
            case 'response.audio.delta':
              setIsSpeaking(true);
              break;
            case 'response.audio.done':
              setIsSpeaking(false);
              break;
            case 'error':
              onError?.(evt.error?.message || 'OpenAI Realtime error');
              break;
          }
        } catch {
          // skip parse errors silently
        }
      };

      dc.onclose = () => {
        setState('disconnected');
        onStateChange?.('disconnected');
      };

      // 6. Create offer and exchange SDP
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);

      const sdpRes = await fetch('https://api.openai.com/v1/realtime/calls', {
        method: 'POST',
        body: offer.sdp,
        headers: {
          Authorization: `Bearer ${ephemeralKey}`,
          'Content-Type': 'application/sdp',
        },
      });

      if (!sdpRes.ok) throw new Error(`SDP exchange failed: ${sdpRes.status}`);
      const answer = { type: 'answer' as RTCSdpType, sdp: await sdpRes.text() };
      await pc.setRemoteDescription(answer);

    } catch (err) {
      setState('disconnected');
      onStateChange?.('disconnected');
      const msg = err instanceof Error ? err.message : 'Connection failed';
      onError?.(msg);
      disconnect();
    }
  }, [onUserTranscript, onAssistantTranscript, onToolCall, onError, onStateChange]);

  const disconnect = useCallback(() => {
    micStreamRef.current?.getTracks().forEach((t) => t.stop());
    micStreamRef.current = null;
    dcRef.current?.close();
    dcRef.current = null;
    pcRef.current?.close();
    pcRef.current = null;
    audioElRef.current?.remove();
    audioElRef.current = null;
    setState('disconnected');
    setIsSpeaking(false);
    onStateChange?.('disconnected');
  }, [onStateChange]);

  const respondToToolCall = useCallback((callId: string, output: Record<string, unknown>) => {
    if (!dcRef.current || dcRef.current.readyState !== 'open') return;
    dcRef.current.send(JSON.stringify({
      type: 'conversation.item.create',
      item: {
        type: 'function_call_output',
        call_id: callId,
        output: JSON.stringify(output),
      },
    }));
    dcRef.current.send(JSON.stringify({ type: 'response.create' }));
  }, []);

  return {
    state,
    connect,
    disconnect,
    isSpeaking,
    respondToToolCall,
  };
}