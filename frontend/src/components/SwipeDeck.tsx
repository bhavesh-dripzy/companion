/**
 * Tinder-style deck for any multi-result card attachment: one full-detail
 * tile at a time — drag right to shortlist, left to pass, tap to flip for
 * the spec sheet. `DeckTile` is a generic contract so every provider's card
 * kind plugs in via deckTiles.tsx without layout changes; CTAs route
 * through the same onQuickPrompt chat round-trip as the classic grids.
 */

import React, { useLayoutEffect, useRef, useState } from 'react';
import { Bookmark, LayoutGrid, RotateCcw, Undo2, X } from 'lucide-react';
import type { CardProvider } from '../types';
import { cn } from '../utils';
import { themeFor } from '../ChatLog';

export interface DeckStat {
  label: string;
  value: string;
  note?: string;
}

export interface DeckSpec {
  label: string;
  value: string;
}

export interface DeckAction {
  label: string;
  prompt?: string;
  href?: string;
}

export interface DeckTile {
  id: string;
  kind: string;
  provider?: CardProvider;
  title: string;
  price?: string;
  priceNote?: string;
  sub?: string;
  image?: string | null;
  icon?: React.ReactNode;
  ribbon?: string;
  rating?: number;
  stats: DeckStat[];
  specs: DeckSpec[];
  cta?: DeckAction;
  cta2?: DeckAction;
}

export interface DeckGroup {
  label: string;
  icon?: React.ReactNode;
  tiles: DeckTile[];
}

const SWIPE_DIST = 90;
const SWIPE_VEL = 0.55;
const EASE_FLUID = 'cubic-bezier(0.22, 1, 0.36, 1)';

interface DragState {
  sx: number;
  sy: number;
  dx: number;
  dy: number;
  vx: number;
  lastX: number;
  lastT: number;
  t0: number;
}

/** Broken-image-safe hero: photo with ink gradient, or brand-tinted fallback art. */
const TileHero: React.FC<{ tile: DeckTile }> = ({ tile }) => {
  const [broken, setBroken] = useState(false);
  const theme = themeFor(tile.provider);
  const showImage = !!tile.image && !broken;

  return (
    <div className="relative h-[44%] shrink-0 overflow-hidden bg-slate-900">
      {showImage ? (
        <img
          src={tile.image!}
          alt={tile.title}
          loading="lazy"
          draggable={false}
          onError={() => setBroken(true)}
          referrerPolicy="no-referrer"
          className="pointer-events-none h-full w-full select-none object-cover"
        />
      ) : (
        <div
          className="flex h-full w-full items-center justify-center text-white/70"
          style={{
            background: `radial-gradient(120% 90% at 85% -10%, ${theme.brandHex}55, transparent 60%), linear-gradient(150deg, #221b2e, ${theme.brandHex})`,
          }}
        >
          <span className="scale-[2.4] opacity-80">{tile.icon}</span>
        </div>
      )}
      <div className="absolute inset-x-0 bottom-0 h-1/2 bg-gradient-to-t from-black/65 to-transparent" />
      <div className="absolute left-3 right-3 top-3 flex gap-1.5">
        <span
          className="inline-flex items-center gap-1.5 rounded-full bg-white/95 px-2.5 py-1 font-mono text-[9px] font-bold uppercase tracking-[0.12em] text-[#221b2e] shadow-sm"
        >
          <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: theme.brandHex }} />
          {theme.label}
        </span>
        <span className="inline-flex items-center rounded-full bg-black/40 px-2.5 py-1 font-mono text-[9px] font-bold uppercase tracking-[0.12em] text-white backdrop-blur-sm">
          {tile.kind}
        </span>
      </div>
      <div className="absolute bottom-2.5 left-3 right-3 flex items-end justify-between gap-2">
        {tile.ribbon ? (
          <span className="max-w-[70%] truncate rounded-lg bg-white/95 px-2 py-1 font-mono text-[9.5px] font-bold uppercase tracking-wide text-[#221b2e] shadow-sm">
            {tile.ribbon}
          </span>
        ) : (
          <span />
        )}
        {typeof tile.rating === 'number' && tile.rating > 0 && (
          <span className="rounded-lg bg-[#1a9d6b] px-2 py-1 font-display text-[13px] font-bold text-white shadow-md">
            {tile.rating.toFixed(1)}★
          </span>
        )}
      </div>
    </div>
  );
};

