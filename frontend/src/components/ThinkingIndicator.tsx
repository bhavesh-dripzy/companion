import { useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';

/**
 * The pre-stream "thinking" status — shown from the moment the user sends a request until the first
 * streamed text lands. It is NOT a static label: it cycles through a short narrative of what the
 * assistant is doing (thinking → understanding → finding the best results), with a soft shimmer on
 * the text and a minimalist breathing-dot indicator, so the wait feels alive and intentional.
 */
const PHASES = [
  'Thinking…',
  'Understanding your request…',
  'Looking for the best results…',
];

const DOT_COLORS = ['#ff8a1e', '#e85d7a', '#2b2f72'];

export function ThinkingIndicator() {
  const [phase, setPhase] = useState(0);

  useEffect(() => {
    // Advance the narrative; stop at the last phase (we stay on "looking for the best results"
    // until the real stream takes over — no jarring loop back to "Thinking").
    const id = setInterval(
      () => setPhase((p) => Math.min(p + 1, PHASES.length - 1)),
      1500,
    );
    return () => clearInterval(id);
  }, []);

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: 'easeOut' }}
      className="flex items-center gap-3 pl-3"
    >
      <div className="w-8 h-8 brand-orb shrink-0" />
      <div className="flex items-center gap-2.5 rounded-full border border-[#ece3d6]/80 bg-white/80 py-2.5 pl-3.5 pr-4 shadow-xs backdrop-blur-xs">
        {/* Minimalist animated icon — three brand dots breathing in a gentle wave. */}
        <span className="flex items-center gap-1">
          {DOT_COLORS.map((c, i) => (
            <motion.span
              key={c}
              className="h-1.5 w-1.5 rounded-full"
              style={{ backgroundColor: c }}
              animate={{ scale: [1, 1.55, 1], opacity: [0.35, 1, 0.35] }}
              transition={{
                duration: 1.1,
                repeat: Infinity,
                ease: 'easeInOut',
                delay: i * 0.18,
              }}
            />
          ))}
        </span>
        {/* Cycling, shimmering status text — soft vertical slide between phases. */}
        <span className="relative inline-flex h-4 items-center overflow-hidden">
          <AnimatePresence mode="wait" initial={false}>
            <motion.span
              key={phase}
              initial={{ opacity: 0, y: 7 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -7 }}
              transition={{ duration: 0.28, ease: 'easeOut' }}
              className="thinking-shimmer whitespace-nowrap text-xs font-medium"
            >
              {PHASES[phase]}
            </motion.span>
          </AnimatePresence>
        </span>
      </div>
    </motion.div>
  );
}
