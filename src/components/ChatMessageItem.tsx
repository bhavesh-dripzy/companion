/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { 
  CheckCircle2, 
  Terminal, 
  Sparkles, 
  Layers,
  ChevronDown,
  User,
  Bot
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import { motion } from "motion/react";
import { ChatMessage } from "../types";

interface ChatMessageItemProps {
  msg: ChatMessage;
}

export function ChatMessageItem({ msg }: ChatMessageItemProps) {
  const isUser = msg.sender === "user";
  const [showMcpArgs, setShowMcpArgs] = React.useState(true);

  // Check if message is a simulated audio/waveform message
  const isAudio = msg.text.includes("[Audio]") || msg.text.includes("audio:") || msg.text.startsWith("🔉") || msg.text.includes("waveform");

  return (
    <motion.div 
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.28, ease: "easeOut" }}
      className={`flex ${isUser ? "justify-end" : "justify-start gap-3.5"} items-start w-full group`}
    >
      {/* Bot/Assistant Circular Icon & Signature Avatar matching the screenshot (dark rounded circle with white symbol) */}
      {!isUser && (
        <div className="w-9 h-9 rounded-full bg-[#121118] text-white flex items-center justify-center shadow-xs shrink-0 select-none transform hover:scale-105 transition-all text-sm font-black">
          ⚛
        </div>
      )}

      {/* Main message body wrapper section */}
      <div className={`flex flex-col ${isUser ? "max-w-[75%] items-end" : "max-w-[85%] flex-1"}`}>
        
        {/* If Bot / Companion triggered an active MCP tool node call */}
        {msg.mcpCall && (
          <div className="bg-gradient-to-r from-slate-50 to-indigo-50/20 border border-slate-250/50 rounded-2xl p-4 shadow-2xs flex flex-col gap-3 select-none font-sans text-xs w-full mb-2 transition-all hover:shadow-sm">
            {/* Header ribbon */}
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <div className="w-5 h-5 rounded bg-emerald-500/10 border border-emerald-300/30 flex items-center justify-center text-emerald-600 animate-pulse">
                  <span className="w-1.5 h-1.5 bg-emerald-500 rounded-full" />
                </div>
                <div className="flex flex-col">
                  <span className="text-[11px] font-bold text-slate-800 font-display">
                    Synced with {msg.mcpCall.serverName.replace(" Server", "").replace(" MCP", "")}
                  </span>
                  <span className="text-[9px] font-medium text-slate-400 uppercase tracking-widest font-mono">
                    Inter-Process Gateway Status
                  </span>
                </div>
              </div>

              <div className="flex items-center gap-2">
                <span className="text-[10px] font-mono font-bold text-emerald-700 bg-emerald-50 border border-emerald-100 px-2.5 py-0.5 rounded-full shadow-xs">
                  Stable Connection Active
                </span>
                
                <button 
                  onClick={() => setShowMcpArgs(!showMcpArgs)} 
                  className="p-1 hover:bg-slate-200/50 text-slate-400 hover:text-slate-650 rounded-lg transition-colors cursor-pointer"
                  title="Toggle connection arguments"
                >
                  <ChevronDown className={`w-3.5 h-3.5 transition-transform duration-200 ${showMcpArgs ? "rotate-180" : ""}`} />
                </button>
              </div>
            </div>
            
            {/* Expanded connection metadata fields */}
            {showMcpArgs && (
              <div className="text-[11.5px] text-slate-600 leading-normal bg-white/80 border border-slate-100 rounded-xl p-3 space-y-2">
                <div className="flex items-baseline gap-2 flex-wrap">
                  <span className="font-mono text-slate-400 font-bold text-[9px] uppercase tracking-wider block">Action Payload:</span>
                  <span className="text-indigo-600 font-bold font-mono">
                    {
                      msg.mcpCall.toolName
                        .split("_")
                        .map(w => w.charAt(0).toUpperCase() + w.slice(1))
                        .join(" ")
                        .replace("Zomato ", "")
                        .replace("Swiggy ", "")
                        .replace("Kite ", "")
                        .replace("Tracker ", "")
                    }
                  </span>
                </div>
                
                {msg.mcpCall.arguments && Object.keys(msg.mcpCall.arguments).length > 0 && (
                  <div className="flex flex-col gap-1">
                    <span className="font-mono text-slate-400 font-bold text-[9px] uppercase tracking-wider">Method Parameters:</span>
                    <div className="flex flex-wrap gap-1.5 mt-0.5">
                      {Object.entries(msg.mcpCall.arguments).map(([key, value]) => (
                        <span key={key} className="bg-slate-100/80 border border-slate-200/40 font-mono text-[10px] text-slate-700 rounded-md px-2 py-0.5 font-bold shadow-2xs">
                          {key}: <span className="text-indigo-600">{String(value)}</span>
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* Standard dialog bubble section styled EXACTLY like the mockup */}
        <div className={`px-5 py-3 rounded-3xl text-[14.5px] leading-relaxed transition-all duration-200 ${
          isUser 
            ? "bg-[#121118] text-white shadow-sm font-medium rounded-tr-sm" 
            : "bg-white border border-[#e3e1e9]/65 text-slate-800 shadow-2xs font-normal rounded-tl-sm select-text"
        }`}>
          {isAudio ? (
            /* Special rich media rendering for audio messages matching the screenshot play button & waveform design exactly! */
            <div className="flex items-center gap-3 py-1 font-sans">
              <button className={`w-9 h-9 rounded-full ${isUser ? "bg-white text-[#121118]" : "bg-[#121118] text-white"} flex items-center justify-center shadow-xs hover:scale-105 active:scale-95 transition-all shrink-0 cursor-pointer`}>
                <svg className="w-4 h-4 fill-current ml-0.5" viewBox="0 0 24 24">
                  <path d="M8 5v14l11-7z" />
                </svg>
              </button>
              {/* Dynamic waveform vertical bars representing voice data logs */}
              <div className="flex items-center gap-[2.5px] h-6 flex-1 pr-2">
                {[2, 4, 1, 3, 5, 2, 4, 3, 1, 5, 4, 2, 3, 5, 2, 4, 2, 1, 3, 4, 2, 5, 3, 1].map((val, i) => (
                  <div 
                    key={i} 
                    className={`w-0.5 ${isUser ? "bg-white/70" : "bg-indigo-500/70"} rounded-full`} 
                    style={{ height: `${val * 18}%` }}
                  />
                ))}
              </div>
              <span className={`text-xs font-bold ${isUser ? "text-white/95" : "text-slate-600"} font-mono select-none pr-1`}>2:19</span>
            </div>
          ) : (
            <div className="markdown-body text-inherit">
              <ReactMarkdown>{msg.text}</ReactMarkdown>
            </div>
          )}
        </div>
        
        {/* Underlay Metadata (Dynamic Clock Timestamp) styled exactly as the screenshot */}
        <span className={`text-[10px] text-slate-400 font-sans font-medium mt-1.5 px-1.5 select-none ${isUser ? "self-end mr-1" : "self-start ml-1"}`}>
          {msg.timestamp}
        </span>
      </div>
    </motion.div>
  );
}
