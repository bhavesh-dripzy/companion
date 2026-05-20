import React, { useState, useEffect } from 'react';
import { X, Globe, Calendar, Zap, Settings as SettingsIcon, CheckCircle2, AlertCircle, ExternalLink, RefreshCw } from 'lucide-react';
import { api } from './api';
import type { ToolProvider, McpStatus } from './types';
import { cn } from './utils';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  toolProviders: ToolProvider[];
}

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  onClose,
  toolProviders,
}) => {
  const [activeTab, setActiveTab] = useState<'mcp' | 'calendar' | 'models'>('mcp');
  const [selectedProviderId, setSelectedProviderId] = useState('zomato');
  const [mcpStatus, setMcpStatus] = useState<McpStatus | null>(null);
  const [isLoadingMcp, setIsLoadingMcp] = useState(false);
  const [pairingCommand, setPairingCommand] = useState('');
  
  const [gcalStatus, setGcalStatus] = useState<{connected: boolean, configured: boolean} | null>(null);

  useEffect(() => {
    if (isOpen) {
      checkMcpStatus();
      checkGcalStatus();
    }
  }, [isOpen, selectedProviderId]);

  const checkMcpStatus = async () => {
    setIsLoadingMcp(true);
    try {
      const data = await api.get(`/api/mcp/status/?provider=${selectedProviderId}`);
      setMcpStatus(data);
    } catch (err) {
      console.error('Failed to check MCP status', err);
    } finally {
      setIsLoadingMcp(false);
    }
  };

  const checkGcalStatus = async () => {
    try {
      const data = await api.get('/api/google-calendar/status/');
      setGcalStatus(data);
    } catch (err) {
      console.error('Failed to check Google Calendar status', err);
    }
  };

  const handleConnectMcp = async () => {
    try {
      const data = await api.get(`/api/mcp/oauth/start?provider=${selectedProviderId}`);
      if (data.command) {
        setPairingCommand(data.command);
        return;
      }
      if (data.authorization_url) {
        window.location.assign(data.authorization_url);
        return;
      }
      
      const pairing = await api.post(`/api/mcp/pairing/?provider=${selectedProviderId}`);
      if (pairing.command) {
        setPairingCommand(pairing.command);
      }
    } catch (err) {
      console.error('Failed to connect MCP', err);
    }
  };

  const handleConnectGcal = async () => {
    try {
      const data = await api.get('/api/google-calendar/oauth/start');
      if (data.authorization_url) {
        window.open(data.authorization_url, '_blank');
      }
    } catch (err) {
      console.error('Failed to connect Google Calendar', err);
    }
  };

  if (!isOpen) return null;

  const tabs = [
    { id: 'mcp', label: 'MCP Connectors', icon: Globe },
    { id: 'calendar', label: 'Integrations', icon: Calendar },
    { id: 'models', label: 'Preferences', icon: SettingsIcon },
  ];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm">
      <div className="w-full max-w-2xl bg-surface border border-border rounded-3xl shadow-2xl flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-8 py-6 border-b border-border">
          <h2 className="text-xl font-bold flex items-center gap-2">
            <SettingsIcon size={20} className="text-accent" />
            Settings
          </h2>
          <button onClick={onClose} className="p-2 hover:bg-neutral-800 rounded-full transition-colors text-gray-400">
            <X size={20} />
          </button>
        </div>

        <div className="flex flex-1 overflow-hidden">
          {/* Sidebar */}
          <div className="w-48 border-r border-border p-4 space-y-2">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id as any)}
                className={cn(
                  "w-full flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-semibold transition-all",
                  activeTab === tab.id 
                    ? "bg-accent/10 text-accent" 
                    : "text-gray-400 hover:text-white hover:bg-neutral-800"
                )}
              >
                <tab.icon size={18} />
                {tab.label}
              </button>
            ))}
          </div>

          {/* Content */}
          <div className="flex-1 overflow-y-auto p-8">
            {activeTab === 'mcp' && (
              <div className="space-y-8">
                <div>
                  <label className="block text-sm font-bold text-gray-300 mb-4 uppercase tracking-wider">Select Provider</label>
                  <div className="grid grid-cols-2 gap-3">
                    {toolProviders.map((p) => (
                      <button
                        key={p.id}
                        onClick={() => setSelectedProviderId(p.id)}
                        className={cn(
                          "p-4 rounded-2xl border text-left transition-all",
                          selectedProviderId === p.id 
                            ? "border-accent bg-accent/5" 
                            : "border-border hover:border-gray-600 bg-neutral-900/50"
                        )}
                      >
                        <div className="font-bold text-sm mb-1">{p.label}</div>
                        <div className="text-[10px] text-gray-500 uppercase font-black">{p.available ? 'Ready' : 'Setup Required'}</div>
                      </button>
                    ))}
                  </div>
                </div>

                <div className="p-6 bg-neutral-900/50 border border-border rounded-2xl space-y-4">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-bold">Status:</span>
                      {mcpStatus?.status === 'ready' ? (
                        <div className="flex items-center gap-1 text-green-500 text-xs font-bold uppercase">
                          <CheckCircle2 size={14} /> Connected
                        </div>
                      ) : (
                        <div className="flex items-center gap-1 text-amber-500 text-xs font-bold uppercase">
                          <AlertCircle size={14} /> {mcpStatus?.status || 'Unknown'}
                        </div>
                      )}
                    </div>
                    <button 
                      onClick={checkMcpStatus}
                      className="p-2 hover:bg-neutral-800 rounded-lg text-gray-400 transition-colors"
                    >
                      <RefreshCw size={14} className={isLoadingMcp ? 'animate-spin' : ''} />
                    </button>
                  </div>
                  
                  <p className="text-xs text-gray-400 leading-relaxed">
                    {mcpStatus?.detail || 'No status details available.'}
                  </p>

                  <div className="pt-2 flex gap-3">
                    <button 
                      onClick={handleConnectMcp}
                      className="flex-1 bg-accent hover:bg-accent-hover text-white text-sm font-bold py-2.5 rounded-xl transition-all shadow-lg shadow-accent/10"
                    >
                      Connect Account
                    </button>
                    <button className="px-6 border border-border hover:bg-neutral-800 text-sm font-bold rounded-xl transition-all">
                      Test
                    </button>
                  </div>
                </div>

                {pairingCommand && (
                  <div className="space-y-3">
                    <label className="block text-[10px] font-black uppercase text-gray-500 tracking-tighter">Connection Command</label>
                    <div className="p-4 bg-black rounded-xl border border-border/50 font-mono text-[11px] text-accent-hover break-all overflow-hidden relative group">
                      {pairingCommand}
                      <button 
                        onClick={() => navigator.clipboard.writeText(pairingCommand)}
                        className="absolute right-2 top-2 p-1.5 bg-neutral-800 rounded-md opacity-0 group-hover:opacity-100 transition-opacity"
                      >
                        <Zap size={12} />
                      </button>
                    </div>
                    <p className="text-[10px] text-gray-500 italic">Run this in your terminal to complete pairing.</p>
                  </div>
                )}
              </div>
            )}

            {activeTab === 'calendar' && (
              <div className="space-y-8">
                <div className="p-6 bg-neutral-900/50 border border-border rounded-3xl flex items-start gap-5">
                  <div className="w-12 h-12 bg-blue-500/10 rounded-2xl flex items-center justify-center text-blue-500 flex-shrink-0">
                    <Calendar size={24} />
                  </div>
                  <div className="flex-1 space-y-4">
                    <div>
                      <h3 className="font-bold text-white">Google Calendar</h3>
                      <p className="text-xs text-gray-400 mt-1 leading-relaxed">Sync your reminders directly to your Google Calendar.</p>
                    </div>
                    
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] font-black uppercase text-gray-500">Status:</span>
                      {gcalStatus?.connected ? (
                        <span className="text-[10px] font-black uppercase text-green-500 flex items-center gap-1">
                          <CheckCircle2 size={12} /> Connected
                        </span>
                      ) : (
                        <span className="text-[10px] font-black uppercase text-gray-500">Not Connected</span>
                      )}
                    </div>

                    <button 
                      onClick={handleConnectGcal}
                      className="w-full bg-white text-black hover:bg-gray-200 text-sm font-bold py-2.5 rounded-xl transition-all flex items-center justify-center gap-2"
                    >
                      {gcalStatus?.connected ? 'Reconnect Google' : 'Connect Google Calendar'}
                      <ExternalLink size={14} />
                    </button>
                  </div>
                </div>

                <div className="p-6 bg-neutral-900/50 border border-border rounded-3xl flex items-start gap-5 opacity-50 grayscale">
                  <div className="w-12 h-12 bg-orange-500/10 rounded-2xl flex items-center justify-center text-orange-500 flex-shrink-0">
                    <Zap size={24} />
                  </div>
                  <div className="flex-1 space-y-4">
                    <div>
                      <h3 className="font-bold text-white">Zapier MCP</h3>
                      <p className="text-xs text-gray-400 mt-1 leading-relaxed">Connect to 6,000+ apps via Zapier's MCP server.</p>
                    </div>
                    <button disabled className="w-full border border-border text-sm font-bold py-2.5 rounded-xl">
                      Coming Soon
                    </button>
                  </div>
                </div>
              </div>
            )}

            {activeTab === 'models' && (
              <div className="space-y-6">
                <div>
                  <label className="block text-sm font-bold text-gray-300 mb-4 uppercase tracking-wider">AI Model Provider</label>
                  <div className="space-y-3">
                    {['Auto-detect', 'Gemini', 'OpenAI', 'DeepSeek'].map((provider) => (
                      <button
                        key={provider}
                        className={cn(
                          "w-full p-4 rounded-2xl border text-left transition-all flex items-center justify-between",
                          provider === 'Auto-detect' ? "border-accent bg-accent/5" : "border-border hover:border-gray-600"
                        )}
                      >
                        <span className="font-bold text-sm">{provider}</span>
                        {provider === 'Auto-detect' && <CheckCircle2 size={16} className="text-accent" />}
                      </button>
                    ))}
                  </div>
                </div>
                
                <p className="text-xs text-gray-500 italic px-2 leading-relaxed">
                  Companion automatically routes requests to the best available model. You can override this setting to force a specific provider.
                </p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
