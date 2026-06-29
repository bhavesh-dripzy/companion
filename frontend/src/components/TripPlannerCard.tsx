import React, { useState, useRef, useEffect } from 'react';
import { Plane, ArrowRight, ArrowLeftRight, Calendar, ChevronDown } from 'lucide-react';

// In-chat trip planner (the "Plan a Trip" chip). Flights tab only for now (My Trips / Offers are
// shown for layout but inert). Filling it + Search Flights fires a normal flights query via
// onSubmit → handleSendMessage, which routes to the flights domain. No new backend.

type Airport = { code: string; city: string };

// Only the airports our flights search reliably resolves (matches the flights_brain IATA map).
const AIRPORTS: Airport[] = [
  { code: 'DEL', city: 'New Delhi' },
  { code: 'BOM', city: 'Mumbai' },
  { code: 'BLR', city: 'Bengaluru' },
  { code: 'GOI', city: 'Goa' },
  { code: 'HYD', city: 'Hyderabad' },
  { code: 'MAA', city: 'Chennai' },
  { code: 'CCU', city: 'Kolkata' },
  { code: 'PNQ', city: 'Pune' },
  { code: 'JAI', city: 'Jaipur' },
  { code: 'AMD', city: 'Ahmedabad' },
  { code: 'LKO', city: 'Lucknow' },
];

const POPULAR = [
  { from: { code: 'DEL', city: 'New Delhi' }, to: { code: 'BOM', city: 'Mumbai' }, price: '2,499', bg: 'bg-[#ece9fb]', accent: 'text-[#5a4fb0]' },
  { from: { code: 'DEL', city: 'New Delhi' }, to: { code: 'BLR', city: 'Bengaluru' }, price: '2,199', bg: 'bg-[#e3f3ec]', accent: 'text-[#1a9d6b]' },
  { from: { code: 'DEL', city: 'New Delhi' }, to: { code: 'HYD', city: 'Hyderabad' }, price: '2,099', bg: 'bg-[#fdeee4]', accent: 'text-[#d2691e]' },
];

const today = () => new Date().toISOString().slice(0, 10);

// "2026-06-26" → "26 Jun, Thu"
const fmtDate = (s: string): string => {
  if (!s) return '';
  const d = new Date(s + 'T00:00:00');
  if (Number.isNaN(d.getTime())) return s;
  const day = d.getDate();
  const mon = d.toLocaleDateString('en-US', { month: 'short' });
  const wd = d.toLocaleDateString('en-US', { weekday: 'short' });
  return `${day} ${mon}, ${wd}`;
};

const AirportBox: React.FC<{ label: string; value: Airport | null; onClick: () => void; active: boolean }> = ({ label, value, onClick, active }) => (
  <button
    type="button"
    onClick={onClick}
    className={`flex-1 min-w-0 text-left rounded-xl border px-3 py-2.5 transition-colors ${
      active ? 'border-[#5a4fb0] bg-[#5a4fb0]/5' : 'border-slate-200 bg-white hover:border-slate-300'
    }`}
  >
    <span className="block text-[10px] font-medium text-slate-400">{label}</span>
    {value ? (
      <>
        <span className="block text-[18px] font-extrabold text-slate-900 leading-tight">{value.code}</span>
        <span className="block text-[11px] text-slate-500 truncate">{value.city}</span>
      </>
    ) : (
      <span className="block text-[15px] font-bold text-slate-300 mt-0.5">Select</span>
    )}
  </button>
);

const DateBox: React.FC<{
  label: string;
  value: string;
  min: string;
  onChange: (v: string) => void;
  inputRef?: React.RefObject<HTMLInputElement | null>;
}> = ({ label, value, min, onChange, inputRef }) => (
  <div className="relative flex-1 min-w-0 rounded-xl border border-slate-200 bg-white px-3 py-2.5">
    <span className="block text-[10px] font-medium text-slate-400">{label}</span>
    <span className={`block text-[14px] font-bold mt-0.5 ${value ? 'text-slate-900' : 'text-slate-300'}`}>
      {value ? fmtDate(value) : 'Select date'}
    </span>
    <Calendar size={15} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
    <input
      ref={inputRef}
      type="date"
      min={min}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
      aria-label={label}
    />
  </div>
);

