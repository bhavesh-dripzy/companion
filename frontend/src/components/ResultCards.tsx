/**
 * Long-tail result tiles: order history, live tracking, coupons, reservation
 * slots, critic reviews, and the generic info tile (the catch-all renderer —
 * any tool result without a bespoke card lands here instead of vanishing).
 *
 * Visual language mirrors the existing card system: white surface on the
 * ivory canvas, rounded-2xl, brand-tinted headers via themeFor(provider),
 * JetBrains mono for money/codes, 10–11px meta type.
 */

import React from 'react';
import {
  ReceiptText,
  Truck,
  TicketPercent,
  CalendarClock,
  Quote,
  Info,
  RotateCcw,
} from 'lucide-react';
import { themeFor } from '../ChatLog';
import type {
  Attachment,
  CardProvider,
  CouponCardEntry,
  InfoCardPayload,
  OrderCardEntry,
  ReviewCardEntry,
  SlotEntry,
  TrackingSnapshot,
} from '../types';

type QuickPrompt = ((prompt: string) => void) | undefined;

const cn = (...parts: Array<string | false | null | undefined>) =>
  parts.filter(Boolean).join(' ');

const GOOD_STATUS = /deliver|complete|confirm|success|active|placed/i;
const BAD_STATUS = /cancel|fail|reject|refund/i;

const statusTone = (status?: string): string => {
  if (!status) return 'bg-slate-100 text-slate-600';
  if (GOOD_STATUS.test(status)) return 'bg-emerald-50 text-emerald-700';
  if (BAD_STATUS.test(status)) return 'bg-rose-50 text-rose-600';
  return 'bg-amber-50 text-amber-700';
};

const formatTotal = (entry: OrderCardEntry): string | null => {
  if (entry.total_text) return entry.total_text;
  if (typeof entry.total === 'number' && Number.isFinite(entry.total)) {
    return `₹${entry.total.toFixed(entry.total % 1 === 0 ? 0 : 2)}`;
  }
  return null;
};

export const BrandHeader: React.FC<{
  provider?: CardProvider;
  icon: React.ReactNode;
  label: string;
  trailing?: React.ReactNode;
}> = ({ provider, icon, label, trailing }) => {
  const theme = themeFor(provider);
  return (
    <div
      className="flex items-center justify-between px-4 py-2.5"
      style={{
        background: `linear-gradient(to right, ${theme.brandHex}26, transparent)`,
        borderBottomColor: `${theme.brandHex}33`,
        borderBottomWidth: 1,
      }}
    >
      <div className="flex items-center gap-1.5" style={{ color: theme.brandHex }}>
        {icon}
        <span className="text-[11px] font-bold uppercase tracking-wider">{label}</span>
      </div>
      {trailing ? (
        <span className="text-[10px] text-slate-500 font-semibold">{trailing}</span>
      ) : null}
    </div>
  );
};

/* ----------------------------- order history ----------------------------- */

