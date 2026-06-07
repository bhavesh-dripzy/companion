/**
 * Procedural ORNATE mandala — gold filigree line-art in the style of a
 * traditional Indian mandala (flower-of-life centre, rings of "sun-discs",
 * paisleys, beaded rings, and an outer crown of hatched lotus petals).
 * Inspired by priyanka1706/Mandala-Generation: a motif is drawn once at the
 * top and replicated rotationally around N divisions (radial symmetry).
 * Seeded → deterministic & unique per seed; colours come from the India
 * palette (warm gold primary, with saffron/rose/indigo accents).
 *
 * Purely decorative; no app logic.
 */
import { useMemo } from 'react';

const PALETTE = {
  gold: '#c5872e',
  goldLite: '#d8a24a',
  saffron: '#e08a2a',
  rose: '#c25b78',
  indigo: '#3a3f86',
  green: '#2f8f68',
};

function rng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const pick = <T,>(r: () => number, arr: T[]): T => arr[Math.floor(r() * arr.length)];

type El =
  | { tag: 'path'; d: string; stroke: string; w: number; fill: string; tr?: string }
  | { tag: 'circle'; cx: number; cy: number; r: number; stroke: string; w: number; fill: string; tr?: string }
  | { tag: 'line'; x1: number; y1: number; x2: number; y2: number; stroke: string; w: number; tr?: string };

