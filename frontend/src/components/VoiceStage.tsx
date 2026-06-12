/**
 * Voice stage — gives the live voice conversation a face. A slim dark bar
 * pinned above the composer while a session is active: the dawn-gradient
 * brand orb breathing through listening / thinking / speaking, live
 * captions as transcripts stream in, and a status line while tools run —
 * so ten seconds of Swiggy never feels like dead air. Cards keep rendering
 * in the chat behind it.
 */

import React from 'react';

export type VoicePhase = 'listening' | 'thinking' | 'speaking';

interface VoiceStageProps {
  phase: VoicePhase;
  userCaption?: string;
  assistantCaption?: string;
  statusLine?: string;
  connecting?: boolean;
}

const PHASES: { key: VoicePhase; label: string }[] = [
  { key: 'listening', label: 'Listening' },
  { key: 'thinking', label: 'Thinking' },
  { key: 'speaking', label: 'Speaking' },
];

export const VoiceStage: React.FC<VoiceStageProps> = ({
  phase,
  userCaption,
  assistantCaption,
  statusLine,
  connecting,
}) => {
  const caption = (assistantCaption || userCaption || '').trim();
  const captionRole = assistantCaption?.trim() ? 'assistant' : userCaption?.trim() ? 'user' : null;

  return (
    <div className="fixed left-1/2 -translate-x-1/2 bottom-[calc(var(--safe-bottom)+108px)] md:bottom-[118px] z-30 w-[min(92vw,560px)] pointer-events-none">
      <div className="pointer-events-auto bg-[#14101d]/95 backdrop-blur-md border border-[#3a3450] rounded-2xl px-4 py-3 shadow-[0_24px_60px_-24px_rgba(20,16,29,0.9)] flex items-center gap-4">
        {/* Brand orb — conic dawn gradient, breathing ring while active. */}
        <div className="relative w-12 h-12 shrink-0">
          <div
            className="absolute inset-0 rounded-full animate-[spin_14s_linear_infinite]"
            style={{
              background:
                'conic-gradient(from 210deg, #ff8a1e, #ffb347, #e85d7a, #2b2f72, #ff8a1e)',
            }}
          />
          <div
            className="absolute inset-[5px] rounded-full"
            style={{
              background:
                'radial-gradient(circle at 38% 32%, #ffd9a8, #ff8a1e 38%, #e85d7a 72%, #2b2f72)',
            }}
          />
          <div
            className={`absolute -inset-1.5 rounded-full border-2 border-[#ffb347]/40 ${
              phase === 'listening' ? 'animate-ping' : phase === 'speaking' ? 'animate-pulse' : ''
            }`}
            style={{ animationDuration: phase === 'listening' ? '2.2s' : '1.4s' }}
          />
        </div>

        <div className="min-w-0 flex-1 flex flex-col gap-1">
          <div className="flex gap-1.5">
            {PHASES.map((p) => (
              <span
                key={p.key}
                className={`text-[9px] font-mono uppercase tracking-[0.14em] rounded-full px-2 py-0.5 border transition-colors ${
                  phase === p.key
                    ? 'text-[#ffd9a8] border-[#ff8a1e] bg-[#ff8a1e]/15'
                    : 'text-[#9d93b3] border-[#3a3450]'
                }`}
              >
                {p.label}
              </span>
            ))}
          </div>
          {connecting ? (
            <div className="text-[12px] text-[#9d93b3]">Connecting…</div>
          ) : caption ? (
            <div className="text-[13px] text-[#fdfaf3] leading-snug line-clamp-2">
              {captionRole === 'user' ? '“' : ''}
              {caption}
              {captionRole === 'user' ? '”' : ''}
            </div>
          ) : (
            <div className="text-[12px] text-[#9d93b3]">Bolo — main sun raha hoon</div>
          )}
          {statusLine && phase === 'thinking' && (
            <div className="text-[11px] text-[#ffb347]">{statusLine}</div>
          )}
        </div>
      </div>
    </div>
  );
};