export const TripPlannerCard: React.FC<{
  onSubmit: (prompt: string) => void;
  disabled?: boolean;
}> = ({ onSubmit, disabled }) => {
  const [from, setFrom] = useState<Airport | null>({ code: 'DEL', city: 'New Delhi' });
  const [to, setTo] = useState<Airport | null>({ code: 'BOM', city: 'Mumbai' });
  const [picker, setPicker] = useState<'from' | 'to' | null>(null);
  const [query, setQuery] = useState('');
  const [round, setRound] = useState(false);
  const [depart, setDepart] = useState('');
  const [ret, setRet] = useState('');
  const [travOpen, setTravOpen] = useState(false);
  const [travelers, setTravelers] = useState(1);
  // A popular-route tap opens its own date popup (independent of the main form above).
  const [routePrompt, setRoutePrompt] = useState<{ from: Airport; to: Airport } | null>(null);
  const popupDateRef = useRef<HTMLInputElement>(null);

  const ready = from && to && depart && !disabled;

  const buildPrompt = (f: Airport, t: Airport, dep: string) => {
    const parts = [`Flights from ${f.code} to ${t.code} on ${dep}`];
    if (round && ret) parts.push(`returning ${ret}`);
    if (travelers > 1) parts.push(`for ${travelers} adults`);
    return parts.join(' ');
  };

  const pick = (a: Airport) => {
    if (picker === 'from') setFrom(a);
    else if (picker === 'to') setTo(a);
    setPicker(null);
    setQuery('');
  };
  const swap = () => {
    setFrom(to);
    setTo(from);
  };

  const search = () => {
    if (ready) onSubmit(buildPrompt(from!, to!, depart));
  };
  // Popular-route popup → once a date is given, search THAT route (main form untouched).
  const searchRoute = (date: string) => {
    if (!routePrompt || !date || disabled) return;
    const parts = [`Flights from ${routePrompt.from.code} to ${routePrompt.to.code} on ${date}`];
    if (travelers > 1) parts.push(`for ${travelers} adults`);
    onSubmit(parts.join(' '));
    setRoutePrompt(null);
  };

  // Auto-open the date picker as soon as the popup appears.
  useEffect(() => {
    if (!routePrompt) return;
    const t = setTimeout(() => {
      const el = popupDateRef.current as (HTMLInputElement & { showPicker?: () => void }) | null;
      try {
        el?.showPicker?.();
      } catch {
        el?.focus?.();
      }
    }, 60);
    return () => clearTimeout(t);
  }, [routePrompt]);

  const filtered = AIRPORTS.filter(
    (a) =>
      !query ||
      a.code.toLowerCase().includes(query.toLowerCase()) ||
      a.city.toLowerCase().includes(query.toLowerCase()),
  );

  return (
    <div className="w-full max-w-full md:max-w-md rounded-2xl border border-slate-200/80 bg-white shadow-sm overflow-hidden animate-fade-in">
      {/* Flights header */}
      <div className="px-4 pt-3 border-b border-slate-100">
        <span className="inline-flex items-center gap-1.5 pb-2.5 text-[13px] font-semibold text-[#5a4fb0] border-b-2 border-[#5a4fb0]">
          <Plane size={15} /> Flights
        </span>
      </div>

      {/* Search Flights */}
      <div className="m-3 rounded-2xl border border-slate-200/80 bg-slate-50/40 p-3.5">
        <div className="flex items-center gap-2 mb-3">
          <span className="text-[15px] font-bold text-[#221b2e]">Search Flights</span>
          <button
            type="button"
            onClick={() => setRound((r) => !r)}
            className="text-[11px] font-semibold text-[#5a4fb0] bg-[#5a4fb0]/10 px-2.5 py-1 rounded-full"
          >
            {round ? 'Round trip' : 'One way'}
          </button>
          <button
            type="button"
            onClick={swap}
            className="ml-auto w-8 h-8 rounded-lg border border-slate-200 bg-white text-slate-500 flex items-center justify-center hover:border-slate-300 active:scale-95"
            aria-label="Swap"
          >
            <ArrowLeftRight size={15} />
          </button>
        </div>

        <div className="flex items-stretch gap-2">
          <AirportBox label="From" value={from} active={picker === 'from'} onClick={() => setPicker(picker === 'from' ? null : 'from')} />
          <AirportBox label="To" value={to} active={picker === 'to'} onClick={() => setPicker(picker === 'to' ? null : 'to')} />
        </div>

        {/* Airport picker */}
        {picker ? (
          <div className="mt-2 rounded-xl border border-slate-200 bg-white p-2">
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search city or airport"
              className="w-full rounded-lg border border-slate-200 px-2.5 py-1.5 text-[12px] outline-none focus:border-[#5a4fb0]/40 mb-1.5"
            />
            <div className="max-h-40 overflow-y-auto">
              {filtered.map((a) => (
                <button
                  key={a.code}
                  type="button"
                  onClick={() => pick(a)}
                  className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-slate-50 text-left"
                >
                  <span className="text-[12px] font-bold text-slate-800 w-9">{a.code}</span>
                  <span className="text-[12px] text-slate-500">{a.city}</span>
                </button>
              ))}
              {filtered.length === 0 ? (
                <p className="text-[11px] text-slate-400 px-2 py-1.5">No match</p>
              ) : null}
            </div>
          </div>
        ) : null}

        {/* Dates */}
        <div className="flex gap-2 mt-2">
          <DateBox label="Departure" value={depart} min={today()} onChange={setDepart} />
          {round ? <DateBox label="Return" value={ret} min={depart || today()} onChange={setRet} /> : null}
        </div>

        {/* Travelers & Class */}
        <button
          type="button"
          onClick={() => setTravOpen((o) => !o)}
          className="w-full mt-2 flex items-center rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-left"
        >
          <div>
            <span className="block text-[10px] font-medium text-slate-400">Travelers</span>
            <span className="block text-[14px] font-bold text-slate-900 mt-0.5">
              {travelers} Traveler{travelers > 1 ? 's' : ''}
            </span>
          </div>
          <ChevronDown size={16} className={`ml-auto text-slate-400 transition-transform ${travOpen ? 'rotate-180' : ''}`} />
        </button>
        {travOpen ? (
          <div className="mt-2 rounded-xl border border-slate-200 bg-white p-3">
            <div className="flex items-center">
              <span className="text-[12px] font-semibold text-slate-600">Adults</span>
              <div className="ml-auto flex items-center gap-2">
                <button type="button" onClick={() => setTravelers((n) => Math.max(1, n - 1))} className="w-7 h-7 rounded-lg border border-slate-200 text-slate-600 font-bold hover:bg-slate-50 active:scale-95">−</button>
                <span className="w-6 text-center text-[14px] font-bold">{travelers}</span>
                <button type="button" onClick={() => setTravelers((n) => Math.min(9, n + 1))} className="w-7 h-7 rounded-lg border border-slate-200 text-slate-600 font-bold hover:bg-slate-50 active:scale-95">+</button>
              </div>
            </div>
          </div>
        ) : null}

        <button
          type="button"
          onClick={search}
          disabled={!ready}
          className={`w-full mt-3 rounded-xl py-3 text-[14px] font-bold transition-all ${
            ready ? 'bg-[#1a1a2e] text-white hover:bg-[#262640] active:scale-[0.99]' : 'bg-slate-200 text-slate-400 cursor-not-allowed'
          }`}
        >
          Search Flights
        </button>
      </div>

      {/* Popular Routes */}
      <div className="px-4 pb-4">
        <div className="flex items-center mb-2">
          <span className="text-[13px] font-bold text-[#221b2e]">Popular Routes</span>
        </div>
        <div className="flex gap-2 overflow-x-auto no-scrollbar -mx-1 px-1">
          {POPULAR.map((r, i) => (
            <button
              key={i}
              type="button"
              onClick={() => setRoutePrompt({ from: r.from, to: r.to })}
              className={`shrink-0 w-[150px] text-left rounded-xl p-2.5 ${r.bg} active:scale-[0.98] transition-transform`}
            >
              <div className="flex items-center gap-1 text-[12px] font-bold text-slate-800">
                {r.from.code} <ArrowRight size={11} /> {r.to.code}
              </div>
              <div className="text-[9.5px] text-slate-500 leading-tight mt-0.5">
                {r.from.city} → {r.to.city}
              </div>
              <div className="flex items-end justify-between mt-2">
                <div>
                  <span className="block text-[9px] text-slate-400">from</span>
                  <span className={`text-[13px] font-extrabold ${r.accent}`}>₹{r.price}</span>
                </div>
                <Plane size={16} className={`${r.accent} opacity-70`} />
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Popular-route date popup (independent of the main form) */}
      {routePrompt ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          onClick={() => setRoutePrompt(null)}
        >
          <div className="absolute inset-0 bg-black/40" />
          <div
            className="relative w-full max-w-[300px] rounded-2xl bg-white shadow-xl p-4 animate-fade-in"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-2.5">
              <span className="w-8 h-8 rounded-lg bg-[#2b2f72]/10 text-[#2b2f72] flex items-center justify-center shrink-0">
                <Plane size={15} strokeWidth={2.2} />
              </span>
              <div className="min-w-0">
                <p className="text-[14px] font-bold text-slate-900">
                  {routePrompt.from.code} → {routePrompt.to.code}
                </p>
                <p className="text-[11px] text-slate-400 truncate">
                  {routePrompt.from.city} → {routePrompt.to.city}
                </p>
              </div>
            </div>
            <p className="text-[12px] font-semibold text-slate-500 mt-3 mb-1">Kis date jaana hai?</p>
            <input
              ref={popupDateRef}
              type="date"
              min={today()}
              onChange={(e) => searchRoute(e.target.value)}
              className="w-full rounded-xl border border-slate-200 px-3 py-2.5 text-[14px] font-bold text-slate-800 outline-none focus:border-[#2b2f72]/40"
            />
            <button
              type="button"
              onClick={() => setRoutePrompt(null)}
              className="mt-3 w-full text-[12px] font-semibold text-slate-400 hover:text-slate-600"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
};
