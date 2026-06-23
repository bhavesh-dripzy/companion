/**
 * StreamingTurn — the generic "player" for a live SSE turn.
 *
 * It is deliberately dumb and domain-agnostic: it walks an ordered list of `segments` (text or
 * cards) IN ARRIVAL ORDER and plays them at a human pace — typing each text line out, building each
 * card block in, and showing a "thinking" shimmer whenever it has caught up and the turn is still
 * streaming. It knows nothing about providers, ordering, or counts; the backend decides all of that
 * and just streams segments. So a one-line chitchat reply and a 4-provider fan-out render through
 * the exact same component, and new tools/cards never require touching it.
 *
 * (A future text-to-speech layer can hook the same per-text-segment reveal to speak each line.)
 */

import { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkBreaks from 'remark-breaks';
import { AttachmentList } from '../ChatLog';
import type { StreamSegment } from '../types';

const CHARS_PER_TICK = 2;     // typing speed (chars revealed per tick)
const TICK_MS = 16;
const PAUSE_AFTER_TEXT = 160; // beat after a line finishes before the next segment
const PAUSE_AFTER_CARDS = 120;

export function StreamingTurn({
  segments,
  streaming,
  onQuickPrompt,
  quickPromptDisabled,
}: {
  segments: StreamSegment[];
  streaming: boolean;
  onQuickPrompt?: (prompt: string) => void;
  quickPromptDisabled?: boolean;
}) {
  // Playback cursor: index of the segment currently being revealed + chars typed within it.
  const [cursor, setCursor] = useState(0);
  const [typed, setTyped] = useState(0);

  useEffect(() => {
    const seg = segments[cursor];
    if (!seg) return; // caught up → wait (the shimmer shows while streaming)
    if (seg.kind === 'cards') {
      const t = setTimeout(() => { setCursor((c) => c + 1); setTyped(0); }, PAUSE_AFTER_CARDS);
      return () => clearTimeout(t);
    }
    if (typed < seg.text.length) {
      const t = setTimeout(
        () => setTyped((n) => Math.min(seg.text.length, n + CHARS_PER_TICK)),
        TICK_MS,
      );
      return () => clearTimeout(t);
    }
    const t = setTimeout(() => { setCursor((c) => c + 1); setTyped(0); }, PAUSE_AFTER_TEXT);
    return () => clearTimeout(t);
  }, [segments, cursor, typed]);

  const caughtUp = cursor >= segments.length;

  return (
    <div className="flex flex-col gap-2 w-full">
      {segments.map((seg, i) => {
        if (i > cursor) return null; // not revealed yet — strictly sequential
        if (seg.kind === 'text') {
          const shown = i < cursor ? seg.text : seg.text.slice(0, typed);
          if (!shown) return null;
          const typing = i === cursor && typed < seg.text.length;
          return (
            <div
              key={i}
              className="animate-fade-in px-4 py-3 rounded-[16px] rounded-tl-md bg-white/[0.82] backdrop-blur-sm border border-[#ece3d6]/80 text-[#221b2e] shadow-2xs text-[14.5px] leading-snug select-text w-fit max-w-full"
            >
              <div className="prose prose-sm max-w-none prose-p:my-1 prose-p:text-[14.5px] prose-slate prose-strong:text-[#221b2e]">
                <ReactMarkdown remarkPlugins={[remarkBreaks]}>{shown}</ReactMarkdown>
              </div>
              {typing && <span className="inline-block w-1.5 h-3.5 ml-0.5 align-middle bg-[#ff8a1e] animate-pulse rounded-sm" />}
            </div>
          );
        }
        // cards block — reuse the existing renderer (provider theming, quick-prompt popovers, etc.)
        if (!seg.attachments?.length) return null;
        return (
          <AttachmentList
            key={i}
            attachments={seg.attachments}
            onQuickPrompt={onQuickPrompt}
            quickPromptDisabled={quickPromptDisabled}
          />
        );
      })}
      {streaming && caughtUp && (
        <div className="flex items-center gap-1.5 px-2 py-1.5 w-fit">
          <span className="w-1.5 h-1.5 rounded-full bg-[#ff8a1e]/70 animate-bounce [animation-delay:-0.2s]" />
          <span className="w-1.5 h-1.5 rounded-full bg-[#e85d7a]/70 animate-bounce [animation-delay:-0.1s]" />
          <span className="w-1.5 h-1.5 rounded-full bg-[#2b2f72]/60 animate-bounce" />
        </div>
      )}
    </div>
  );
}
