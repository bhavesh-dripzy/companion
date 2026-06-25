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
import type { StreamSegment, TurnStatus } from '../types';
import { WorkingCard } from './WorkingCard';

const CHARS_PER_TICK = 2;     // typing speed (chars revealed per tick)
const TICK_MS = 16;
const PAUSE_AFTER_TEXT = 160; // beat after a line finishes before the next segment
const PAUSE_AFTER_CARDS = 120;

function OmiAvatar() {
  return (
    <div className="w-9 h-9 shrink-0 relative select-none">
      <img
        src="/omi-logo.png"
        alt="Omi"
        draggable={false}
        className="w-9 h-9 rounded-full object-cover bg-white shadow-sm ring-1 ring-[#ece3d6]/80"
      />
      <span className="absolute bottom-0 right-0 w-2.5 h-2.5 rounded-full bg-[#1a9d6b] border-2 border-white" />
    </div>
  );
}

export function StreamingTurn({
  segments,
  streaming,
  working,
  onQuickPrompt,
  quickPromptDisabled,
  responseAvatar = false,
}: {
  segments: StreamSegment[];
  streaming: boolean;
  working?: TurnStatus | null;
  onQuickPrompt?: (prompt: string) => void;
  quickPromptDisabled?: boolean;
  /** Product-grid turns hide the message-level avatar so the grid spans full width; when true, the
   *  FINAL narration bubble (the reply that follows the results) gets its own inline Omi avatar so
   *  it reads as omi's response rather than an orphaned line. */
  responseAvatar?: boolean;
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

  // Group consecutive text segments so a run of short narration lines renders as
  // ONE bubble (tighter, less fragmented) instead of a stack of floating bubbles.
  // Card segments stay standalone. Order is preserved, so text→cards→text still
  // alternates correctly.
  type TextSeg = Extract<StreamSegment, { kind: 'text' }>;
  type CardSeg = Extract<StreamSegment, { kind: 'cards' }>;
  type Group =
    | { kind: 'text'; items: { seg: TextSeg; i: number }[] }
    | { kind: 'cards'; seg: CardSeg; i: number };
  const groups: Group[] = [];
  segments.forEach((seg, i) => {
    if (seg.kind === 'text') {
      const last = groups[groups.length - 1];
      if (last && last.kind === 'text') last.items.push({ seg, i });
      else groups.push({ kind: 'text', items: [{ seg, i }] });
    } else {
      groups.push({ kind: 'cards', seg, i });
    }
  });

  // While still working and caught up: if the last thing on screen is a narration bubble, the loader
  // lives INSIDE that same capsule (no second card); otherwise fall back to a standalone working card.
  const lastGroup = groups[groups.length - 1];
  const lastIsText = lastGroup?.kind === 'text';
  const inlineLoading = streaming && caughtUp && lastIsText;

  return (
    <div className="flex flex-col gap-2.5 w-full">
      {groups.map((group) => {
        if (group.kind === 'cards') {
          // not revealed yet, or empty — skip
          if (group.i > cursor || !group.seg.attachments?.length) return null;
          return (
            <AttachmentList
              key={group.i}
              attachments={group.seg.attachments}
              onQuickPrompt={onQuickPrompt}
              quickPromptDisabled={quickPromptDisabled}
            />
          );
        }

        // text group — one bubble, each revealed line stacked inside it
        const revealed = group.items.filter(
          ({ i }) => i < cursor || (i === cursor && typed > 0),
        );
        if (revealed.length === 0) return null;
        const lines = (
          <>
            {group.items.map(({ seg, i }) => {
              if (i > cursor) return null;
              const shown = i < cursor ? seg.text : seg.text.slice(0, typed);
              if (!shown) return null;
              const typing = i === cursor && typed < seg.text.length;
              return (
                <div
                  key={i}
                  className="prose prose-sm max-w-none prose-p:my-0.5 prose-p:text-[14.5px] prose-slate prose-strong:text-[#221b2e]"
                >
                  <ReactMarkdown remarkPlugins={[remarkBreaks]}>{shown}</ReactMarkdown>
                  {typing && <span className="inline-block w-1.5 h-3.5 ml-0.5 align-middle bg-[#ff8a1e] animate-pulse rounded-sm" />}
                </div>
              );
            })}
            {/* Loader lives in the SAME capsule as the narration while the turn finishes working. */}
            {inlineLoading && group === lastGroup && (
              <div className="flex items-center gap-1 pt-1">
                <span className="w-1.5 h-1.5 rounded-full bg-[#ff8a1e] animate-bounce [animation-delay:-0.3s]" />
                <span className="w-1.5 h-1.5 rounded-full bg-[#ff8a1e] animate-bounce [animation-delay:-0.15s]" />
                <span className="w-1.5 h-1.5 rounded-full bg-[#ff8a1e] animate-bounce" />
              </div>
            )}
          </>
        );
        const bubbleCls =
          'animate-fade-in px-4 py-3 rounded-[18px] rounded-tl-md bg-white/[0.82] backdrop-blur-sm border border-[#ece3d6]/80 text-[#221b2e] shadow-2xs text-[14.5px] leading-snug select-text w-fit max-w-full space-y-1';
        // The reply that FOLLOWS the results gets its own Omi avatar so it reads as omi's response,
        // not an orphaned line below the full-width grid.
        if (responseAvatar && group === lastGroup) {
          return (
            <div key={group.items[0].i} className="flex items-start gap-2.5">
              <OmiAvatar />
              <div className={bubbleCls}>{lines}</div>
            </div>
          );
        }
        return (
          <div key={group.items[0].i} className={bubbleCls}>
            {lines}
          </div>
        );
      })}
      {/* Standalone working card only when there's no narration bubble to host the loader. */}
      {streaming && caughtUp && !lastIsText && <WorkingCard status={working} />}
    </div>
  );
}
