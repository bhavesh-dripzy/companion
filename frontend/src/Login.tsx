import React, { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, Eye, EyeOff, KeyRound, Lock, Mail, User } from 'lucide-react';
import { api, setToken, setRefreshToken, clearRefreshToken } from './api';
import { prefetchHeroGreeting, rememberLastUser } from './heroGreeting';
import type { AuthResponse } from './types';

interface LoginProps {
  onLoginSuccess: (username: string) => void;
}

// Sign-in surface. Primary flow is passwordless email OTP (Cognito USER_AUTH
// + EMAIL_OTP): enter email → 6-digit code lands in the inbox → verified into
// tokens. Password sign-in stays available as a fallback (and is the only
// mode when the backend runs without Cognito, e.g. bare local dev).
type OtpStep = 'email' | 'code';

const RESEND_COOLDOWN_S = 30;

export const Login: React.FC<LoginProps> = ({ onLoginSuccess }) => {
  const [mode, setMode] = useState<'otp' | 'password'>('otp');
  const [otpAvailable, setOtpAvailable] = useState<boolean | null>(null);

  // Shared
  const [rememberMe, setRememberMe] = useState(true);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  // OTP flow
  const [otpStep, setOtpStep] = useState<OtpStep>('email');
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [otpSession, setOtpSession] = useState('');
  const [destination, setDestination] = useState('');
  const [resendIn, setResendIn] = useState(0);
  const codeInputRef = useRef<HTMLInputElement>(null);

  // Password flow
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);

  // OTP requires Cognito — ask the backend once and fall back to the
  // password form when it isn't configured (e.g. legacy local dev).
  useEffect(() => {
    let alive = true;
    api
      .get('/api/auth/cognito/config/')
      .then((cfg) => {
        if (!alive) return;
        const enabled = !!cfg?.enabled;
        setOtpAvailable(enabled);
        if (!enabled) setMode('password');
      })
      .catch(() => {
        if (!alive) return;
        setOtpAvailable(false);
        setMode('password');
      });
    return () => {
      alive = false;
    };
  }, []);

  // Resend cooldown ticker.
  useEffect(() => {
    if (resendIn <= 0) return;
    const t = setTimeout(() => setResendIn((s) => s - 1), 1000);
    return () => clearTimeout(t);
  }, [resendIn]);

  const finishLogin = (data: AuthResponse) => {
    setToken(data.access_token);
    if (data.refresh_token) {
      setRefreshToken(data.refresh_token);
    } else {
      clearRefreshToken();
    }
    rememberLastUser(data.username);
    prefetchHeroGreeting(data.username);
    onLoginSuccess(data.username);
  };

  const sendCode = async () => {
    setError('');
    setLoading(true);
    try {
      const data = await api.post('/api/auth/otp/start/', { email: email.trim() });
      setOtpSession(data.session);
      setDestination(data.destination || '');
      setOtpStep('code');
      setCode('');
      setResendIn(RESEND_COOLDOWN_S);
      setTimeout(() => codeInputRef.current?.focus(), 50);
    } catch (err: any) {
      setError(err.message || 'Could not send the code. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const handleOtpSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (otpStep === 'email') {
      await sendCode();
      return;
    }
    setError('');
    setLoading(true);
    try {
      const data: AuthResponse = await api.post('/api/auth/otp/verify/', {
        username: email.trim(),
        session: otpSession,
        code: code.trim(),
        remember_me: rememberMe,
      });
      finishLogin(data);
    } catch (err: any) {
      setError(err.message || 'Verification failed. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const handlePasswordSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const data: AuthResponse = await api.post('/api/login/', {
        username,
        password,
        remember_me: rememberMe,
      });
      finishLogin(data);
    } catch (err: any) {
      setError(err.message || 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  const switchMode = (next: 'otp' | 'password') => {
    setMode(next);
    setError('');
    setOtpStep('email');
    setCode('');
  };

  return (
    <div className="h-[var(--app-height)] overflow-y-auto overflow-x-hidden flex flex-col items-center justify-center px-6 pt-[calc(1.5rem+var(--safe-top))] pb-[calc(1.5rem+var(--safe-bottom))] relative bg-[#fdfaf3] dawn-bloom">
      {/* Brand header */}
      <div
        className="flex flex-col items-center mb-6 relative z-10 animate-rise-in"
        style={{ animationDelay: '0.05s' }}
      >
        <div className="w-[58px] h-[58px] rounded-[18px] overflow-hidden shadow-[0_14px_32px_-12px_rgba(255,138,30,0.45)] mb-3.5">
          <img src="/omi-logo.png" alt="Companion" className="w-full h-full object-cover" />
        </div>
        <h1 className="font-display text-[22px] font-semibold tracking-tight text-[#221b2e] flex items-center gap-2">
          Welcome back <span aria-hidden>👋</span>
        </h1>
        <p className="text-[#7a7388] text-[13px] mt-1 font-medium">
          Sign in to continue with Companion
        </p>
      </div>

      {/* Card */}
      <div
        className="w-full max-w-[360px] bg-white/85 border border-[#ece3d6]/80 backdrop-blur-xl rounded-[20px] shadow-[0_24px_60px_-30px_rgba(34,27,46,0.35)] px-6 py-6 relative z-10 animate-rise-in"
        style={{ animationDelay: '0.14s' }}
      >
        {mode === 'otp' ? (
          <>
            {otpStep === 'email' ? (
              <>
                <h2 className="text-[16px] font-bold text-[#221b2e]">Sign in with email</h2>
                <p className="text-[#a49db1] text-[12.5px] mt-1 mb-5 font-medium">
                  We'll email you a one-time code — no password needed
                </p>
              </>
            ) : (
              <>
                <h2 className="text-[16px] font-bold text-[#221b2e]">Enter your code</h2>
                <p className="text-[#a49db1] text-[12.5px] mt-1 mb-5 font-medium">
                  Sent to {destination || email}
                </p>
              </>
            )}

            <form onSubmit={handleOtpSubmit} className="space-y-3.5">
              {otpStep === 'email' ? (
                <div>
                  <label className="block text-[12.5px] font-semibold text-[#3d3550] mb-1.5">Email</label>
                  <div className="relative">
                    <Mail
                      size={16}
                      className="absolute left-3 top-1/2 -translate-y-1/2 text-[#a49db1] pointer-events-none"
                    />
                    <input
                      type="email"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      className="w-full bg-white border border-[#ece3d6] rounded-xl pl-10 pr-4 py-2.5 text-[14px] text-[#221b2e] placeholder:text-[#b3aa9e] focus:outline-none focus:border-[#2b2f72] focus:ring-2 focus:ring-[#2b2f72]/15"
                      placeholder="you@example.com"
                      autoCapitalize="none"
                      autoCorrect="off"
                      autoComplete="email"
                      required
                    />
                  </div>
                </div>
              ) : (
                <div>
                  <label className="block text-[12.5px] font-semibold text-[#3d3550] mb-1.5">
                    One-time code
                  </label>
                  <div className="relative">
                    <KeyRound
                      size={16}
                      className="absolute left-3 top-1/2 -translate-y-1/2 text-[#a49db1] pointer-events-none"
                    />
                    <input
                      ref={codeInputRef}
                      type="text"
                      inputMode="numeric"
                      pattern="[0-9]*"
                      maxLength={8}
                      value={code}
                      onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
                      className="w-full bg-white border border-[#ece3d6] rounded-xl pl-10 pr-4 py-2.5 text-[17px] tracking-[0.35em] font-semibold text-[#221b2e] placeholder:text-[#b3aa9e] placeholder:tracking-normal placeholder:font-normal placeholder:text-[14px] focus:outline-none focus:border-[#2b2f72] focus:ring-2 focus:ring-[#2b2f72]/15"
                      placeholder="6-digit code"
                      autoComplete="one-time-code"
                      required
                    />
                  </div>
                  <div className="flex items-center justify-between mt-2">
                    <button
                      type="button"
                      onClick={() => {
                        setOtpStep('email');
                        setError('');
                      }}
                      className="text-[12px] font-semibold text-[#7a7388] hover:text-[#221b2e] inline-flex items-center gap-1"
                    >
                      <ArrowLeft size={12} /> Change email
                    </button>
                    <button
                      type="button"
                      disabled={resendIn > 0 || loading}
                      onClick={sendCode}
                      className="text-[12px] font-semibold text-[#2b2f72] hover:text-[#e85d7a] disabled:text-[#b3aa9e] disabled:cursor-not-allowed"
                    >
                      {resendIn > 0 ? `Resend in ${resendIn}s` : 'Resend code'}
                    </button>
                  </div>
                </div>
              )}

              <label className="flex items-center gap-2 cursor-pointer select-none pt-0.5">
                <input
                  type="checkbox"
                  checked={rememberMe}
                  onChange={(e) => setRememberMe(e.target.checked)}
                  className="w-3.5 h-3.5 rounded accent-[#221b2e] cursor-pointer"
                />
                <span className="text-[12.5px] font-medium text-[#5b5468]">Remember me</span>
              </label>

              {error && (
                <div className="p-2.5 bg-[#e85d7a]/10 border border-[#e85d7a]/30 rounded-xl text-[#c23e5b] text-[13px] text-center">
                  {error}
                </div>
              )}

              <button
                type="submit"
                disabled={loading || (otpStep === 'code' && code.length < 6)}
                className="btn-ink w-full font-bold py-3 text-[14px] rounded-xl flex items-center justify-center gap-2 disabled:opacity-50 mt-1"
              >
                {loading
                  ? otpStep === 'email' ? 'Sending code…' : 'Verifying…'
                  : otpStep === 'email' ? 'Send code' : 'Verify & sign in'}
                {!loading && <ArrowRight size={16} />}
              </button>
            </form>

            <button
              type="button"
              onClick={() => switchMode('password')}
              className="w-full text-center text-[12.5px] font-semibold text-[#7a7388] hover:text-[#221b2e] mt-4"
            >
              Sign in with password instead
            </button>
          </>
        ) : (
          <>
            <h2 className="text-[16px] font-bold text-[#221b2e]">Sign in to your account</h2>
            <p className="text-[#a49db1] text-[12.5px] mt-1 mb-5 font-medium">
              Enter your credentials to continue
            </p>

            <form onSubmit={handlePasswordSubmit} className="space-y-3.5">
              <div>
                <label className="block text-[12.5px] font-semibold text-[#3d3550] mb-1.5">Username</label>
                <div className="relative">
                  <User
                    size={16}
                    className="absolute left-3 top-1/2 -translate-y-1/2 text-[#a49db1] pointer-events-none"
                  />
                  <input
                    type="text"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    className="w-full bg-white border border-[#ece3d6] rounded-xl pl-10 pr-4 py-2.5 text-[14px] text-[#221b2e] placeholder:text-[#b3aa9e] focus:outline-none focus:border-[#2b2f72] focus:ring-2 focus:ring-[#2b2f72]/15"
                    placeholder="Enter your username"
                    autoCapitalize="none"
                    autoCorrect="off"
                    required
                  />
                </div>
              </div>

              <div>
                <label className="block text-[12.5px] font-semibold text-[#3d3550] mb-1.5">Password</label>
                <div className="relative">
                  <Lock
                    size={16}
                    className="absolute left-3 top-1/2 -translate-y-1/2 text-[#a49db1] pointer-events-none"
                  />
                  <input
                    type={showPassword ? 'text' : 'password'}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="w-full bg-white border border-[#ece3d6] rounded-xl pl-10 pr-10 py-2.5 text-[14px] text-[#221b2e] placeholder:text-[#b3aa9e] focus:outline-none focus:border-[#2b2f72] focus:ring-2 focus:ring-[#2b2f72]/15"
                    placeholder="Enter your password"
                    required
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((v) => !v)}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[#a49db1] hover:text-[#5b5468] p-1"
                    aria-label={showPassword ? 'Hide password' : 'Show password'}
                  >
                    {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                  </button>
                </div>
              </div>

              <label className="flex items-center gap-2 cursor-pointer select-none pt-0.5">
                <input
                  type="checkbox"
                  checked={rememberMe}
                  onChange={(e) => setRememberMe(e.target.checked)}
                  className="w-3.5 h-3.5 rounded accent-[#221b2e] cursor-pointer"
                />
                <span className="text-[12.5px] font-medium text-[#5b5468]">Remember me</span>
              </label>

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

            {otpAvailable && (
              <button
                type="button"
                onClick={() => switchMode('otp')}
                className="w-full text-center text-[12.5px] font-semibold text-[#7a7388] hover:text-[#221b2e] mt-4"
              >
                Email me a code instead
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
};
