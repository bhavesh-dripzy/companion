/**
 * One-slot rotating "smart card" for the hero empty state: a single slide
 * visible at a time (heritage fact, time-of-day nudge, cart resume, …) with
 * a gentle cross-fade, pagination dots, swipe-to-advance and hover pause.
 * Slides are a generic contract, so future nudge sources (reminders due,
 * price alerts, IPO updates) plug in as new slides without layout changes.
 */

import React, { useEffect, useRef, useState } from 'react';

export type SmartCardTone = 'saffron' | 'indigo' | 'green' | 'rose';

export interface SmartCardSlide {
  id: string;
  eyebrow: string;
  icon: React.ReactNode;
  tone: SmartCardTone;
  body: React.ReactNode;
  cta?: { label: string; onClick: () => void };
}

interface SmartCardProps {
  slides: SmartCardSlide[];
  intervalMs?: number;
  ctaDisabled?: boolean;
  /** Fired each time the auto-rotation wraps past the last slide. */
  onWrap?: () => void;
}

const TONE_ACCENT_BG: Record<SmartCardTone, string> = {
  saffron: 'from-[#ff8a1e] to-[#f5a623]',
  indigo: 'from-[#2b2f72] to-[#4a5196]',
  green: 'from-[#137a52] to-[#1da76e]',
  rose: 'from-[#b03a55] to-[#d9537a]',
};

const TONE_ICON_CHIP: Record<SmartCardTone, string> = {
  saffron: 'bg-[#fff3e0] text-[#b35a08]',
  indigo: 'bg-[#eef0f9] text-[#2b2f72]',
  green: 'bg-[#e6f4ed] text-[#137a52]',
  rose: 'bg-[#fcedf1] text-[#b03a55]',
};

const TONE_EYEBROW: Record<SmartCardTone, string> = {
  saffron: 'text-[#b35a08]',
  indigo: 'text-[#2b2f72]',
  green: 'text-[#137a52]',
  rose: 'text-[#b03a55]',
};

const TONE_CTA: Record<SmartCardTone, string> = {
  saffron: 'bg-[#ff8a1e]/10 text-[#b35a08] hover:bg-[#ff8a1e]/20',
  indigo: 'bg-[#2b2f72]/10 text-[#2b2f72] hover:bg-[#2b2f72]/20',
  green: 'bg-[#137a52]/10 text-[#137a52] hover:bg-[#137a52]/20',
  rose: 'bg-[#b03a55]/10 text-[#b03a55] hover:bg-[#b03a55]/20',
};

export function SmartCard({ slides, intervalMs = 8000, ctaDisabled = false, onWrap }: SmartCardProps) {
  const [active, setActive] = useState(0);
  const pausedRef = useRef(false);
  const touchX = useRef<number | null>(null);
  const onWrapRef = useRef(onWrap);
  onWrapRef.current = onWrap;

  const advance = (dir: 1 | -1) => {
    setActive((prev) => {
      const next = (prev + dir + slides.length) % slides.length;
      if (dir === 1 && next === 0 && prev !== 0) onWrapRef.current?.();
      return next;
    });
  };

  useEffect(() => {
    if (slides.length < 2) return;
    const tick = setInterval(() => {
      if (!pausedRef.current) advance(1);
    }, intervalMs);
    return () => clearInterval(tick);
  }, [intervalMs, slides.length]);

  if (slides.length === 0) return null;

  return (
    <div
      className="w-full max-w-[460px]"
      onPointerEnter={() => { pausedRef.current = true; }}
      onPointerLeave={() => { pausedRef.current = false; }}
      onTouchStart={(e) => {
        pausedRef.current = true;
        touchX.current = e.touches[0].clientX;
      }}
      onTouchEnd={(e) => {
        const start = touchX.current;
        touchX.current = null;
        pausedRef.current = false;
        if (start === null) return;
        const dx = e.changedTouches[0].clientX - start;
        if (Math.abs(dx) > 40) advance(dx < 0 ? 1 : -1);
      }}
    >
      <div className="relative h-[136px]">
        {slides.map((slide, i) => (
          <div
            key={slide.id}
            aria-hidden={i !== active}
            aria-live={i === active ? 'polite' : undefined}
            className={`absolute inset-0 flex overflow-hidden rounded-2xl bg-white shadow-[0_4px_24px_-8px_rgba(40,30,60,0.12)] border border-[#ece3d6]/70 transition-all duration-700 ease-out ${
              i === active ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-2 pointer-events-none'
            }`}
          >
            {/* Left accent stripe */}
            <div className={`w-[4px] shrink-0 bg-gradient-to-b ${TONE_ACCENT_BG[slide.tone]} rounded-l-2xl`} />

            {/* Content */}
            <div className="flex-1 px-4 py-4 text-left flex flex-col justify-between min-w-0">
              {/* Eyebrow */}
              <div className={`flex items-center gap-2 mb-2.5`}>
                <span
                  className={`inline-flex items-center justify-center w-6 h-6 rounded-lg text-[13px] shrink-0 ${TONE_ICON_CHIP[slide.tone]}`}
                  aria-hidden
                >
                  {slide.icon}
                </span>
                <span className={`font-mono text-[9.5px] font-bold uppercase tracking-[0.18em] ${TONE_EYEBROW[slide.tone]}`}>
                  {slide.eyebrow}
                </span>
              </div>

              {/* Body */}
              <p className={`text-[13px] leading-[1.55] text-[#3d3650] flex-1 ${slide.cta ? 'line-clamp-2' : 'line-clamp-4'}`}>
                {slide.body}
              </p>

              {/* CTA */}
              {slide.cta && (
                <button
                  type="button"
                  onClick={slide.cta.onClick}
                  disabled={ctaDisabled}
                  className={`mt-2.5 self-start text-[11.5px] font-semibold px-2.5 py-1 rounded-lg transition-colors cursor-pointer disabled:cursor-not-allowed disabled:opacity-50 ${TONE_CTA[slide.tone]}`}
                >
                  {slide.cta.label} →
                </button>
              )}
            </div>

            {/* Arrow icon (no-cta slides) */}
            {!slide.cta && (
              <div className="shrink-0 flex items-end pb-4 pr-4">
                <span className={`w-7 h-7 rounded-full flex items-center justify-center text-[13px] ${TONE_ICON_CHIP[slide.tone]}`}>
                  ↗
                </span>
              </div>
            )}
          </div>
        ))}
      </div>

      {slides.length > 1 && (
        <div className="flex justify-center gap-1.5 mt-3">
          {slides.map((slide, i) => (
            <button
              key={slide.id}
              type="button"
              aria-label={`Show: ${slide.eyebrow}`}
              onClick={() => setActive(i)}
              className={`h-[5px] rounded-full transition-all duration-300 cursor-pointer ${
                i === active
                  ? `w-3.5 bg-gradient-to-r ${TONE_ACCENT_BG[slides[active].tone]}`
                  : 'w-[5px] bg-[#d8d0c2] hover:bg-[#c4bbaa]'
              }`}
            />
          ))}
        </div>
      )}
    </div>
  );
}
