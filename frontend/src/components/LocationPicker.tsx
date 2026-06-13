import React, { useState } from 'react';
import { MapPin, Loader2, Search } from 'lucide-react';
import { api } from '../api';
import type { GeoCoordinate } from '../types';

interface LocationPickerProps {
  onConfirm: (geo: GeoCoordinate) => void;
}

export function LocationPicker({ onConfirm }: LocationPickerProps) {
  const [state, setState] = useState<'idle' | 'locating' | 'fallback' | 'geocoding'>('idle');
  const [query, setQuery] = useState('');
  const [error, setError] = useState<string | null>(null);

  const handleGrant = () => {
    setError(null);
    setState('locating');
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        onConfirm({ latitude: pos.coords.latitude, longitude: pos.coords.longitude });
      },
      () => {
        // GPS unavailable — show text fallback
        setState('fallback');
        setError('Could not detect your location. Enter your area below.');
      },
      { timeout: 8000, maximumAge: 60000 },
    );
  };

  const handleTextSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const q = query.trim();
    if (!q) return;
    setError(null);
    setState('geocoding');
    try {
      const res = await api.get(`/api/maps/geocode/?q=${encodeURIComponent(q)}`);
      onConfirm({ latitude: res.latitude, longitude: res.longitude });
    } catch {
      setState('fallback');
      setError('Location not found. Try "Koramangala, Bangalore".');
    }
  };

  if (state === 'fallback' || state === 'geocoding') {
    return (
      <div className="mt-3 rounded-2xl border border-[#1a9d6b]/20 bg-[#1a9d6b]/5 p-4 max-w-sm">
        <div className="flex items-center gap-2 mb-3">
          <MapPin size={15} className="text-[#1a9d6b]" />
          <span className="text-[13px] font-semibold text-[#1a9d6b]">Enter your area</span>
        </div>
        <form onSubmit={handleTextSubmit} className="flex gap-2">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="e.g. Koramangala, Bangalore"
            className="flex-1 text-[13px] px-3 py-2 rounded-xl border border-[#ece3d6] bg-white/80 outline-none focus:border-[#1a9d6b]/50 focus:ring-1 focus:ring-[#1a9d6b]/20 text-[#221b2e] placeholder:text-slate-400"
            disabled={state === 'geocoding'}
            autoFocus
          />
          <button
            type="submit"
            disabled={state === 'geocoding' || !query.trim()}
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-[#1a9d6b] text-white text-[13px] font-medium hover:bg-[#168a5c] transition-colors disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
          >
            {state === 'geocoding' ? <Loader2 size={13} className="animate-spin" /> : <Search size={13} />}
            {state === 'geocoding' ? '' : 'Find'}
          </button>
        </form>
        {error && <p className="mt-2 text-[12px] text-rose-500">{error}</p>}
      </div>
    );
  }

  return (
    <button
      onClick={handleGrant}
      disabled={state === 'locating'}
      className="mt-2 flex items-center gap-2 px-4 py-2 rounded-full text-[13px] font-medium bg-[#1a9d6b]/10 text-[#1a9d6b] border border-[#1a9d6b]/20 hover:bg-[#1a9d6b]/20 transition-all cursor-pointer disabled:opacity-60 disabled:cursor-not-allowed"
    >
      {state === 'locating' ? (
        <Loader2 size={14} className="animate-spin" />
      ) : (
        <MapPin size={14} />
      )}
      {state === 'locating' ? 'Getting location…' : 'Share location to see Zepto prices'}
    </button>
  );
}
