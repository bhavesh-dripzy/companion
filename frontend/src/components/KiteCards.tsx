// Kite (Zerodha trading) chat card components.
//
// Mirrors InstamartCards.tsx / ZeptoCards.tsx in spirit — every action
// button emits an LLM-friendly quick-prompt through ``onQuickPrompt`` and
// NEVER calls an API directly — but the surface is a brokerage account, so
// the cards are flat financial tables instead of product grids:
//   * `kite_instrument_cards`  → KiteInstrumentGrid (symbol + LTP tiles;
//     tapping a tile asks for details on that symbol).
//   * `kite_holdings_card`     → KiteHoldingsCard (demat holdings table,
//     P&L coloured green/red).
//   * `kite_positions_card`    → KitePositionsCard (net/day open positions).
//   * `kite_open_orders_card`  → KiteOpenOrdersCard (pending orders with a
//     status badge + per-row Modify / Cancel prompts).
//   * `kite_margin_card`       → KiteMarginCard (available funds summary).
//   * `kite_order_ticket_card` → KiteOrderTicketCard — THE SAFETY-CRITICAL
//     card. Renders EVERY order field explicitly with no silent defaults
//     and two clearly distinct CTAs (Confirm vs Edit) plus Cancel, so the
//     user reviews the exact real-money order before it is placed.
//
// Theme is Zerodha blue (#387ED1) — distinct from every grocery / food
// provider so trading turns stay obvious in the chat scroll.

import React from 'react';
import {
  TrendingUp,
  Sparkles,
  Wallet,
  ListOrdered,
  Briefcase,
  ShieldCheck,
  Pencil,
  X as XIcon,
  CheckCircle2,
  AlertTriangle,
} from 'lucide-react';
import type {
  Attachment,
  KiteHoldings,
  KiteHoldingRow,
  KiteInstrumentCardEntry,
  KiteMargin,
  KiteOpenOrder,
  KiteOrderTicket,
  KitePositionRow,
  KitePositions,
} from '../types';
import { cn } from '../utils';
import { themeFor } from '../ChatLog';

// ---------------------------------------------------------------------------
// Formatting helpers. Every numeric is guarded — the backend coerces
// missing / malformed values to null, so we render an em-dash placeholder
// rather than "₹NaN" or "undefined".
// ---------------------------------------------------------------------------
const DASH = '—';

const formatInr = (value: number | null | undefined): string => {
  if (typeof value !== 'number' || !Number.isFinite(value)) return DASH;
  // Indian-grouped rupee amount with 2 decimals for sub-rupee precision
  // where it matters (average prices, P&L), rounded display for big totals.
  return `₹${value.toLocaleString('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
};

const formatQty = (value: number | null | undefined): string => {
  if (typeof value !== 'number' || !Number.isFinite(value)) return DASH;
  return value.toLocaleString('en-IN');
};

const formatPct = (value: number | null | undefined): string | null => {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}%`;
};

// Tailwind class for a signed money value — green positive, red negative,
// slate for zero / unknown.
const pnlColor = (value: number | null | undefined): string => {
  if (typeof value !== 'number' || !Number.isFinite(value) || value === 0) {
    return 'text-slate-600';
  }
  return value > 0 ? 'text-emerald-600' : 'text-red-600';
};

