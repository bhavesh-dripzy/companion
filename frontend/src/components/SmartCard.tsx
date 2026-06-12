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

const TONE_TEXT: Record<SmartCardTone, string> = {
  saffron: 'text-[#b35a08]',
  indigo: 'text-[#2b2f72]',
  green: 'text-[#137a52]',
  rose: 'text-[#b03a55]',
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
      onPointerEnter={() => {
        pausedRef.current = true;
      }}
      onPointerLeave={() => {
        pausedRef.current = false;
      }}
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
      <div className="relative h-[150px]">
        {slides.map((slide, i) => (
          <div
            key={slide.id}
            aria-hidden={i !== active}
            aria-live={i === active ? 'polite' : undefined}
            className={`absolute inset-0 rounded-[18px] border border-[#ece3d6] bg-white/90 backdrop-blur-sm px-5 py-4 text-left shadow-[0_16px_40px_-24px_rgba(60,40,30,0.5)] transition-all duration-700 ease-out ${
              i === active ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-2 pointer-events-none'
            }`}
          >
            <div
              className={`flex items-center gap-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.16em] mb-2 ${TONE_TEXT[slide.tone]}`}
            >
              <span aria-hidden>{slide.icon}</span>
              {slide.eyebrow}
            </div>
            <div
              className={`text-[13.5px] leading-relaxed text-[#4a4458] ${
                slide.cta ? 'line-clamp-2' : 'line-clamp-4'
              }`}
            >
              {slide.body}
            </div>
            {slide.cta && (
              <button
                type="button"
                onClick={slide.cta.onClick}
                disabled={ctaDisabled}
                className="mt-2.5 text-[12px] font-bold text-[#2b2f72] border-b-[1.5px] border-[#2b2f72]/30 pb-px hover:border-[#2b2f72] transition-colors cursor-pointer disabled:cursor-not-allowed disabled:opacity-50"
              >
                {slide.cta.label} →
              </button>
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
                i === active ? 'w-3.5 bg-[#ff8a1e]' : 'w-[5px] bg-[#d8d0c2] hover:bg-[#c4bbaa]'
              }`}
            />
          ))}
        </div>
      )}
    </div>
  );
}
