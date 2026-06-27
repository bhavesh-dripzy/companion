import React, { useState } from 'react';
import { User, Lock, Eye, EyeOff, ArrowRight } from 'lucide-react';
import { api, setToken, setRefreshToken, clearRefreshToken } from './api';
import { prefetchHeroGreeting, rememberLastUser } from './heroGreeting';
import type { AuthResponse } from './types';

interface LoginProps {
  onLoginSuccess: (username: string) => void;
}

export const Login: React.FC<LoginProps> = ({ onLoginSuccess }) => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [rememberMe, setRememberMe] = useState(true);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

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
      rememberLastUser(data.username);
      prefetchHeroGreeting(data.username);
      onLoginSuccess(data.username);
    } catch (err: any) {
      setError(err.message || 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="h-[var(--app-height)] overflow-y-auto overflow-x-hidden flex flex-col items-center justify-center px-6 pt-[calc(1.5rem+var(--safe-top))] pb-[calc(1.5rem+var(--safe-bottom))] relative bg-gradient-to-b from-[#f3eefc] via-[#f6f1fc] to-[#efe7fb]">
      {/* Soft lavender glow blobs */}
      <div className="absolute -top-24 -left-16 w-72 h-72 rounded-full bg-[#caa8f5]/30 blur-3xl pointer-events-none" aria-hidden />
      <div className="absolute -bottom-24 -right-16 w-72 h-72 rounded-full bg-[#f0a8d8]/25 blur-3xl pointer-events-none" aria-hidden />

      {/* Brand header */}
      <div
        className="flex flex-col items-center mb-6 relative z-10 animate-rise-in"
        style={{ animationDelay: '0.05s' }}
      >
        <div className="w-[58px] h-[58px] rounded-[18px] overflow-hidden shadow-[0_14px_32px_-12px_rgba(180,80,200,0.55)] mb-3.5">
          <img src="/omi-logo.png" alt="Companion" className="w-full h-full object-cover" />
        </div>
        <h1 className="font-display text-[22px] font-semibold tracking-tight text-[#241433] flex items-center gap-2">
          Welcome back <span aria-hidden>👋</span>
        </h1>
        <p className="text-[#8b7fa3] text-[13px] mt-1 font-medium">
          Sign in to continue with Companion
        </p>
      </div>

      {/* Card */}
      <div
        className="w-full max-w-[360px] bg-white/85 border border-white/70 backdrop-blur-xl rounded-[20px] shadow-[0_24px_60px_-30px_rgba(80,40,120,0.4)] px-6 py-6 relative z-10 animate-rise-in"
        style={{ animationDelay: '0.14s' }}
      >
        <h2 className="text-[16px] font-bold text-[#241433]">Sign in to your account</h2>
        <p className="text-[#9a8fb0] text-[12.5px] mt-1 mb-5 font-medium">
          Enter your credentials to continue
        </p>

        <form onSubmit={handleSubmit} className="space-y-3.5">
          <div>
            <label className="block text-[12.5px] font-semibold text-[#3a2b4d] mb-1.5">Username</label>
            <div className="relative">
              <User
                size={16}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-[#b0a4c6] pointer-events-none"
              />
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="w-full bg-white border border-[#e7def4] rounded-xl pl-10 pr-4 py-2.5 text-[14px] text-[#241433] placeholder:text-[#b6acc7] focus:outline-none focus:border-[#9b5cf6] focus:ring-2 focus:ring-[#9b5cf6]/15"
                placeholder="Enter your username"
                autoCapitalize="none"
                autoCorrect="off"
                required
              />
            </div>
          </div>

          <div>
            <label className="block text-[12.5px] font-semibold text-[#3a2b4d] mb-1.5">Password</label>
            <div className="relative">
              <Lock
                size={16}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-[#b0a4c6] pointer-events-none"
              />
              <input
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full bg-white border border-[#e7def4] rounded-xl pl-10 pr-10 py-2.5 text-[14px] text-[#241433] placeholder:text-[#b6acc7] focus:outline-none focus:border-[#9b5cf6] focus:ring-2 focus:ring-[#9b5cf6]/15"
                placeholder="Enter your password"
                required
              />
              <button
                type="button"
                onClick={() => setShowPassword((v) => !v)}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[#b0a4c6] hover:text-[#7a6e93] p-1"
                aria-label={showPassword ? 'Hide password' : 'Show password'}
              >
                {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </div>

          <div className="flex items-center justify-between pt-0.5">
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={rememberMe}
                onChange={(e) => setRememberMe(e.target.checked)}
                className="w-3.5 h-3.5 rounded accent-[#9b5cf6] cursor-pointer"
              />
              <span className="text-[12.5px] font-medium text-[#5a4f6e]">Remember me</span>
            </label>
            <a
              href="#"
              onClick={(e) => e.preventDefault()}
              className="text-[12.5px] font-semibold text-[#b14fd8] hover:text-[#9b5cf6]"
            >
              Forgot password?
            </a>
          </div>

          {error && (
            <div className="p-2.5 bg-[#e85d7a]/10 border border-[#e85d7a]/30 rounded-xl text-[#c23e5b] text-[13px] text-center">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="btn-ink w-full font-bold py-3 text-[14px] rounded-xl flex items-center justify-center gap-2 disabled:opacity-50 mt-1"
          >
            {loading ? 'Signing in…' : 'Sign In'}
            {!loading && <ArrowRight size={16} />}
          </button>
        </form>
      </div>
    </div>
  );
};
