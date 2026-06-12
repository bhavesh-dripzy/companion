import React, { useMemo, useState } from 'react';
import { api, setToken, setRefreshToken, clearRefreshToken } from './api';
import { randomFact } from './data/indiaFacts';
import type { AuthResponse } from './types';

interface LoginProps {
  onLoginSuccess: (username: string) => void;
}

export const Login: React.FC<LoginProps> = ({ onLoginSuccess }) => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [rememberMe, setRememberMe] = useState(true);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  // A single heritage fact for this sign-in moment.
  const heritageFact = useMemo(() => randomFact(), []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      const data: AuthResponse = await api.post('/api/login/', {
        username,
        password,
        remember_me: rememberMe,
      });
      setToken(data.access_token);
      if (data.refresh_token) {
        setRefreshToken(data.refresh_token);
      } else {
        clearRefreshToken();
      }
      onLoginSuccess(data.username);
    } catch (err: any) {
      setError(err.message || 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-6 relative overflow-hidden dawn-bloom">
      {/* Faint concentric mandala behind the whole login moment */}
      <div className="mandala-accent absolute inset-0 pointer-events-none" aria-hidden />

      <div
        className="w-full max-w-md bg-white/70 border border-[#ece3d6]/80 backdrop-blur-xl rounded-[22px] shadow-[0_18px_50px_-28px_rgba(60,40,30,0.4)] px-8 pb-9 pt-11 relative animate-rise-in z-10"
        style={{ animationDelay: '0.05s' }}
      >
        <div
          className="flex flex-col items-center mb-8 animate-rise-in"
          style={{ animationDelay: '0.12s' }}
        >
          {/* Brand orb — conic mandala disc */}
          <div className="brand-orb w-14 h-14 mb-5 animate-orb-glow" aria-hidden />

          <div className="text-[11px] font-bold tracking-[0.14em] uppercase text-[#ff8a1e] mb-2">
            ★ Bharat&rsquo;s everything-assistant ★
          </div>
          <h1 className="font-display text-4xl font-semibold tracking-tight leading-[1.05] text-center text-[#221b2e]">
            From Bharat,<br /><span className="brand-gradient-text">For Bharat.</span>
          </h1>
          <p className="text-[#7a7388] text-sm mt-2.5 font-medium">Sign in to your workspace</p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="space-y-4 animate-rise-in"
          style={{ animationDelay: '0.2s' }}
        >
          <div>
            <label className="block text-xs font-semibold text-[#221b2e] mb-2 uppercase tracking-wider">
              Username
            </label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full bg-white/80 border border-[#ece3d6] rounded-xl px-4 py-3 text-[#221b2e] placeholder:text-[#a89f95] focus:outline-none focus:border-[#2b2f72] focus:ring-2 focus:ring-[#2b2f72]/15"
              placeholder="admin"
              required
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-[#221b2e] mb-2 uppercase tracking-wider">
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full bg-white/80 border border-[#ece3d6] rounded-xl px-4 py-3 text-[#221b2e] placeholder:text-[#a89f95] focus:outline-none focus:border-[#2b2f72] focus:ring-2 focus:ring-[#2b2f72]/15"
              placeholder="••••••••"
              required
            />
          </div>

          <label className="flex items-center gap-2.5 cursor-pointer select-none w-fit">
            <input
              type="checkbox"
              checked={rememberMe}
              onChange={(e) => setRememberMe(e.target.checked)}
              className="w-4 h-4 rounded accent-[#2b2f72] cursor-pointer"
            />
            <span className="text-[13px] font-medium text-[#4a4458]">
              Remember me on this device
            </span>
          </label>

          {error && (
            <div className="p-3 bg-[#e85d7a]/10 border border-[#e85d7a]/30 rounded-xl text-[#c23e5b] text-sm text-center">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="btn-ink w-full font-bold py-3 rounded-xl disabled:opacity-50"
          >
            {loading ? 'Signing in…' : 'Sign in'}
          </button>
        </form>

        {/* A small rotating heritage fact for the sign-in moment. */}
        <div className="mt-7 flex justify-center animate-rise-in" style={{ animationDelay: '0.28s' }}>
          <div className="heritage-fact">
            <span>💡</span>
            <span>
              Did you know? <b>{heritageFact}</b>
            </span>
          </div>
        </div>
      </div>
    </div>
  );
};