export const OrderCardGrid: React.FC<{
  orders: OrderCardEntry[];
  provider?: CardProvider;
  onQuickPrompt?: QuickPrompt;
  disabled?: boolean;
}> = ({ orders, provider, onQuickPrompt, disabled }) => {
  if (!orders.length) return null;
  return (
    <div className="mt-3 w-full max-w-full animate-fade-in">
      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
        <BrandHeader
          provider={provider}
          icon={<ReceiptText size={14} />}
          label="Past orders"
          trailing={`${orders.length} order${orders.length === 1 ? '' : 's'}`}
        />
        <div className="flex gap-2.5 overflow-x-auto px-3 py-3 [scrollbar-width:thin] [&::-webkit-scrollbar]:h-1.5 [&::-webkit-scrollbar-thumb]:bg-slate-300">
          {orders.map((order, idx) => {
            const total = formatTotal(order);
            return (
              <div
                key={`${order.order_id}-${idx}`}
                className="w-64 shrink-0 rounded-xl border border-slate-200/80 bg-white p-3 hover:shadow-md hover:-translate-y-0.5 transition-all"
              >
                <div className="flex items-start justify-between gap-2">
                  <p className="text-[12.5px] font-extrabold text-slate-900 leading-tight line-clamp-1">
                    {order.place_name || `Order ${order.order_id}`}
                  </p>
                  {order.status ? (
                    <span
                      className={cn(
                        'shrink-0 rounded-full px-2 py-0.5 text-[9px] font-bold uppercase tracking-wide',
                        statusTone(order.status),
                      )}
                    >
                      {order.status}
                    </span>
                  ) : null}
                </div>
                {order.date ? (
                  <p className="mt-0.5 text-[10px] text-slate-500 font-medium">{order.date}</p>
                ) : null}
                {order.items?.length ? (
                  <ul className="mt-2 space-y-0.5">
                    {order.items.map((item, i) => (
                      <li key={i} className="text-[10.5px] text-slate-600 line-clamp-1">
                        {item.quantity ? `${item.quantity} × ` : ''}
                        {item.name}
                      </li>
                    ))}
                  </ul>
                ) : null}
                <div className="mt-2.5 flex items-center justify-between">
                  {total ? (
                    <span className="font-mono text-[12.5px] font-extrabold text-slate-900">
                      {total}
                    </span>
                  ) : (
                    <span />
                  )}
                  {onQuickPrompt && order.place_name ? (
                    <button
                      type="button"
                      disabled={disabled}
                      onClick={() =>
                        onQuickPrompt(`Reorder my last order from ${order.place_name}`)
                      }
                      className="flex items-center gap-1 rounded-full border border-slate-200 px-2 py-1 text-[9.5px] font-bold text-slate-600 hover:bg-slate-50 disabled:opacity-50 transition-colors"
                    >
                      <RotateCcw size={10} />
                      Reorder
                    </button>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

/* ----------------------------- live tracking ----------------------------- */

export const TrackingCardView: React.FC<{
  tracking: TrackingSnapshot;
  provider?: CardProvider;
}> = ({ tracking, provider }) => {
  const theme = themeFor(provider);
  return (
    <div className="mt-3 w-full max-w-full md:max-w-sm animate-fade-in">
      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
        <BrandHeader provider={provider} icon={<Truck size={14} />} label="Order status" />
        <div className="px-4 py-3.5">
          <div className="flex items-center justify-between gap-2">
            <span
              className={cn(
                'rounded-full px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide',
                statusTone(tracking.status || tracking.stage),
              )}
            >
              {tracking.status || tracking.stage}
            </span>
            {tracking.eta ? (
              <span
                className="text-[12px] font-extrabold"
                style={{ color: theme.brandHex }}
              >
                {tracking.eta}
              </span>
            ) : null}
          </div>
          {tracking.stage && tracking.status ? (
            <p className="mt-2 text-[11.5px] text-slate-600 font-medium">{tracking.stage}</p>
          ) : null}
          <div className="mt-2.5 flex items-center justify-between text-[10px] text-slate-500">
            {tracking.place_name ? <span className="font-semibold">{tracking.place_name}</span> : <span />}
            {tracking.order_id ? (
              <span className="font-mono">#{tracking.order_id}</span>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
};

/* -------------------------------- coupons -------------------------------- */

export const CouponCardGrid: React.FC<{
  coupons: CouponCardEntry[];
  provider?: CardProvider;
  onQuickPrompt?: QuickPrompt;
  disabled?: boolean;
}> = ({ coupons, provider, onQuickPrompt, disabled }) => {
  const theme = themeFor(provider);
  if (!coupons.length) return null;
  return (
    <div className="mt-3 w-full max-w-full animate-fade-in">
      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
        <BrandHeader
          provider={provider}
          icon={<TicketPercent size={14} />}
          label="Offers"
          trailing={`${coupons.length} available`}
        />
        <div className="flex gap-2.5 overflow-x-auto px-3 py-3 [scrollbar-width:thin] [&::-webkit-scrollbar]:h-1.5 [&::-webkit-scrollbar-thumb]:bg-slate-300">
          {coupons.map((coupon, idx) => (
            <div
              key={`${coupon.code || coupon.title}-${idx}`}
              className="w-56 shrink-0 rounded-xl border border-dashed bg-white p-3 transition-all hover:shadow-md hover:-translate-y-0.5"
              style={{ borderColor: `${theme.brandHex}66`, backgroundColor: `${theme.brandHex}08` }}
            >
              {coupon.code ? (
                <span
                  className="inline-block rounded-md px-2 py-0.5 font-mono text-[11px] font-extrabold tracking-widest"
                  style={{ backgroundColor: `${theme.brandHex}1a`, color: theme.brandHex }}
                >
                  {coupon.code}
                </span>
              ) : null}
              {coupon.title ? (
                <p className="mt-1.5 text-[12px] font-extrabold text-slate-900 leading-snug line-clamp-2">
                  {coupon.title}
                </p>
              ) : null}
              {coupon.savings_text && coupon.savings_text !== coupon.title ? (
                <p className="mt-0.5 text-[10.5px] font-bold text-emerald-700">{coupon.savings_text}</p>
              ) : null}
              {coupon.description ? (
                <p className="mt-1 text-[10px] text-slate-500 line-clamp-2">{coupon.description}</p>
              ) : null}
              {onQuickPrompt && coupon.code ? (
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => onQuickPrompt(`Apply coupon ${coupon.code} to my cart`)}
                  className="mt-2 w-full rounded-full py-1 text-[10px] font-bold text-white transition-opacity hover:opacity-90 disabled:opacity-50"
                  style={{ backgroundColor: theme.brandHex }}
                >
                  Apply
                </button>
              ) : null}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

/* ---------------------------- reservation slots --------------------------- */

export const SlotCardGrid: React.FC<{
  att: Attachment;
  onQuickPrompt?: QuickPrompt;
  disabled?: boolean;
}> = ({ att, onQuickPrompt, disabled }) => {
  const theme = themeFor(att.provider);
  const slots = att.slots ?? [];
  if (!slots.length) return null;
  const meta = [
    att.booking_date,
    att.party_size ? `Party of ${att.party_size}` : null,
  ].filter(Boolean);
  return (
    <div className="mt-3 w-full max-w-full md:max-w-lg animate-fade-in">
      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
        <BrandHeader
          provider={att.provider}
          icon={<CalendarClock size={14} />}
          label={att.res_name ? `Slots — ${att.res_name}` : 'Available slots'}
          trailing={meta.join(' · ')}
        />
        <div className="flex flex-wrap gap-2 px-4 py-3.5">
          {slots.map((slot: SlotEntry, idx: number) => {
            const free = slot.available !== false;
            const prompt = att.res_name
              ? `Book the ${slot.time} slot at ${att.res_name}${att.party_size ? ` for ${att.party_size}` : ''}`
              : `Book the ${slot.time} slot`;
            return (
              <button
                key={`${slot.time}-${idx}`}
                type="button"
                disabled={disabled || !free || !onQuickPrompt}
                onClick={() => onQuickPrompt?.(prompt)}
                className={cn(
                  'rounded-full border px-3 py-1.5 text-[11px] font-bold transition-all',
                  free
                    ? 'border-slate-200 text-slate-700 hover:-translate-y-0.5 hover:shadow-sm'
                    : 'border-slate-100 text-slate-300 line-through',
                )}
                style={free ? { backgroundColor: `${theme.brandHex}0d` } : undefined}
              >
                {slot.time}
                {slot.label ? (
                  <span className="ml-1 text-[9px] font-semibold text-slate-400">{slot.label}</span>
                ) : null}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
};

/* ------------------------------ critic reviews ---------------------------- */

export const ReviewCardGrid: React.FC<{
  reviews: ReviewCardEntry[];
  provider?: CardProvider;
}> = ({ reviews, provider }) => {
  const theme = themeFor(provider);
  if (!reviews.length) return null;
  return (
    <div className="mt-3 w-full max-w-full animate-fade-in">
      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
        <BrandHeader
          provider={provider}
          icon={<Quote size={14} />}
          label="Critic reviews"
          trailing={`${reviews.length} review${reviews.length === 1 ? '' : 's'}`}
        />
        <div className="flex gap-2.5 overflow-x-auto px-3 py-3 [scrollbar-width:thin] [&::-webkit-scrollbar]:h-1.5 [&::-webkit-scrollbar-thumb]:bg-slate-300">
          {reviews.map((review, idx) => (
            <div
              key={idx}
              className="w-64 shrink-0 rounded-xl border border-slate-200/80 bg-white p-3"
            >
              <div className="flex items-center justify-between gap-2">
                <p className="text-[11px] font-extrabold text-slate-800 line-clamp-1">
                  {review.publication || review.critic || 'Review'}
                </p>
                {review.rating ? (
                  <span
                    className="shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-extrabold text-white"
                    style={{ backgroundColor: theme.brandHex }}
                  >
                    {review.rating}
                  </span>
                ) : null}
              </div>
              {review.critic && review.publication ? (
                <p className="mt-0.5 text-[9.5px] text-slate-400 font-semibold">{review.critic}</p>
              ) : null}
              {review.snippet ? (
                <p className="mt-1.5 text-[10.5px] leading-relaxed text-slate-600 line-clamp-4">
                  “{review.snippet}”
                </p>
              ) : null}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

/* ------------------------------ generic info ------------------------------ */

export const InfoCardView: React.FC<{
  info: InfoCardPayload;
  provider?: CardProvider;
}> = ({ info, provider }) => {
  if (!info.rows.length && !info.lines?.length) return null;
  return (
    <div className="mt-3 w-full max-w-full md:max-w-sm animate-fade-in">
      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
        <BrandHeader provider={provider} icon={<Info size={14} />} label={info.title} />
        <div className="px-4 py-3">
          {info.rows.length ? (
            <dl className="space-y-1.5">
              {info.rows.map((row, idx) => (
                <div key={idx} className="flex items-baseline justify-between gap-3">
                  <dt className="text-[10px] font-bold uppercase tracking-wide text-slate-400 shrink-0">
                    {row.label}
                  </dt>
                  <dd className="text-[11.5px] font-semibold text-slate-800 text-right break-words">
                    {row.value}
                  </dd>
                </div>
              ))}
            </dl>
          ) : null}
          {info.lines?.length ? (
            <ul
              className={cn(
                'space-y-0.5',
                info.rows.length > 0 && 'mt-2 border-t border-slate-100 pt-2',
              )}
            >
              {info.lines.map((line, idx) => (
                <li key={idx} className="text-[10.5px] text-slate-600 whitespace-pre-wrap">
                  {line}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      </div>
    </div>
  );
};