const TileActions: React.FC<{
  tile: DeckTile;
  brandHex: string;
  onAction: (a?: DeckAction) => void;
  actionable: (a?: DeckAction) => boolean;
  full?: boolean;
}> = ({ tile, brandHex, onAction, actionable, full }) => (
  <div className="mt-3 flex gap-2">
    {tile.cta && (
      <button
        type="button"
        disabled={!actionable(tile.cta)}
        onClick={() => onAction(tile.cta)}
        style={{ backgroundColor: brandHex }}
        className={cn(
          'flex-[1.4] rounded-[13px] px-3 py-2.5 text-[12.5px] font-bold text-white shadow-md transition-all',
          actionable(tile.cta) ? 'cursor-pointer hover:brightness-110 active:scale-[0.98]' : 'cursor-not-allowed opacity-50',
          full && 'flex-1',
        )}
      >
        {tile.cta.label}
      </button>
    )}
    {!full && tile.cta2 && (
      <button
        type="button"
        disabled={!actionable(tile.cta2)}
        onClick={() => onAction(tile.cta2)}
        className={cn(
          'flex-1 rounded-[13px] border border-[#ece3d6] bg-white px-3 py-2.5 text-[12.5px] font-bold text-[#221b2e] transition-all',
          actionable(tile.cta2) ? 'cursor-pointer hover:border-[#b9aa93]' : 'cursor-not-allowed opacity-50',
        )}
      >
        {tile.cta2.label}
      </button>
    )}
  </div>
);

