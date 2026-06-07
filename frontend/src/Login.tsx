import React, { useState } from 'react';
import { Sparkles } from 'lucide-react';
import { api, setToken } from './api';
import type { AuthResponse } from './types';

interface LoginProps {
  onLoginSuccess: (username: string) => void;
}

export const Login: React.FC<LoginProps> = ({ onLoginSuccess }) => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      const data: AuthResponse = await api.post('/api/login/', { username, password });
      setToken(data.access_token);
      onLoginSuccess(data.username);
    } catch (err: any) {
      setError(err.message || 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-6 relative overflow-hidden">
      {/* Faint concentric mandala behind the whole login moment */}
      <div className="mandala-accent absolute inset-0 pointer-events-none" aria-hidden />

      <div
        className="spectrum-ring gateway-arch w-full max-w-md bg-white/70 backdrop-blur-xl rounded-3xl shadow-2xl shadow-[#2a2150]/10 px-8 pb-9 pt-12 relative animate-rise-in"
        style={{ animationDelay: '0.05s' }}
      >
        <div
          className="flex flex-col items-center mb-9 animate-rise-in"
          style={{ animationDelay: '0.12s' }}
        >
          <div className="relative w-16 h-16 mb-5 flex items-center justify-center">
            {/* Brand orb — drifting blue→violet→orange glow */}
            <div className="absolute inset-0 rounded-full brand-gradient animate-orb-glow" aria-hidden />
            <div className="relative w-14 h-14 rounded-full brand-gradient flex items-center justify-center text-white shadow-lg shadow-[#3a5bef]/30">
              <div className="w-7 h-7 flex items-center justify-center border-2 border-dashed border-white/80 rounded-full animate-spin-slow">
                <Sparkles size={14} className="text-white" />
              </div>
            </div>
          </div>
          <h1 className="font-display brand-gradient-text text-4xl font-semibold tracking-tight leading-none">
            Companion
          </h1>
          <p className="text-[#6d6680] text-sm mt-2.5 font-medium">Sign in to your workspace</p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="space-y-4 animate-rise-in"
          style={{ animationDelay: '0.2s' }}
        >
          <div>
            <label className="block text-xs font-semibold text-[#1a1626] mb-2 uppercase tracking-wider">
              Username
            </label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full bg-white/80 border border-[#e8e2d9] rounded-xl px-4 py-3 text-[#1a1626] placeholder:text-[#9a93a8] focus:outline-none focus:border-[#8b5cf6] focus:ring-2 focus:ring-[#8b5cf6]/15"
              placeholder="admin"
              required
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-[#1a1626] mb-2 uppercase tracking-wider">
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full bg-white/80 border border-[#e8e2d9] rounded-xl px-4 py-3 text-[#1a1626] placeholder:text-[#9a93a8] focus:outline-none focus:border-[#8b5cf6] focus:ring-2 focus:ring-[#8b5cf6]/15"
              placeholder="••••••••"
              required
            />
          </div>

          {error && (
            <div className="p-3 bg-[#ff6a3d]/10 border border-[#ff6a3d]/30 rounded-xl text-[#c2401f] text-sm text-center">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="btn-spectrum w-full font-bold py-3 rounded-xl disabled:opacity-50"
          >
            {loading ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  );
};
