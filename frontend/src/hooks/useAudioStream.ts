/**
 * useAudioStream - React hook for microphone capture and speaker playback
 *
 * Captures microphone audio at 16kHz Int16 PCM, accumulates into ~100ms chunks.
 * Plays back 24kHz Int16 PCM audio through speakers.
 */

import { useRef, useCallback, useEffect } from "react";

interface UseAudioStreamOptions {
  onAudioCaptured: (data: ArrayBuffer) => void;
  onCaptureError: (message: string) => void;
}

interface UseAudioStreamReturn {
  startCapture: () => Promise<void>;
  stopCapture: () => void;
  playAudio: (data: ArrayBuffer) => void;
  stopPlayback: () => void;
  setMicMuted: (muted: boolean) => void;
  isCapturing: boolean;
}

const CAPTURE_SAMPLE_RATE = 16000;
const PLAYBACK_SAMPLE_RATE = 24000;
const CHUNK_SIZE_BYTES = 3200;
const SCRIPT_PROCESSOR_BUFFER_SIZE = 4096;

export function useAudioStream({
  onAudioCaptured,
  onCaptureError,
}: UseAudioStreamOptions): UseAudioStreamReturn {
  const captureContextRef = useRef<AudioContext | null>(null);
  const playbackContextRef = useRef<AudioContext | null>(null);

  const mediaStreamRef = useRef<MediaStream | null>(null);
  const scriptProcessorRef = useRef<ScriptProcessorNode | null>(null);
  const isCapturingRef = useRef<boolean>(false);
  const isMicMutedRef = useRef<boolean>(false);

  const activeSourceNodesRef = useRef<AudioBufferSourceNode[]>([]);
  const accumulatedBuffersRef = useRef<ArrayBuffer[]>([]);
  const nextPlayTimeRef = useRef<number>(0);

  const float32ToInt16 = useCallback((floatSamples: Float32Array): ArrayBuffer => {
    const int16Samples = new Int16Array(floatSamples.length);
    for (let i = 0; i < floatSamples.length; i++) {
      const clamped = Math.max(-1, Math.min(1, floatSamples[i]));
      int16Samples[i] = Math.round(clamped * 32767);
    }
    return int16Samples.buffer;
  }, []);

  const flushAccumulatedBytes = useCallback(() => {
    if (accumulatedBuffersRef.current.length === 0) {
      return;
    }
    let totalLength = 0;
    for (const buf of accumulatedBuffersRef.current) {
      totalLength += buf.byteLength;
    }
    if (totalLength < CAPTURE_SAMPLE_RATE * 0.025) {
      accumulatedBuffersRef.current = [];
      return;
    }
    const result = new Uint8Array(totalLength);
    let offset = 0;
    for (const chunk of accumulatedBuffersRef.current) {
      result.set(new Uint8Array(chunk), offset);
      offset += chunk.byteLength;
    }
    accumulatedBuffersRef.current = [];
    console.log('[AudioStream] Sending chunk:', totalLength, 'bytes (~' + Math.round(totalLength / 32) + 'ms)');
    onAudioCaptured(result.buffer);
  }, [onAudioCaptured]);

  const startCapture = useCallback(async (): Promise<void> => {
    console.log('[AudioStream] startCapture called, isCapturing:', isCapturingRef.current);
    if (isCapturingRef.current) {
      return;
    }

    try {
      console.log('[AudioStream] Requesting microphone...');
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          sampleRate: CAPTURE_SAMPLE_RATE,
          channelCount: 1,
        },
      });
      console.log('[AudioStream] Microphone access granted');

      mediaStreamRef.current = stream;

      const AudioContextClass =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const captureContext = new AudioContextClass({ sampleRate: CAPTURE_SAMPLE_RATE });
      captureContextRef.current = captureContext;
      console.log('[AudioStream] AudioContext actual sampleRate:', captureContext.sampleRate, 'state:', captureContext.state);

      if (captureContext.state === "suspended") {
        await captureContext.resume();
      }

      const source = captureContext.createMediaStreamSource(stream);

      const inputFormat = source.context.sampleRate;
      console.log('[AudioStream] MediaStreamSource sampleRate:', inputFormat);

      const scriptProcessor = captureContext.createScriptProcessor(
        SCRIPT_PROCESSOR_BUFFER_SIZE,
        1,
        1
      );
      scriptProcessorRef.current = scriptProcessor;

      accumulatedBuffersRef.current = [];
      let processCount = 0;

      scriptProcessor.onaudioprocess = (event: AudioProcessingEvent): void => {
        processCount++;
        if (processCount <= 5) {
          console.log('[AudioStream] onaudioprocess #' + processCount + ', muted:', isMicMutedRef.current);
        }

        if (isMicMutedRef.current) {
          return;
        }

        const inputBuffer = event.inputBuffer;
        const floatSamples = inputBuffer.getChannelData(0);

        let hasNonSilent = false;
        let maxAbs = 0;
        for (let i = 0; i < floatSamples.length; i++) {
          const abs = Math.abs(floatSamples[i]);
          if (abs > maxAbs) maxAbs = abs;
          if (abs > 0.001) {
            hasNonSilent = true;
          }
        }

        if (processCount <= 5) {
          console.log('[AudioStream] process #' + processCount + ' maxAmp:', maxAbs.toFixed(6), 'hasNonSilent:', hasNonSilent);
        }

        if (!hasNonSilent) {
          return;
        }

        const int16Buffer = float32ToInt16(floatSamples);

        const uint8View = new Uint8Array(int16Buffer);
        accumulatedBuffersRef.current.push(uint8View.buffer);

        const totalBytes = accumulatedBuffersRef.current.reduce((a: number, b: ArrayBuffer) => a + b.byteLength, 0);
        if (totalBytes >= CHUNK_SIZE_BYTES) {
          flushAccumulatedBytes();
        }
      };

      source.connect(scriptProcessor);
      const zeroGain = captureContext.createGain();
      zeroGain.gain.value = 0;
      scriptProcessor.connect(zeroGain);
      zeroGain.connect(captureContext.destination);

      isCapturingRef.current = true;
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Unknown error";
      if (message.includes("Permission denied") || message.includes("NotAllowedError")) {
        onCaptureError("Microphone access denied");
      } else {
        onCaptureError(`Microphone error: ${message}`);
      }
      throw error;
    }
  }, [float32ToInt16, flushAccumulatedBytes, onCaptureError]);

  const stopCapture = useCallback((): void => {
    if (!isCapturingRef.current) {
      return;
    }

    flushAccumulatedBytes();

    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((track) => track.stop());
      mediaStreamRef.current = null;
    }

    if (scriptProcessorRef.current) {
      scriptProcessorRef.current.disconnect();
      scriptProcessorRef.current = null;
    }

    if (captureContextRef.current) {
      captureContextRef.current.close();
      captureContextRef.current = null;
    }

    isCapturingRef.current = false;
  }, [flushAccumulatedBytes]);

  const playAudio = useCallback((data: ArrayBuffer): void => {
    if (data.byteLength === 0) {
      return;
    }

    let playbackContext = playbackContextRef.current;
    if (!playbackContext) {
      const AudioContextClass =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      playbackContext = new AudioContextClass({ sampleRate: PLAYBACK_SAMPLE_RATE });
      playbackContextRef.current = playbackContext;
    }

    if (playbackContext.state === "suspended") {
      playbackContext.resume();
    }

    const now = playbackContext.currentTime;
    if (nextPlayTimeRef.current < now) {
      nextPlayTimeRef.current = now;
    }

    const int16Array = new Int16Array(data);
    const floatSamples = new Float32Array(int16Array.length);
    for (let i = 0; i < int16Array.length; i++) {
      floatSamples[i] = int16Array[i] / 32768.0;
    }

    const audioBuffer = playbackContext.createBuffer(
      1,
      floatSamples.length,
      PLAYBACK_SAMPLE_RATE
    );
    audioBuffer.getChannelData(0).set(floatSamples);

    const sourceNode = playbackContext.createBufferSource();
    sourceNode.buffer = audioBuffer;
    sourceNode.connect(playbackContext.destination);

    activeSourceNodesRef.current.push(sourceNode);

    const startTime = nextPlayTimeRef.current;
    const duration = floatSamples.length / PLAYBACK_SAMPLE_RATE;
    nextPlayTimeRef.current = startTime + duration;

    sourceNode.onended = (): void => {
      activeSourceNodesRef.current = activeSourceNodesRef.current.filter(
        (node) => node !== sourceNode
      );
    };

    sourceNode.start(startTime);
  }, []);

  const stopPlayback = useCallback((): void => {
    for (const sourceNode of activeSourceNodesRef.current) {
      try {
        sourceNode.stop();
      } catch {
        // Ignore if already stopped
      }
    }
    activeSourceNodesRef.current = [];
    nextPlayTimeRef.current = 0;
  }, []);

  const setMicMuted = useCallback((muted: boolean): void => {
    isMicMutedRef.current = muted;
  }, []);

  useEffect(() => {
    return () => {
      stopCapture();
      stopPlayback();
    };
  }, [stopCapture, stopPlayback]);

  return {
    startCapture,
    stopCapture,
    playAudio,
    stopPlayback,
    setMicMuted,
    get isCapturing(): boolean {
      return isCapturingRef.current;
    },
  };
}