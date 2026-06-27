import { useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import type { TurnStatus } from '../types';

/**
 * The live "working" indicator shown while a streamed turn has caught up and is waiting for the next
 * content (e.g. the MCP call to land). Unlike a bare spinner, it states WHAT the assistant is doing —
 * derived from the backend `status` event (action + provider) — with a cycling sub-line, so the wait
 * reads as purposeful: "Updating quantity on Swiggy Instamart · Talking to Swiggy Instamart…".
 */
const PROVIDER_LABEL: Record<string, string> = {
  instamart: 'Swiggy Instamart',
  zepto: 'Zepto',
  swiggy_food: 'Swiggy',
  zomato: 'Zomato',
};

function copy(status?: TurnStatus | null): { title: string; lines: string[] } {
  const prov = status?.provider ? PROVIDER_LABEL[status.provider] : '';
  const on = prov ? ` on ${prov}` : '';
  const talking = prov ? `Talking to ${prov}…` : 'Working on it…';
  switch (status?.action) {
    case 'search':
      return {
        title: prov ? `Looking for the best options on ${prov}` : 'Looking for the best options',
        lines: ['Comparing price, delivery time & availability', 'Finding the best match for you…'],
      };
    case 'add':
      return { title: `Adding to your cart${on}`, lines: [talking, 'Almost there…'] };
    case 'set_qty':
      return { title: `Updating the quantity${on}`, lines: [talking, 'Almost there…'] };
    case 'remove':
      return { title: `Removing from your cart${on}`, lines: [talking, 'Almost there…'] };
    case 'show_cart':
      return { title: `Loading your cart${on}`, lines: [talking, 'Fetching the latest prices…'] };
    case 'clear':
      return { title: `Clearing your cart${on}`, lines: [talking] };
    case 'checkout':
      return { title: `Preparing checkout${on}`, lines: [talking, 'Confirming availability…'] };
    default:
      return { title: 'Working on it…', lines: ['Just a moment…'] };
  }
}

export function WorkingCard({ status }: { status?: TurnStatus | null }) {
  const { title, lines } = copy(status);
  const [i, setI] = useState(0);

  useEffect(() => {
    setI(0);
    if (lines.length < 2) return;
    const id = setInterval(() => setI((p) => (p + 1) % lines.length), 1800);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [title, lines.length]);

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.28, ease: 'easeOut' }}
      className="flex items-center gap-3 w-fit max-w-full rounded-2xl border border-[#ece3d6]/80 bg-gradient-to-br from-[#f4f0ff] via-white/70 to-white/60 px-4 py-3 shadow-2xs backdrop-blur-sm"
    >
      {/* Animated typing dots (replaces the boxed icon). */}
      <span className="flex items-center gap-1 shrink-0">
        <span className="w-1.5 h-1.5 rounded-full bg-[#6366f1] animate-bounce [animation-delay:-0.3s]" />
        <span className="w-1.5 h-1.5 rounded-full bg-[#8b5cf6] animate-bounce [animation-delay:-0.15s]" />
        <span className="w-1.5 h-1.5 rounded-full bg-[#a855f7] animate-bounce" />
      </span>
      <div className="min-w-0">
        <div className="text-[13.5px] font-semibold text-[#2b2f72] leading-tight">{title}</div>
        <div className="relative h-4 overflow-hidden">
          <AnimatePresence mode="wait" initial={false}>
            <motion.div
              key={i}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.26, ease: 'easeOut' }}
              className="whitespace-nowrap text-[11.5px] text-slate-400"
            >
              {lines[i]}
            </motion.div>
          </AnimatePresence>
        </div>
      </div>
    </motion.div>
  );
}