export const SwipeDeck: React.FC<{
  group: DeckGroup;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
  onShowGrid?: () => void;
}> = ({ group, onQuickPrompt, disabled, onShowGrid }) => {
  const tiles = group.tiles;
  const [history, setHistory] = useState<(1 | -1)[]>([]);
  const [flipped, setFlipped] = useState(false);
  const cardRefs = useRef<(HTMLDivElement | null)[]>([]);
  const dragRef = useRef<DragState | null>(null);

  const idx = history.length;
  const done = idx >= tiles.length;
  const saved = tiles.filter((_, i) => history[i] === 1);
  const topTheme = themeFor(tiles[Math.min(idx, tiles.length - 1)]?.provider);

  useLayoutEffect(() => {
    tiles.forEach((_, i) => {
      const el = cardRefs.current[i];
      if (!el) return;
      const d = i - idx;
      if (d < 0) {
        const dir = history[i] ?? 1;
        el.style.transform = `translate(${dir * 150}%, 60px) rotate(${dir * 24}deg)`;
        el.style.opacity = '0';
        el.style.pointerEvents = 'none';
        el.style.zIndex = '0';
      } else {
        const depth = Math.min(d, 3);
        el.style.transform = `translateY(${depth * 14}px) scale(${1 - depth * 0.05})`;
        el.style.opacity = d > 2 ? '0' : '1';
        el.style.pointerEvents = d === 0 ? 'auto' : 'none';
        el.style.zIndex = String(40 - depth);
      }
    });
  }, [idx, history, tiles]);

  const clearStamps = (el: HTMLDivElement | null) => {
    el?.style.setProperty('--save-o', '0');
    el?.style.setProperty('--skip-o', '0');
  };

  const settle = (i: number) => {
    const el = cardRefs.current[i];
    if (el) {
      el.style.transform = 'translateY(0px) scale(1)';
      clearStamps(el);
    }
    const next = cardRefs.current[i + 1];
    if (next) next.style.transform = 'translateY(14px) scale(0.95)';
  };

  const commit = (dir: 1 | -1) => {
    if (done) return;
    cardRefs.current[idx]?.style.setProperty(dir > 0 ? '--save-o' : '--skip-o', '1');
    setFlipped(false);
    setHistory((h) => (h.length >= tiles.length ? h : [...h, dir]));
  };

  const undo = () => {
    if (history.length === 0) return;
    clearStamps(cardRefs.current[history.length - 1]);
    setFlipped(false);
    setHistory(history.slice(0, -1));
  };

  const restart = () => {
    cardRefs.current.forEach(clearStamps);
    setFlipped(false);
    setHistory([]);
  };

  const onPointerDown = (i: number) => (e: React.PointerEvent<HTMLDivElement>) => {
    if (i !== idx || done) return;
    if ((e.target as HTMLElement).closest('button, a')) return;
    const el = cardRefs.current[i];
    if (!el) return;
    el.setPointerCapture(e.pointerId);
    el.style.transition = 'none';
    dragRef.current = {
      sx: e.clientX,
      sy: e.clientY,
      dx: 0,
      dy: 0,
      vx: 0,
      lastX: e.clientX,
      lastT: performance.now(),
      t0: performance.now(),
    };
  };

  const onPointerMove = (i: number) => (e: React.PointerEvent<HTMLDivElement>) => {
    const d = dragRef.current;
    if (!d || i !== idx) return;
    d.dx = e.clientX - d.sx;
    d.dy = e.clientY - d.sy;
    const now = performance.now();
    d.vx = 0.7 * d.vx + (0.3 * (e.clientX - d.lastX)) / Math.max(now - d.lastT, 1);
    d.lastX = e.clientX;
    d.lastT = now;
    const el = cardRefs.current[i];
    if (!el) return;
    el.style.transform = `translate(${d.dx}px, ${d.dy * 0.4}px) rotate(${d.dx * 0.06}deg)`;
    el.style.setProperty('--save-o', String(Math.min(Math.max(d.dx, 0) / SWIPE_DIST, 1)));
    el.style.setProperty('--skip-o', String(Math.min(Math.max(-d.dx, 0) / SWIPE_DIST, 1)));
    const next = cardRefs.current[i + 1];
    if (next) {
      const p = Math.min(Math.abs(d.dx) / 140, 1);
      next.style.transform = `translateY(${14 - 6 * p}px) scale(${0.95 + 0.035 * p})`;
    }
  };

  const onPointerUp = (i: number) => (e: React.PointerEvent<HTMLDivElement>) => {
    const d = dragRef.current;
    if (!d || i !== idx) return;
    dragRef.current = null;
    const el = cardRefs.current[i];
    if (el) el.style.transition = '';
    if (e.type === 'pointerup') {
      d.dx = e.clientX - d.sx;
      d.dy = e.clientY - d.sy;
    }
    const dist = Math.hypot(d.dx, d.dy);
    if (dist < 8 && performance.now() - d.t0 < 450) {
      setFlipped((f) => !f);
      settle(i);
      return;
    }
    if (Math.abs(d.dx) > SWIPE_DIST || Math.abs(d.vx) > SWIPE_VEL) {
      commit(d.dx > 0 || (Math.abs(d.dx) <= SWIPE_DIST && d.vx > 0) ? 1 : -1);
    } else {
      settle(i);
    }
  };

  const actionable = (a?: DeckAction) => !!a && (a.prompt ? !!onQuickPrompt && !disabled : !!a.href);

  const fireAction = (a?: DeckAction) => {
    if (!a) return;
    if (a.prompt && onQuickPrompt && !disabled) onQuickPrompt(a.prompt);
    else if (a.href) window.open(a.href, '_blank', 'noopener');
  };

  if (tiles.length === 0) return null;

  return (
    <div className="mt-3 w-full max-w-full animate-fade-in select-none">
      <div className="mx-auto mb-2 flex max-w-[380px] items-center gap-1.5 px-1">
        <span style={{ color: topTheme.brandHex }}>{group.icon}</span>
        <span className="truncate text-xs font-bold uppercase tracking-wider text-slate-500">
          {group.label}
        </span>
        <span className="font-mono text-[10px] font-semibold text-slate-400">({tiles.length})</span>
        {onShowGrid && (
          <button
            type="button"
            onClick={onShowGrid}
            className="ml-auto inline-flex shrink-0 cursor-pointer items-center gap-1 rounded-full border border-[#ece3d6] bg-white px-2.5 py-1 text-[10px] font-bold text-slate-500 transition-colors hover:border-[#b9aa93] hover:text-slate-800"
            title="Show all results as a grid"
          >
            <LayoutGrid size={11} />
            Grid
          </button>
        )}
      </div>

      <div className="mx-auto max-w-[380px]">
        <div className="relative h-[470px] w-full [perspective:1400px]">
          {tiles.map((tile, i) => {
            const theme = themeFor(tile.provider);
            const isTop = i === idx;
            return (
              <div
                key={tile.id}
                ref={(el) => {
                  cardRefs.current[i] = el;
                }}
                onPointerDown={onPointerDown(i)}
                onPointerMove={onPointerMove(i)}
                onPointerUp={onPointerUp(i)}
                onPointerCancel={onPointerUp(i)}
                className="absolute inset-0 touch-none will-change-transform transition-[transform,opacity] duration-[550ms]"
                style={{ transitionTimingFunction: EASE_FLUID }}
              >
                <div
                  className={cn(
                    'relative h-full w-full transition-transform duration-500 [transform-style:preserve-3d]',
                    isTop && flipped && '[transform:rotateY(180deg)]',
                  )}
                  style={{ transitionTimingFunction: EASE_FLUID }}
                >
                  <div className="absolute inset-0 flex flex-col overflow-hidden rounded-3xl border border-[#ece3d6] bg-white shadow-[0_24px_60px_-28px_rgba(60,40,30,0.45)] [backface-visibility:hidden]">
                    <TileHero tile={tile} />
                    <div className="flex min-h-0 flex-1 flex-col p-4">
                      <div className="flex items-baseline justify-between gap-3">
                        <h3 className="font-display text-[17px] font-semibold leading-tight text-[#221b2e] line-clamp-2">
                          {tile.title}
                        </h3>
                        {tile.price && (
                          <span className="shrink-0 text-right font-mono text-[14px] font-bold text-[#221b2e]">
                            {tile.price}
                            {tile.priceNote && (
                              <em className="block text-right font-sans text-[9px] font-medium not-italic tracking-wide text-slate-400">
                                {tile.priceNote}
                              </em>
                            )}
                          </span>
                        )}
                      </div>
                      {tile.sub && (
                        <p className="mt-1 text-[11.5px] leading-snug text-slate-500 line-clamp-2">{tile.sub}</p>
                      )}
                      {tile.stats.length > 0 && (
                        <div
                          className="mt-auto grid gap-2 pt-3"
                          style={{ gridTemplateColumns: `repeat(${Math.min(tile.stats.length, 3)}, 1fr)` }}
                        >
                          {tile.stats.slice(0, 3).map((s) => (
                            <div key={s.label} className="rounded-xl border border-[#ece3d6] bg-[#fdfaf3]/70 px-2.5 py-2">
                              <span className="block font-mono text-[8px] font-bold uppercase tracking-[0.14em] text-slate-400">
                                {s.label}
                              </span>
                              <span className="mt-0.5 block truncate text-[13px] font-bold text-[#221b2e]">{s.value}</span>
                              {s.note && <span className="block truncate text-[9.5px] text-slate-400">{s.note}</span>}
                            </div>
                          ))}
                        </div>
                      )}
                      <TileActions tile={tile} brandHex={theme.brandHex} onAction={fireAction} actionable={actionable} />
                    </div>
                  </div>

                  <div className="absolute inset-0 flex flex-col overflow-hidden rounded-3xl border border-[#ece3d6] bg-white shadow-[0_24px_60px_-28px_rgba(60,40,30,0.45)] [backface-visibility:hidden] [transform:rotateY(180deg)]">
                    <div className="flex items-center gap-1.5 p-4 pb-0">
                      <span
                        className="inline-flex items-center gap-1.5 rounded-full border border-[#ece3d6] bg-white px-2.5 py-1 font-mono text-[9px] font-bold uppercase tracking-[0.12em] text-[#221b2e]"
                      >
                        <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: theme.brandHex }} />
                        {theme.label}
                      </span>
                      <span className="inline-flex items-center rounded-full bg-[#221b2e]/5 px-2.5 py-1 font-mono text-[9px] font-bold uppercase tracking-[0.12em] text-[#221b2e]">
                        {tile.kind} · details
                      </span>
                    </div>
                    <h4 className="px-4 pt-3 font-display text-[16px] font-semibold leading-tight text-[#221b2e]">
                      {tile.title}
                    </h4>
                    <div className="mt-1 min-h-0 flex-1 overflow-y-auto px-4 [scrollbar-width:thin]">
                      {tile.specs.map((s, si) => (
                        <div
                          key={`${s.label}-${si}`}
                          className="flex items-start justify-between gap-3 border-b border-dashed border-[#ece3d6] py-2 last:border-0"
                        >
                          <span className="shrink-0 pt-0.5 font-mono text-[9px] font-bold uppercase tracking-[0.14em] text-slate-400">
                            {s.label}
                          </span>
                          <span className="text-right text-[12px] font-semibold leading-snug text-[#221b2e]">{s.value}</span>
                        </div>
                      ))}
                    </div>
                    <div className="p-4 pt-2">
                      <TileActions tile={tile} brandHex={theme.brandHex} onAction={fireAction} actionable={actionable} full />
                      <span className="mt-2 block text-center font-mono text-[8.5px] font-bold uppercase tracking-[0.2em] text-slate-400">
                        tap card to flip back
                      </span>
                    </div>
                  </div>
                </div>

                <div
                  className="pointer-events-none absolute left-4 top-5 z-10 rotate-[-12deg] rounded-xl border-[3px] border-[#1a9d6b] bg-white/90 px-3 py-1 font-display text-[17px] font-bold tracking-[0.1em] text-[#1a9d6b]"
                  style={{ opacity: 'var(--save-o, 0)' }}
                >
                  SHORTLIST
                </div>
                <div
                  className="pointer-events-none absolute right-4 top-5 z-10 rotate-[12deg] rounded-xl border-[3px] border-[#b3525e] bg-white/90 px-3 py-1 font-display text-[17px] font-bold tracking-[0.1em] text-[#b3525e]"
                  style={{ opacity: 'var(--skip-o, 0)' }}
                >
                  PASS
                </div>
              </div>
            );
          })}

          {done && (
            <div className="absolute inset-0 z-50 flex animate-fade-in flex-col items-center justify-center gap-1.5 rounded-3xl border-[1.5px] border-dashed border-[#ddd0bf] bg-white/75 p-6 text-center backdrop-blur-sm">
              <div className="brand-orb mb-1 h-10 w-10" />
              <div className="font-display text-lg font-semibold text-[#221b2e]">That's the lot.</div>
              <p className="text-xs text-slate-500">
                {tiles.length} reviewed · {saved.length} shortlisted
                {saved.length > 0 && ' — tap a shortlist chip below to act on it'}
              </p>
              <button
                type="button"
                onClick={restart}
                className="mt-2 inline-flex cursor-pointer items-center gap-1.5 rounded-[13px] border border-[#ece3d6] bg-white px-4 py-2 text-[12px] font-bold text-[#221b2e] transition-colors hover:border-[#b9aa93]"
              >
                <RotateCcw size={12} />
                Deal again
              </button>
            </div>
          )}
        </div>

        <div className="mt-2.5 flex items-center gap-2.5 px-1">
          <span className="shrink-0 font-mono text-[10px] font-semibold tracking-[0.1em] text-slate-400">
            {done ? 'DONE' : `${idx + 1} OF ${tiles.length}`}
          </span>
          <div className="flex flex-1 gap-1">
            {tiles.map((t, i) => (
              <span
                key={t.id}
                className={cn(
                  'h-[3px] flex-1 rounded-full transition-colors duration-300',
                  i < idx ? 'bg-gradient-to-r from-[#ff8a1e] to-[#e85d7a]' : i === idx ? 'bg-[#2b2f72]' : 'bg-[#2b2f72]/10',
                )}
              />
            ))}
          </div>
          {history.length > 0 && (
            <button
              type="button"
              onClick={undo}
              className="inline-flex shrink-0 cursor-pointer items-center gap-1 rounded-full border border-[#ece3d6] bg-white px-2 py-0.5 font-mono text-[9px] font-bold uppercase tracking-wider text-slate-500 transition-colors hover:border-[#b9aa93] hover:text-slate-800"
            >
              <Undo2 size={10} />
              Undo
            </button>
          )}
          <span className="shrink-0 rounded-full border border-[#1a9d6b]/25 bg-[#1a9d6b]/10 px-2 py-0.5 font-mono text-[9px] font-bold tracking-wider text-[#137a52]">
            {saved.length} SAVED
          </span>
        </div>

        {!done && (
          <div className="mt-2 flex items-center justify-center gap-5">
            <button
              type="button"
              onClick={() => commit(-1)}
              className="grid h-11 w-11 cursor-pointer place-items-center rounded-full border-[1.5px] border-[#f1d4d4] bg-white text-[#b3525e] shadow-sm transition-transform hover:scale-110 active:scale-95"
              title="Pass"
            >
              <X size={18} strokeWidth={2.5} />
            </button>
            <span className="font-mono text-[8.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
              drag · tap = details
            </span>
            <button
              type="button"
              onClick={() => commit(1)}
              className="grid h-12 w-12 cursor-pointer place-items-center rounded-full bg-gradient-to-br from-[#ff8a1e] to-[#e85d7a] text-white shadow-[0_14px_28px_-12px_rgba(232,93,122,0.8)] transition-transform hover:scale-110 active:scale-95"
              title="Shortlist"
            >
              <Bookmark size={18} strokeWidth={2.5} />
            </button>
          </div>
        )}

        {saved.length > 0 && (
          <div className="mt-2.5 flex flex-wrap gap-1.5">
            {saved.map((t) => (
              <button
                key={t.id}
                type="button"
                disabled={!actionable(t.cta)}
                onClick={() => fireAction(t.cta)}
                title={t.cta ? `${t.cta.label} — ${t.title}` : t.title}
                className={cn(
                  'inline-flex max-w-full items-center gap-1.5 rounded-full border border-[#ece3d6] bg-white px-2.5 py-1.5 text-[11px] font-bold text-[#221b2e] shadow-sm transition-all',
                  actionable(t.cta) ? 'cursor-pointer hover:-translate-y-0.5 hover:border-[#b9aa93] hover:shadow-md' : 'opacity-60',
                )}
              >
                <span
                  className="h-1.5 w-1.5 shrink-0 rounded-full"
                  style={{ backgroundColor: themeFor(t.provider).brandHex }}
                />
                <span className="truncate">{t.title}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

/** Deck-first wrapper with a toggle back to the classic grid rendering. */
export const SwipeDeckTray: React.FC<{
  group: DeckGroup;
  classic: React.ReactNode;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ group, classic, onQuickPrompt, disabled }) => {
  const [view, setView] = useState<'deck' | 'grid'>('grid');

  if (view === 'grid') {
    return (
      <div className="w-full max-w-full">
        <div className="flex justify-end px-1">
          <button
            type="button"
            onClick={() => setView('deck')}
            className="inline-flex cursor-pointer items-center gap-1 rounded-full border border-[#ece3d6] bg-white px-2.5 py-1 text-[10px] font-bold text-slate-500 transition-colors hover:border-[#b9aa93] hover:text-slate-800"
            title="Back to swipe deck"
          >
            <Bookmark size={11} />
            Deck
          </button>
        </div>
        {classic}
      </div>
    );
  }

  return <SwipeDeck group={group} onQuickPrompt={onQuickPrompt} disabled={disabled} onShowGrid={() => setView('grid')} />;
};