const formatSignedInr = (value: number | null | undefined): string => {
  if (typeof value !== 'number' || !Number.isFinite(value)) return DASH;
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  const abs = Math.abs(value);
  return `${sign}₹${abs.toLocaleString('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
};

// Shared header for every Kite card — brand pill + label + optional count.
const KiteCardHeader: React.FC<{
  icon: React.ReactNode;
  title: string;
  subtitle?: string;
}> = ({ icon, title, subtitle }) => {
  const theme = themeFor('kite');
  return (
    <div className="flex items-center gap-2 px-1 mb-2">
      <div
        className="flex items-center gap-1.5 px-2 py-0.5 rounded-md text-white text-[10px] font-bold tracking-wider uppercase"
        style={{ backgroundColor: theme.brandHex }}
      >
        {icon}
        <span>{theme.label}</span>
      </div>
      <span className="text-xs font-bold uppercase tracking-wider text-slate-500 truncate">{title}</span>
      {subtitle && (
        <span className="text-[10px] text-slate-400 ml-1 truncate">{subtitle}</span>
      )}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Instrument tiles. Each shows symbol + exchange + LTP + (when present) a
// freshness hint. Tapping a tile asks the agent for details on that symbol
// so the user can drill in without typing the ticker by hand.
// ---------------------------------------------------------------------------
const KiteInstrumentTile: React.FC<{
  item: KiteInstrumentCardEntry;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ item, onQuickPrompt, disabled }) => {
  const theme = themeFor('kite');
  const ltp = formatInr(item.last_price);
  const sym = (item.tradingsymbol || '').trim() || 'Instrument';

  const handleTap = () => {
    if (!onQuickPrompt || disabled) return;
    onQuickPrompt(`Show details for ${sym}.`);
  };

  return (
    <button
      type="button"
      onClick={handleTap}
      disabled={disabled || !onQuickPrompt}
      className={cn(
        'flex flex-col w-[170px] shrink-0 snap-start bg-white rounded-2xl border border-slate-200/80 overflow-hidden text-left transition-all duration-200',
        'hover:shadow-md hover:-translate-y-0.5',
        'disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:translate-y-0 disabled:hover:shadow-none',
      )}
      title={disabled ? 'Waiting for current reply' : `See details for ${sym}`}
    >
      <div
        className="px-3 py-2 flex items-center justify-between"
        style={{ background: `linear-gradient(135deg, ${theme.brandHex}1a, transparent)` }}
      >
        <span className="text-[12.5px] font-extrabold text-slate-900 truncate">{sym}</span>
        {item.exchange && (
          <span
            className="text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded text-white shrink-0"
            style={{ backgroundColor: theme.brandHex }}
          >
            {item.exchange}
          </span>
        )}
      </div>
      <div className="flex flex-col flex-1 px-3 pt-1.5 pb-2.5 gap-0.5">
        {item.name && (
          <p className="text-[10px] text-slate-400 leading-snug line-clamp-2 min-h-[26px]">
            {item.name}
          </p>
        )}
        <div className="mt-auto">
          <span className="text-[10px] uppercase font-bold tracking-wider text-slate-400">LTP</span>
          <p className="text-[14px] font-extrabold text-slate-900 leading-tight">{ltp}</p>
        </div>
      </div>
    </button>
  );
};

export const KiteInstrumentGrid: React.FC<{
  attachment: Attachment;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ attachment, onQuickPrompt, disabled }) => {
  const instruments = Array.isArray(attachment.instruments) ? attachment.instruments : [];
  if (instruments.length === 0) return null;

  return (
    <div className="mt-2 w-full max-w-full">
      <KiteCardHeader
        icon={<Sparkles size={11} />}
        title="Instruments"
        subtitle={`${instruments.length} result${instruments.length === 1 ? '' : 's'}`}
      />
      <div className="flex flex-row gap-2.5 overflow-x-auto snap-x snap-mandatory pb-2 -mx-1 px-1 [scrollbar-width:thin] [&::-webkit-scrollbar]:h-1.5 [&::-webkit-scrollbar-thumb]:bg-slate-300 [&::-webkit-scrollbar-thumb]:rounded-full">
        {instruments.map((inst, idx) => (
          <KiteInstrumentTile
            key={`${inst.exchange || ''}:${inst.tradingsymbol || idx}`}
            item={inst}
            onQuickPrompt={onQuickPrompt}
            disabled={disabled}
          />
        ))}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Holdings table. One row per demat holding with P&L coloured green / red.
// ---------------------------------------------------------------------------
const KiteHoldingRowView: React.FC<{ row: KiteHoldingRow }> = ({ row }) => {
  const sym = (row.tradingsymbol || '').trim() || DASH;
  const pct = formatPct(row.day_change_percentage);
  return (
    <tr className="border-b border-slate-100 last:border-b-0">
      <td className="py-2 px-3 text-left">
        <div className="text-[12px] font-bold text-slate-800 leading-tight">{sym}</div>
        {row.exchange && (
          <div className="text-[9px] uppercase tracking-wider text-slate-400">{row.exchange}</div>
        )}
      </td>
      <td className="py-2 px-3 text-right text-[11.5px] font-semibold text-slate-700">
        {formatQty(row.quantity)}
      </td>
      <td className="py-2 px-3 text-right text-[11.5px] text-slate-500 hidden sm:table-cell">
        {formatInr(row.average_price)}
      </td>
      <td className="py-2 px-3 text-right text-[11.5px] font-semibold text-slate-800">
        {formatInr(row.last_price)}
      </td>
      <td className={cn('py-2 px-3 text-right text-[11.5px] font-bold', pnlColor(row.pnl))}>
        {formatSignedInr(row.pnl)}
      </td>
      <td className={cn('py-2 px-3 text-right text-[11px] font-semibold hidden sm:table-cell', pnlColor(row.day_change_percentage))}>
        {pct ?? DASH}
      </td>
    </tr>
  );
};

export const KiteHoldingsCard: React.FC<{
  holdings: KiteHoldings;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ holdings }) => {
  const theme = themeFor('kite');
  const rows = Array.isArray(holdings?.holdings) ? holdings.holdings : [];
  const totalPnl = rows.reduce(
    (sum, r) => sum + (typeof r.pnl === 'number' && Number.isFinite(r.pnl) ? r.pnl : 0),
    0,
  );

  return (
    <div className="mt-3 w-full max-w-full md:max-w-lg animate-fade-in">
      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
        <div
          className="flex items-center justify-between px-4 py-2.5"
          style={{
            background: `linear-gradient(to right, ${theme.brandHex}26, transparent)`,
            borderBottomColor: `${theme.brandHex}33`,
            borderBottomWidth: 1,
          }}
        >
          <div className="flex items-center gap-1.5">
            <Briefcase size={14} style={{ color: theme.brandHex }} />
            <span className="text-[11px] font-bold uppercase tracking-wider" style={{ color: theme.brandHex }}>
              Holdings
            </span>
          </div>
          <span className="text-[10px] text-slate-500 font-semibold">
            {rows.length} holding{rows.length === 1 ? '' : 's'}
          </span>
        </div>

        {rows.length === 0 ? (
          <div className="px-4 py-6 text-center">
            <Briefcase size={28} className="mx-auto text-slate-300 mb-2" />
            <p className="text-[13px] font-semibold text-slate-600">No holdings to show.</p>
          </div>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full border-collapse">
                <thead>
                  <tr className="bg-slate-50/70 text-[9.5px] uppercase tracking-wider text-slate-400 font-bold">
                    <th className="py-1.5 px-3 text-left">Symbol</th>
                    <th className="py-1.5 px-3 text-right">Qty</th>
                    <th className="py-1.5 px-3 text-right hidden sm:table-cell">Avg</th>
                    <th className="py-1.5 px-3 text-right">LTP</th>
                    <th className="py-1.5 px-3 text-right">P&amp;L</th>
                    <th className="py-1.5 px-3 text-right hidden sm:table-cell">Day %</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r, idx) => (
                    <KiteHoldingRowView key={`${r.tradingsymbol || idx}`} row={r} />
                  ))}
                </tbody>
              </table>
            </div>
            <div className="px-4 py-2.5 bg-slate-50/60 border-t border-slate-100 flex items-center justify-between">
              <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500">Total P&amp;L</span>
              <span className={cn('text-[13px] font-extrabold', pnlColor(totalPnl))}>
                {formatSignedInr(totalPnl)}
              </span>
            </div>
          </>
        )}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Positions table. Net + day buckets, each a small section.
// ---------------------------------------------------------------------------
const KitePositionRowView: React.FC<{ row: KitePositionRow }> = ({ row }) => {
  const sym = (row.tradingsymbol || '').trim() || DASH;
  return (
    <tr className="border-b border-slate-100 last:border-b-0">
      <td className="py-2 px-3 text-left">
        <div className="text-[12px] font-bold text-slate-800 leading-tight">{sym}</div>
        <div className="text-[9px] uppercase tracking-wider text-slate-400">
          {[row.exchange, row.product].filter(Boolean).join(' · ') || DASH}
        </div>
      </td>
      <td className="py-2 px-3 text-right text-[11.5px] font-semibold text-slate-700">
        {formatQty(row.quantity)}
      </td>
      <td className="py-2 px-3 text-right text-[11.5px] text-slate-500 hidden sm:table-cell">
        {formatInr(row.average_price)}
      </td>
      <td className="py-2 px-3 text-right text-[11.5px] font-semibold text-slate-800">
        {formatInr(row.last_price)}
      </td>
      <td className={cn('py-2 px-3 text-right text-[11.5px] font-bold', pnlColor(row.pnl))}>
        {formatSignedInr(row.pnl)}
      </td>
    </tr>
  );
};

const KitePositionsSection: React.FC<{ label: string; rows: KitePositionRow[] }> = ({ label, rows }) => {
  if (rows.length === 0) return null;
  return (
    <div>
      <div className="px-3 py-1.5 bg-slate-50/70 text-[9.5px] uppercase tracking-wider text-slate-400 font-bold border-b border-slate-100">
        {label} ({rows.length})
      </div>
      <table className="w-full border-collapse">
        <thead>
          <tr className="text-[9.5px] uppercase tracking-wider text-slate-400 font-bold">
            <th className="py-1.5 px-3 text-left">Symbol</th>
            <th className="py-1.5 px-3 text-right">Qty</th>
            <th className="py-1.5 px-3 text-right hidden sm:table-cell">Avg</th>
            <th className="py-1.5 px-3 text-right">LTP</th>
            <th className="py-1.5 px-3 text-right">P&amp;L</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, idx) => (
            <KitePositionRowView key={`${r.tradingsymbol || idx}-${r.product || ''}`} row={r} />
          ))}
        </tbody>
      </table>
    </div>
  );
};

export const KitePositionsCard: React.FC<{
  positions: KitePositions;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ positions }) => {
  const theme = themeFor('kite');
  const net = Array.isArray(positions?.net) ? positions.net : [];
  const day = Array.isArray(positions?.day) ? positions.day : [];
  const isEmpty = net.length === 0 && day.length === 0;

  return (
    <div className="mt-3 w-full max-w-full md:max-w-lg animate-fade-in">
      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
        <div
          className="flex items-center justify-between px-4 py-2.5"
          style={{
            background: `linear-gradient(to right, ${theme.brandHex}26, transparent)`,
            borderBottomColor: `${theme.brandHex}33`,
            borderBottomWidth: 1,
          }}
        >
          <div className="flex items-center gap-1.5">
            <TrendingUp size={14} style={{ color: theme.brandHex }} />
            <span className="text-[11px] font-bold uppercase tracking-wider" style={{ color: theme.brandHex }}>
              Positions
            </span>
          </div>
          <span className="text-[10px] text-slate-500 font-semibold">
            {net.length + day.length} position{net.length + day.length === 1 ? '' : 's'}
          </span>
        </div>

        {isEmpty ? (
          <div className="px-4 py-6 text-center">
            <TrendingUp size={28} className="mx-auto text-slate-300 mb-2" />
            <p className="text-[13px] font-semibold text-slate-600">No open positions.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <KitePositionsSection label="Net" rows={net} />
            <KitePositionsSection label="Day" rows={day} />
          </div>
        )}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Open orders table. Each row has a status badge + Modify / Cancel prompts.
// ---------------------------------------------------------------------------
const statusBadgeClass = (status: string | null | undefined): string => {
  const s = (status || '').trim().toUpperCase();
  if (s === 'COMPLETE') return 'bg-emerald-100 text-emerald-700';
  if (s === 'REJECTED' || s === 'CANCELLED') return 'bg-red-100 text-red-700';
  if (s.includes('OPEN') || s.includes('PENDING') || s.includes('TRIGGER')) return 'bg-amber-100 text-amber-700';
  return 'bg-slate-100 text-slate-600';
};

const sideClass = (side: string | null | undefined): string => {
  const s = (side || '').trim().toUpperCase();
  if (s === 'BUY') return 'text-emerald-700 bg-emerald-50 border-emerald-200';
  if (s === 'SELL') return 'text-red-700 bg-red-50 border-red-200';
  return 'text-slate-600 bg-slate-50 border-slate-200';
};

const KiteOpenOrderRow: React.FC<{
  order: KiteOpenOrder;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ order, onQuickPrompt, disabled }) => {
  const sym = (order.tradingsymbol || '').trim() || DASH;
  const side = (order.transaction_type || '').trim().toUpperCase();
  const idShort = order.order_id ? order.order_id.slice(-6) : '';

  const modify = () => {
    if (!onQuickPrompt || disabled) return;
    onQuickPrompt(`Modify order ${order.order_id} (${side} ${sym}).`);
  };
  const cancel = () => {
    if (!onQuickPrompt || disabled) return;
    onQuickPrompt(`Cancel order ${order.order_id} (${side} ${sym}).`);
  };

  return (
    <div className="flex items-center gap-3 py-2.5 px-4 border-b border-slate-100 last:border-b-0">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          {side && (
            <span className={cn('text-[9px] font-bold uppercase px-1.5 py-0.5 rounded border', sideClass(side))}>
              {side}
            </span>
          )}
          <span className="text-[12.5px] font-bold text-slate-800 truncate">{sym}</span>
          {order.exchange && (
            <span className="text-[9px] uppercase tracking-wider text-slate-400">{order.exchange}</span>
          )}
        </div>
        <p className="text-[11px] text-slate-500 mt-0.5">
          {formatQty(order.quantity)} @ {order.order_type || DASH}
          {typeof order.price === 'number' && order.price > 0 && (
            <span> · {formatInr(order.price)}</span>
          )}
          {order.product && <span className="text-slate-400"> · {order.product}</span>}
          {idShort && <span className="text-slate-400"> · #{idShort}</span>}
        </p>
      </div>
      <div className="flex flex-col items-end gap-1.5 shrink-0">
        {order.status && (
          <span className={cn('text-[9px] font-bold uppercase px-1.5 py-0.5 rounded', statusBadgeClass(order.status))}>
            {order.status}
          </span>
        )}
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={modify}
            disabled={disabled || !onQuickPrompt}
            className={cn(
              'inline-flex items-center gap-1 px-2 py-1 rounded-md text-[10px] font-bold text-slate-600 bg-slate-100 hover:bg-slate-200 transition-colors',
              'disabled:opacity-40 disabled:cursor-not-allowed',
            )}
            title={`Modify order ${order.order_id}`}
          >
            <Pencil size={10} />
            Modify
          </button>
          <button
            type="button"
            onClick={cancel}
            disabled={disabled || !onQuickPrompt}
            className={cn(
              'inline-flex items-center gap-1 px-2 py-1 rounded-md text-[10px] font-bold text-red-600 bg-red-50 hover:bg-red-100 transition-colors',
              'disabled:opacity-40 disabled:cursor-not-allowed',
            )}
            title={`Cancel order ${order.order_id}`}
          >
            <XIcon size={10} />
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
};

export const KiteOpenOrdersCard: React.FC<{
  orders: KiteOpenOrder[];
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ orders, onQuickPrompt, disabled }) => {
  const theme = themeFor('kite');
  const rows = Array.isArray(orders) ? orders : [];

  return (
    <div className="mt-3 w-full max-w-full md:max-w-lg animate-fade-in">
      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
        <div
          className="flex items-center justify-between px-4 py-2.5"
          style={{
            background: `linear-gradient(to right, ${theme.brandHex}26, transparent)`,
            borderBottomColor: `${theme.brandHex}33`,
            borderBottomWidth: 1,
          }}
        >
          <div className="flex items-center gap-1.5">
            <ListOrdered size={14} style={{ color: theme.brandHex }} />
            <span className="text-[11px] font-bold uppercase tracking-wider" style={{ color: theme.brandHex }}>
              Open Orders
            </span>
          </div>
          <span className="text-[10px] text-slate-500 font-semibold">
            {rows.length} order{rows.length === 1 ? '' : 's'}
          </span>
        </div>

        {rows.length === 0 ? (
          <div className="px-4 py-6 text-center">
            <ListOrdered size={28} className="mx-auto text-slate-300 mb-2" />
            <p className="text-[13px] font-semibold text-slate-600">No open orders.</p>
          </div>
        ) : (
          <div>
            {rows.map((o, idx) => (
              <KiteOpenOrderRow
                key={o.order_id || idx}
                order={o}
                onQuickPrompt={onQuickPrompt}
                disabled={disabled}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Margin / funds summary. The backend forwards whatever Kite returns, so we
// dig defensively for available cash and a couple of well-known rows
// (equity / commodity ``available.cash``, ``net``).
// ---------------------------------------------------------------------------
const asRecord = (value: unknown): Record<string, unknown> | null =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null;

const numFrom = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;

// Pull a list of (label, amount) rows from a margin segment ({available:{cash,...}, net, ...}).
const marginRows = (segment: Record<string, unknown> | null): Array<{ label: string; amount: number }> => {
  if (!segment) return [];
  const rows: Array<{ label: string; amount: number }> = [];
  const available = asRecord(segment.available);
  const cash = numFrom(available?.cash);
  if (cash !== null) rows.push({ label: 'Available cash', amount: cash });
  const live = numFrom(available?.live_balance);
  if (live !== null) rows.push({ label: 'Live balance', amount: live });
  const net = numFrom(segment.net);
  if (net !== null) rows.push({ label: 'Net', amount: net });
  const utilised = asRecord(segment.utilised);
  const debits = numFrom(utilised?.debits);
  if (debits !== null) rows.push({ label: 'Utilised', amount: debits });
  return rows;
};

export const KiteMarginCard: React.FC<{
  margin: KiteMargin;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ margin }) => {
  const theme = themeFor('kite');
  const root = asRecord(margin);
  // Kite's get_margins returns {equity:{...}, commodity:{...}}; some MCP
  // framings nest one level deeper under ``data``. Probe both.
  const equity = asRecord(root?.equity);
  const commodity = asRecord(root?.commodity);

  const sections: Array<{ label: string; rows: Array<{ label: string; amount: number }> }> = [];
  if (equity) sections.push({ label: 'Equity', rows: marginRows(equity) });
  if (commodity) sections.push({ label: 'Commodity', rows: marginRows(commodity) });
  // Fallback: treat the whole object as a single segment if neither
  // equity nor commodity was present.
  if (sections.length === 0 && root) {
    const flat = marginRows(root);
    if (flat.length > 0) sections.push({ label: 'Funds', rows: flat });
  }
  const hasAny = sections.some((s) => s.rows.length > 0);

  return (
    <div className="mt-3 w-full max-w-full md:max-w-sm animate-fade-in">
      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
        <div
          className="flex items-center gap-1.5 px-4 py-2.5"
          style={{
            background: `linear-gradient(to right, ${theme.brandHex}26, transparent)`,
            borderBottomColor: `${theme.brandHex}33`,
            borderBottomWidth: 1,
          }}
        >
          <Wallet size={14} style={{ color: theme.brandHex }} />
          <span className="text-[11px] font-bold uppercase tracking-wider" style={{ color: theme.brandHex }}>
            Funds &amp; Margins
          </span>
        </div>

        {!hasAny ? (
          <div className="px-4 py-6 text-center">
            <Wallet size={28} className="mx-auto text-slate-300 mb-2" />
            <p className="text-[13px] font-semibold text-slate-600">No margin details to show.</p>
          </div>
        ) : (
          <div className="px-4 py-3 space-y-3">
            {sections.map((sec) =>
              sec.rows.length === 0 ? null : (
                <div key={sec.label}>
                  <div className="text-[9.5px] font-bold uppercase tracking-wider text-slate-400 mb-1">
                    {sec.label}
                  </div>
                  <div className="space-y-1">
                    {sec.rows.map((r) => (
                      <div key={r.label} className="flex items-center justify-between text-[12px]">
                        <span className="text-slate-500 font-semibold">{r.label}</span>
                        <span className="font-extrabold text-slate-900">{formatInr(r.amount)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ),
            )}
          </div>
        )}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// SAFETY-CRITICAL order ticket. Renders EVERY field explicitly with no
// silent defaults so the user reviews the exact real-money order. Two
// clearly distinct CTAs (Confirm vs Edit) plus a Cancel — all routed
// through onQuickPrompt, never a direct API call. BUY vs SELL coloured
// distinctly so the side is unmistakable.
// ---------------------------------------------------------------------------
const TicketField: React.FC<{
  label: string;
  value: string;
  emphasis?: boolean;
  valueClass?: string;
}> = ({ label, value, emphasis, valueClass }) => (
  <div className="flex items-center justify-between py-1.5 border-b border-slate-100 last:border-b-0">
    <span className="text-[10.5px] uppercase tracking-wider font-bold text-slate-400">{label}</span>
    <span
      className={cn(
        'text-[12.5px] font-bold text-slate-800',
        emphasis && 'text-[13.5px] font-extrabold',
        valueClass,
      )}
    >
      {value}
    </span>
  </div>
);

const ticketStr = (value: unknown): string => {
  if (typeof value === 'string' && value.trim()) return value.trim();
  return DASH;
};

export const KiteOrderTicketCard: React.FC<{
  ticket: KiteOrderTicket;
  onQuickPrompt?: (prompt: string) => void;
  disabled?: boolean;
}> = ({ ticket, onQuickPrompt, disabled }) => {
  const theme = themeFor('kite');
  if (!ticket || typeof ticket !== 'object') return null;

  const side = (ticket.transaction_type || '').toString().trim().toUpperCase();
  const isBuy = side === 'BUY';
  const isSell = side === 'SELL';
  const sym = ticketStr(ticket.tradingsymbol);

  // Distinct side accent — green for BUY, red for SELL, slate when the
  // ticket somehow lacks a side (never silently assume one).
  const sideHex = isBuy ? '#059669' : isSell ? '#DC2626' : '#475569';

  const confirm = () => onQuickPrompt && !disabled && onQuickPrompt('Confirm and place the order.');
  const edit = () => onQuickPrompt && !disabled && onQuickPrompt('I want to change the order');
  const cancel = () => onQuickPrompt && !disabled && onQuickPrompt("Cancel that, don't place anything.");

  return (
    <div className="mt-3 w-full max-w-full md:max-w-md animate-fade-in">
      <div
        className="bg-white rounded-2xl border-2 shadow-md overflow-hidden"
        style={{ borderColor: `${sideHex}55` }}
      >
        {/* Header band — side-coloured so BUY vs SELL is unmistakable. */}
        <div
          className="flex items-center justify-between px-4 py-3 text-white"
          style={{ backgroundColor: sideHex }}
        >
          <div className="flex items-center gap-2">
            <ShieldCheck size={16} />
            <div className="leading-tight">
              <div className="text-[10px] font-mono font-black uppercase tracking-widest opacity-90">
                {theme.cartHeaderTitle} · Order Review
              </div>
              <div className="text-[14px] font-extrabold">
                {side || 'ORDER'} {sym}
              </div>
            </div>
          </div>
          <span className="text-[10px] font-bold uppercase tracking-wider bg-white/20 px-2 py-1 rounded">
            Unconfirmed
          </span>
        </div>

        {/* Every order field rendered explicitly — no silent defaults. */}
        <div className="px-4 py-2">
          <TicketField
            label="Side"
            value={side || DASH}
            emphasis
            valueClass={isBuy ? 'text-emerald-600' : isSell ? 'text-red-600' : 'text-slate-600'}
          />
          <TicketField label="Symbol" value={sym} emphasis />
          <TicketField label="Exchange" value={ticketStr(ticket.exchange)} />
          <TicketField label="Quantity" value={formatQty(ticket.quantity)} emphasis />
          <TicketField label="Order type" value={ticketStr(ticket.order_type)} />
          <TicketField label="Product" value={ticketStr(ticket.product)} />
          <TicketField label="Variety" value={ticketStr(ticket.variety)} />
          <TicketField label="Limit price" value={formatInr(ticket.price)} />
          <TicketField label="Trigger price" value={formatInr(ticket.trigger_price)} />
          <TicketField label="Current LTP" value={formatInr(ticket.last_price)} />
          <TicketField
            label="Est. cost"
            value={formatInr(ticket.estimated_cost)}
            emphasis
            valueClass="text-slate-900"
          />
        </div>

        {/* Warning strip — this is a real-money account. */}
        <div className="mx-4 mb-3 flex items-start gap-2 rounded-lg bg-amber-50 border border-amber-200 px-3 py-2">
          <AlertTriangle size={13} className="text-amber-600 shrink-0 mt-0.5" />
          <span className="text-[10.5px] text-amber-800 leading-snug font-medium">
            Review every detail. Confirming places this order against your real Zerodha account.
          </span>
        </div>

        {/* Two clearly distinct CTAs + Cancel. */}
        <div className="px-4 pb-4 space-y-2">
          <button
            type="button"
            onClick={confirm}
            disabled={disabled || !onQuickPrompt}
            className={cn(
              'w-full inline-flex items-center justify-center gap-1.5 rounded-xl px-3 py-3 text-[13px] font-extrabold uppercase tracking-wider text-white transition-all',
              'hover:brightness-110 active:scale-[0.99]',
              'disabled:cursor-not-allowed disabled:opacity-50',
            )}
            style={{ backgroundColor: sideHex }}
            title={disabled ? 'Waiting for current reply' : 'Confirm and place this order'}
          >
            <CheckCircle2 size={15} />
            <span>Confirm and place order</span>
          </button>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={edit}
              disabled={disabled || !onQuickPrompt}
              className={cn(
                'flex-1 inline-flex items-center justify-center gap-1.5 rounded-xl px-3 py-2.5 text-[12px] font-bold text-slate-700 bg-slate-100 hover:bg-slate-200 transition-colors',
                'disabled:cursor-not-allowed disabled:opacity-50',
              )}
              title="Change something about this order"
            >
              <Pencil size={13} />
              <span>Edit</span>
            </button>
            <button
              type="button"
              onClick={cancel}
              disabled={disabled || !onQuickPrompt}
              className={cn(
                'flex-1 inline-flex items-center justify-center gap-1.5 rounded-xl px-3 py-2.5 text-[12px] font-bold text-red-600 bg-red-50 hover:bg-red-100 transition-colors',
                'disabled:cursor-not-allowed disabled:opacity-50',
              )}
              title="Cancel — don't place anything"
            >
              <XIcon size={13} />
              <span>Cancel</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
