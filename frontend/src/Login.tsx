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
    <div className="min-h-screen flex items-center justify-center p-6 bg-background">
      <div className="w-full max-w-md bg-surface border border-border rounded-3xl shadow-[0_24px_64px_-24px_rgba(99,102,241,0.35)] p-8">
        <div className="flex flex-col items-center mb-8">
          <div className="w-14 h-14 brand-gradient rounded-2xl flex items-center justify-center text-white mb-4 shadow-lg shadow-violet-500/30">
            <Sparkles size={26} strokeWidth={2.5} />
          </div>
          <h1 className="text-2xl font-bold text-gray-900">Companion</h1>
          <p className="text-gray-500 text-sm mt-1">Sign in to your assistant</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-5">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Username</label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full bg-white border border-border rounded-xl px-4 py-3 text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-4 focus:ring-violet-100 focus:border-violet-300 transition-all"
              placeholder="admin"
              required
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full bg-white border border-border rounded-xl px-4 py-3 text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-4 focus:ring-violet-100 focus:border-violet-300 transition-all"
              placeholder="••••••••"
              required
            />
          </div>

          {error && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-xl text-red-600 text-sm text-center">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full brand-gradient text-white font-semibold py-3 rounded-xl shadow-lg shadow-violet-500/30 transition-all hover:opacity-95 disabled:opacity-50"
          >
            {loading ? 'Signing in...' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  );
};
