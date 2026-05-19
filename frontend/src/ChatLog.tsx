import React from 'react';
import ReactMarkdown from 'react-markdown';
import type { Message } from './types';
import { cn } from './utils';
import { User, Bot, Loader2 } from 'lucide-react';

interface ChatLogProps {
  messages: Message[];
  isThinking: boolean;
}

export const ChatLog: React.FC<ChatLogProps> = ({ messages, isThinking }) => {
  return (
    <div className="flex-1 overflow-y-auto p-4 md:p-6 space-y-8">
      {messages.map((msg, i) => (
        <div
          key={i}
          className={cn(
            "flex gap-4 max-w-4xl mx-auto",
            msg.role === 'user' ? "flex-row-reverse" : "flex-row"
          )}
        >
          <div className={cn(
            "w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0 shadow-sm",
            msg.role === 'user' ? "bg-accent text-white" : "bg-neutral-800 text-accent"
          )}>
            {msg.role === 'user' ? <User size={20} /> : <Bot size={20} />}
          </div>

          <div className={cn(
            "flex flex-col gap-2",
            msg.role === 'user' ? "items-end" : "items-start"
          )}>
            <div className={cn(
              "rounded-2xl px-5 py-3 text-[15px] leading-relaxed",
              msg.role === 'user' 
                ? "bg-accent text-white shadow-lg shadow-accent/10" 
                : "bg-surface border border-border text-gray-200"
            )}>
              <div className="prose prose-invert max-w-none prose-p:leading-relaxed prose-pre:bg-black/50 prose-pre:border prose-pre:border-white/10">
                <ReactMarkdown>
                  {msg.content}
                </ReactMarkdown>
              </div>
            </div>
            
            {msg.meta && msg.meta.length > 0 && (
              <div className="flex flex-wrap gap-2 mt-1">
                {msg.meta.map((m, idx) => (
                  <span key={idx} className="text-[10px] font-bold uppercase tracking-wider px-2 py-1 bg-neutral-900 border border-border rounded-md text-gray-500">
                    {m}
                  </span>
                ))}
              </div>
            )}

            {msg.trace && msg.trace.length > 0 && (
              <details className="mt-2 w-full">
                <summary className="text-xs text-gray-500 cursor-pointer hover:text-gray-400 transition-colors font-medium">
                  View trace details
                </summary>
                <div className="mt-2 space-y-2 p-3 bg-black/30 rounded-xl border border-border/50">
                  {msg.trace.map((t, idx) => (
                    <div key={idx} className="text-[11px] grid grid-cols-[80px_1fr] gap-2">
                      <span className="text-gray-400 font-semibold">{t.provider || t.stage}:</span>
                      <span className="text-gray-500 truncate">{t.detail}</span>
                    </div>
                  ))}
                </div>
              </details>
            )}
          </div>
        </div>
      ))}

      {isThinking && (
        <div className="flex gap-4 max-w-4xl mx-auto">
          <div className="w-10 h-10 rounded-xl bg-neutral-800 text-accent flex items-center justify-center flex-shrink-0 shadow-sm">
            <Bot size={20} />
          </div>
          <div className="flex flex-col gap-3">
            <div className="bg-surface border border-border rounded-2xl px-5 py-3 flex items-center gap-3">
              <Loader2 size={18} className="animate-spin text-accent" />
              <span className="text-sm text-gray-400 animate-pulse font-medium">Thinking...</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
