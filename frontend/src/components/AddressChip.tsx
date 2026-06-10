/**
 * Delivery address chip — makes the silently-resolved delivery address
 * visible and switchable BEFORE a food/grocery search runs, instead of the
 * user discovering the wrong house at the cart. Renders above the composer
 * whenever the session carries saved addresses; selecting one threads
 * `address_id` into every subsequent agent request.
 */

import React, { useEffect, useRef, useState } from 'react';
import { ChevronDown, Home, Building2, MapPin, Check } from 'lucide-react';
import type { SessionAddress } from '../types';

interface AddressChipProps {
  addresses: SessionAddress[];
  selectedId?: string | null;
  onSelect: (addr: SessionAddress) => void;
}

export const addressLabel = (addr: SessionAddress): string =>
  String(
    addr.label || addr.addressTag || addr.address_tag || addr.location_name || 'Saved address',
  );

const addressLine = (addr: SessionAddress): string => String(addr.address_line || '');

const iconFor = (label: string) => {
  const l = label.toLowerCase();
  if (l.includes('home') || l.includes('ghar')) return Home;
  if (l.includes('office') || l.includes('work')) return Building2;
  return MapPin;
};

export const AddressChip: React.FC<AddressChipProps> = ({ addresses, selectedId, onSelect }) => {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);

  if (addresses.length === 0) return null;
  const active =
    addresses.find((a) => selectedId && String(a.address_id) === String(selectedId)) || addresses[0];
  const activeLabel = addressLabel(active);

  return (
    <div ref={rootRef} className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1.5 bg-[#1a9d6b]/8 border border-[#1a9d6b]/35 text-[#137a52] rounded-full px-3 py-1.5 text-[11.5px] font-bold hover:bg-[#1a9d6b]/14 transition-colors cursor-pointer"
        title="Delivery address for food & grocery orders"
      >
        <MapPin size={12} strokeWidth={2.5} />
        <span>
          Delivering to {activeLabel}
          {addressLine(active) ? ` — ${addressLine(active).slice(0, 28)}` : ''}
        </span>
        <ChevronDown size={12} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className="absolute bottom-full mb-2 left-0 w-64 bg-white border-[1.5px] border-[#221b2e] rounded-2xl overflow-hidden shadow-[0_18px_40px_-18px_rgba(34,27,46,0.35)] z-30">
          {addresses.slice(0, 6).map((addr) => {
            const label = addressLabel(addr);
            const Icon = iconFor(label);
            const isActive = String(addr.address_id) === String(active.address_id);
            return (
              <button
                key={addr.address_id}
                type="button"
                onClick={() => {
                  onSelect(addr);
                  setOpen(false);
                }}
                className={`w-full flex items-center gap-2.5 px-3.5 py-2.5 text-left border-b border-[#ece3d6] last:border-b-0 transition-colors cursor-pointer ${
                  isActive ? 'bg-[#1a9d6b]/7' : 'hover:bg-[#fdfaf3]'
                }`}
              >
                <Icon size={14} className="text-[#7a7388] shrink-0" />
                <span className="min-w-0">
                  <span className="block text-[12.5px] font-extrabold text-[#221b2e]">{label}</span>
                  {addressLine(addr) && (
                    <span className="block text-[10.5px] text-[#7a7388] truncate">
                      {addressLine(addr)}
                    </span>
                  )}
                </span>
                {isActive && <Check size={14} className="ml-auto text-[#1a9d6b] shrink-0" strokeWidth={3} />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
};