function build(seed: number, size: number) {
  const r = rng(seed);
  const c = size / 2;
  const Rmax = size * 0.48;
  const els: El[] = [];
  const N = pick(r, [8, 10, 12, 16]); // primary symmetry
  const ringGold = [PALETTE.gold, PALETTE.goldLite, PALETTE.saffron];
  const accent = pick(r, [PALETTE.rose, PALETTE.indigo, PALETTE.green]);
  const rot = (i: number, n: number) => `rotate(${(i * 360) / n} ${c} ${c})`;

  // a pointed lotus petal whose base sits at (c,y) and tip points outward to (c,y-h)
  const lotus = (y: number, w: number, h: number) =>
    `M ${c - w} ${y} Q ${c - w * 0.85} ${y - h * 0.62} ${c} ${y - h} Q ${c + w * 0.85} ${y - h * 0.62} ${c + w} ${y} Q ${c} ${y - h * 0.16} ${c - w} ${y} Z`;
  // a paisley / teardrop
  const drop = (y: number, w: number, h: number) =>
    `M ${c} ${y} C ${c - w} ${y - h * 0.4} ${c - w * 0.55} ${y - h} ${c} ${y - h} C ${c + w * 0.55} ${y - h} ${c + w} ${y - h * 0.4} ${c} ${y} Z`;

  // --- centre: concentric circles + flower-of-life + small star ---
  const cgold = ringGold[0];
  els.push({ tag: 'circle', cx: c, cy: c, r: size * 0.13, stroke: cgold, w: 1.3, fill: 'none' });
  els.push({ tag: 'circle', cx: c, cy: c, r: size * 0.115, stroke: cgold, w: 0.5, fill: 'none' });
  els.push({ tag: 'circle', cx: c, cy: c, r: size * 0.075, stroke: cgold, w: 0.8, fill: 'none' });
  // 6-petal flower-of-life (overlapping vesica circles)
  const fr = size * 0.045;
  for (let i = 0; i < 6; i++) {
    const ang = (i * Math.PI) / 3;
    els.push({ tag: 'circle', cx: c + fr * Math.cos(ang), cy: c + fr * Math.sin(ang), r: fr, stroke: accent, w: 0.7, fill: 'none' });
  }
  els.push({ tag: 'circle', cx: c, cy: c, r: size * 0.012, stroke: cgold, w: 1, fill: cgold });
  // small petal star around the centre
  for (let i = 0; i < N; i++) els.push({ tag: 'path', d: drop(c - size * 0.075, size * 0.012, size * 0.03), stroke: cgold, w: 0.7, fill: 'none', tr: rot(i, N) });

  // --- a "sun-disc" motif (circle ringed with radial teeth) ---
  function sunDisc(radius: number, R: number, col: string, n: number) {
    for (let i = 0; i < n; i++) {
      const tr = rot(i, n);
      const y = c - radius;
      els.push({ tag: 'circle', cx: c, cy: y, r: R, stroke: col, w: 1, fill: 'none', tr });
      els.push({ tag: 'circle', cx: c, cy: y, r: R * 0.55, stroke: col, w: 0.6, fill: 'none', tr });
      els.push({ tag: 'circle', cx: c, cy: y, r: R * 0.16, stroke: col, w: 0.8, fill: col, tr });
      const teeth = 16;
      for (let k = 0; k < teeth; k++) {
        const a = (k * 2 * Math.PI) / teeth;
        els.push({
          tag: 'line',
          x1: c + R * Math.cos(a), y1: y + R * Math.sin(a),
          x2: c + R * 1.28 * Math.cos(a), y2: y + R * 1.28 * Math.sin(a),
          stroke: col, w: 0.6, tr,
        });
      }
    }
  }

  // --- rings (inner → outer) ---
  // beaded ring
  const bead = (radius: number, n: number, col: string) => {
    for (let i = 0; i < n; i++) els.push({ tag: 'circle', cx: c, cy: c - radius, r: size * 0.0045, stroke: col, w: 0.5, fill: col, tr: rot(i, n) });
    els.push({ tag: 'circle', cx: c, cy: c, r: radius, stroke: col, w: 0.4, fill: 'none' });
  };

  bead(size * 0.155, N * 2, ringGold[1]);
  sunDisc(size * 0.205, size * 0.028, ringGold[0], N);          // inner sun-discs
  bead(size * 0.25, N * 3, accent);
  // paisley ring
  for (let i = 0; i < N; i++) els.push({ tag: 'path', d: drop(c - size * 0.30, size * 0.022, size * 0.06), stroke: ringGold[2], w: 0.9, fill: 'none', tr: rot(i, N) });
  els.push({ tag: 'circle', cx: c, cy: c, r: size * 0.30, stroke: ringGold[0], w: 0.5, fill: 'none' });
  sunDisc(size * 0.355, size * 0.034, ringGold[1], N);          // outer sun-discs
  bead(size * 0.40, N * 3, ringGold[0]);

  // --- outer crown: large hatched lotus petals + small petals between + tip beads ---
  const crownR = size * 0.435;
  for (let i = 0; i < N; i++) {
    const tr = rot(i, N);
    const y = c - crownR;
    els.push({ tag: 'path', d: lotus(y + size * 0.06, size * 0.045, size * 0.085), stroke: ringGold[0], w: 1.1, fill: 'none', tr });
    // hatching inside the petal
    for (let k = -2; k <= 2; k++) {
      els.push({ tag: 'line', x1: c + k * size * 0.012, y1: y + size * 0.055, x2: c + k * size * 0.008, y2: y + size * 0.005, stroke: ringGold[1], w: 0.4, tr });
    }
    els.push({ tag: 'circle', cx: c, cy: y - size * 0.005, r: size * 0.006, stroke: ringGold[0], w: 0.6, fill: ringGold[0], tr });
    // small rounded petal between two big ones
    els.push({ tag: 'path', d: drop(c - (crownR - size * 0.02), size * 0.016, size * 0.04), stroke: accent, w: 0.7, fill: 'none', tr: rot(i + 0.5, N) });
  }
  els.push({ tag: 'circle', cx: c, cy: c, r: Rmax, stroke: ringGold[0], w: 0.7, fill: 'none' });
  els.push({ tag: 'circle', cx: c, cy: c, r: Rmax - size * 0.01, stroke: ringGold[1], w: 0.35, fill: 'none' });

  return { els, N };
}

interface MandalaProps {
  seed?: number;
  size?: number;
  className?: string;
  style?: React.CSSProperties;
}

export function Mandala({ seed, size = 600, className, style }: MandalaProps) {
  const { els, N } = useMemo(() => build(seed ?? Math.floor(Math.random() * 1e9), size), [seed, size]);
  return (
    <svg className={className} style={style} width={size} height={size} viewBox={`0 0 ${size} ${size}`} fill="none" aria-hidden="true" data-symmetry={N}>
      {els.map((e, i) =>
        e.tag === 'path' ? (
          <path key={i} d={e.d} stroke={e.stroke} strokeWidth={e.w} fill={e.fill} transform={e.tr} strokeLinecap="round" strokeLinejoin="round" />
        ) : e.tag === 'circle' ? (
          <circle key={i} cx={e.cx} cy={e.cy} r={e.r} stroke={e.stroke} strokeWidth={e.w} fill={e.fill} transform={e.tr} />
        ) : (
          <line key={i} x1={e.x1} y1={e.y1} x2={e.x2} y2={e.y2} stroke={e.stroke} strokeWidth={e.w} transform={e.tr} strokeLinecap="round" />
        ),
      )}
    </svg>
  );
}
