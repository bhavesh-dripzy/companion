/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useRef, useEffect } from "react";
import { 
  Send, 
  Mic, 
  Paperclip, 
  Terminal, 
  Menu, 
  Sparkles, 
  Flame, 
  MessageSquare,
  X,
  Volume2,
  ArrowRight,
  CirclePlay,
  CheckCircle2,
  AlertCircle,
  Car,
  Utensils,
  Calendar,
  Ticket,
  Bell
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import { motion, AnimatePresence } from "motion/react";
import { ChatMessage, SystemLog } from "../types";
import { ChatMessageItem } from "./ChatMessageItem";

interface ChatAreaProps {
  messages: ChatMessage[];
  onSendMessage: (text: string) => void;
  isPending: boolean;
  logs: SystemLog[];
  onSuggestionClick: (text: string) => void;
  onToggleLeft?: () => void;
  connectedCount?: number;
  isSidebarCollapsed?: boolean;
}

export function ChatArea({
  messages,
  onSendMessage,
  isPending,
  logs,
  onSuggestionClick,
  onToggleLeft,
  connectedCount = 0,
  isSidebarCollapsed = false
}: ChatAreaProps) {
  const [inputValue, setInputValue] = useState("");
  const [isListening, setIsListening] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Modular Prompt Options dataset above the chat input
  const promptOptions = [
    {
      id: "cab",
      label: "Book a Cab",
      prompt: "Book a cab from my location to the office",
      icon: Car,
      color: "bg-blue-50/70 border-blue-100 hover:border-blue-400 text-blue-600 hover:bg-blue-50/95",
      iconColor: "text-blue-500 bg-blue-100/60"
    },
    {
      id: "food",
      label: "Order Food",
      prompt: "Order a delicious cheese pizza and garlic bread from a top-rated Italian restaurant",
      icon: Utensils,
      color: "bg-orange-50/70 border-orange-100 hover:border-orange-400 text-orange-600 hover:bg-orange-50/95",
      iconColor: "text-orange-500 bg-orange-100/60"
    },
    {
      id: "table",
      label: "Book a Table",
      prompt: "Book a elegant table for 2 at Indiranagar restaurant for 8 PM tonight",
      icon: Calendar,
      color: "bg-emerald-50/70 border-emerald-100 hover:border-emerald-400 text-emerald-600 hover:bg-emerald-50/95",
      iconColor: "text-emerald-500 bg-emerald-100/60"
    },
    {
      id: "movie",
      label: "Book Movie Ticket",
      prompt: "Book two premium tickets for the latest sci-fi movie release nearby",
      icon: Ticket,
      color: "bg-indigo-50/70 border-indigo-100 hover:border-indigo-400 text-indigo-600 hover:bg-indigo-50/95",
      iconColor: "text-indigo-500 bg-indigo-100/60"
    },
    {
      id: "reminder",
      label: "Set a Reminder",
      prompt: "Set a reminder to check on high-priority team report tomorrow at 10 AM",
      icon: Bell,
      color: "bg-purple-50/70 border-purple-100 hover:border-purple-400 text-purple-600 hover:bg-purple-50/95",
      iconColor: "text-purple-500 bg-purple-100/60"
    }
  ];

  // Auto-scroll to newest message in chat mode
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, isPending]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputValue.trim() || isPending) return;
    onSendMessage(inputValue.trim());
    setInputValue("");
  };

  const startVoiceListening = () => {
    if (isListening) return;
    setIsListening(true);
    // Simulate voice capturing then sending a popular query
    setTimeout(() => {
      setIsListening(false);
      onSendMessage("What's happening in Indiranagar today?");
    }, 2800);
  };

  // Elite Playful high-fidelity brand dataset directly inspired by the user's attached mockup image
  const appCards = [
    {
      id: "zomato",
      name: "Zomato",
      prompt: "Search pizza restaurants available near Bangalore on Zomato",
      bgColor: "bg-gradient-to-b from-[#e23744] to-[#cb202d]",
      textColor: "text-white",
      angleClass: "-rotate-3 hover:rotate-0",
      renderIcon: () => (
        <div className="w-12 h-12 relative flex items-center justify-center">
          {/* Playful realistic pizza graphics representing Zomato in the image */}
          <div className="w-9 h-9 bg-[#fdf2f2] rounded-full shadow-2xs border border-[#ffb3c1]/40 flex items-center justify-center transform rotate-12 transition-transform duration-300">
            <span className="text-xl leading-none">🍕</span>
          </div>
          <span className="absolute -bottom-1 -right-1 text-[13px]">🍔</span>
        </div>
      ),
      logoText: "zomato",
      tagline: "Food Delivery"
    },
    {
      id: "swiggy",
      name: "Swiggy",
      prompt: "Is organic milk in stock on Swiggy Instamart?",
      bgColor: "bg-[#fc8019]",
      textColor: "text-white",
      angleClass: "rotate-2 hover:rotate-0",
      renderIcon: () => (
        <div className="w-11 h-11 relative flex items-center justify-center">
          {/* Swiggy Orange logo style */}
          <div className="w-9 h-9 bg-white rounded-full flex items-center justify-center shadow-3xs">
            <span className="text-xl">📍</span>
          </div>
        </div>
      ),
      logoText: "SWIGey",
      tagline: "Fast Order"
    },
    {
      id: "uber",
      name: "Uber",
      prompt: "Compute traffic and transit duration stats from Indiranagar to Whitefield",
      bgColor: "bg-black",
      textColor: "text-white",
      angleClass: "-rotate-1 hover:rotate-0",
      renderIcon: () => (
        <div className="w-12 h-11 flex items-center justify-center font-sans font-black text-2.5xl tracking-tighter text-white">
          Uber
        </div>
      ),
      logoText: "Uber",
      tagline: "Ride Sync"
    },
    {
      id: "zepto",
      name: "Zepto",
      prompt: "Check if whole wheat bread and milk are in stock on Zepto",
      bgColor: "bg-[#5c2d91]",
      textColor: "text-white",
      angleClass: "rotate-3 hover:rotate-0",
      renderIcon: () => (
        <div className="w-11 h-10 bg-white/10 rounded-xl flex items-center justify-center gap-1 border border-white/20 shadow-inner">
          <span className="text-lg">🥛</span>
          <span className="text-xs">🥕</span>
        </div>
      ),
      logoText: "zepto",
      tagline: "10 Min Store"
    },
    {
      id: "groww",
      name: "Groww",
      prompt: "Check my SIP portfolio status and stock market updates on Groww",
      bgColor: "bg-white",
      textColor: "text-slate-800",
      borderColor: "border-[#cbdffd]/60",
      angleClass: "-rotate-2 hover:rotate-0",
      renderIcon: () => (
        <div className="w-9 h-9 rounded-full bg-gradient-to-tr from-[#00b0ff] to-[#00d09c] flex items-center justify-center shadow-3xs">
          <span className="text-white text-[10px] font-black">▲</span>
        </div>
      ),
      logoText: "Groww",
      tagline: "SIP & Stocks"
    },
    {
      id: "blinkit",
      name: "Blinkit",
      prompt: "Get fresh vegetables and organic tomatoes in 10 mins from Blinkit",
      bgColor: "bg-[#fcd303]",
      textColor: "text-slate-950",
      angleClass: "rotate-3 hover:rotate-0",
      renderIcon: () => (
        <div className="w-10 h-10 bg-white rounded-xl flex items-center justify-center relative shadow-3xs border border-yellow-400">
          <span className="text-xl">🍏</span>
          <span className="absolute -bottom-1 -right-0.5 text-xs">🛍️</span>
        </div>
      ),
      logoText: "biinkit",
      tagline: "Groceries"
    },
    {
      id: "google_maps",
      name: "Google Maps",
      prompt: "Can you search current traffic condition and locate fast route on Google Maps?",
      bgColor: "bg-white",
      textColor: "text-slate-800",
      borderColor: "border-[#cbdffd]/60",
      angleClass: "-rotate-3 hover:rotate-0",
      renderIcon: () => (
        <div className="w-9 h-9 relative flex items-center justify-center">
          <div className="w-8 h-8 rounded-full bg-slate-50 flex items-center justify-center border border-slate-100 shadow-3xs">
            <span className="text-lg">🗺️</span>
          </div>
        </div>
      ),
      logoText: "Googe Maps",
      tagline: "Navigation"
    },
    {
      id: "google_photos",
      name: "Google Photos",
      prompt: "Show me my holiday photos memories in Google Photos",
      bgColor: "bg-white",
      textColor: "text-slate-800",
      borderColor: "border-[#cbdffd]/60",
      angleClass: "rotate-2 hover:rotate-0",
      renderIcon: () => (
        <div className="grid grid-cols-2 gap-0.5 w-6 h-6 transform rotate-12 transition-transform duration-300">
          <span className="bg-blue-500 rounded-tl-full h-2.5 w-2.5" />
          <span className="bg-yellow-400 rounded-tr-full h-2.5 w-2.5" />
          <span className="bg-red-500 rounded-bl-full h-2.5 w-2.5" />
          <span className="bg-emerald-500 rounded-br-full h-2.5 w-2.5" />
        </div>
      ),
      logoText: "Googe Haos",
      tagline: "Gallery"
    },
    {
      id: "google_calendar",
      name: "Google Calendar",
      prompt: "Can you help me book doctor appointment on my Google Calendar?",
      bgColor: "bg-white",
      textColor: "text-slate-800",
      borderColor: "border-[#cbdffd]/60",
      angleClass: "-rotate-1 hover:rotate-0",
      renderIcon: () => (
        <div className="w-9 h-9 bg-white rounded-lg border border-slate-200 shadow-3xs flex flex-col items-center justify-center overflow-hidden relative shrink-0">
          <div className="bg-red-500 h-2 w-full absolute top-0" />
          <span className="text-[12px] font-black mt-2 text-slate-800 font-display">31</span>
        </div>
      ),
      logoText: "Caleridar",
      tagline: "Planning"
    },
    {
      id: "district",
      name: "District",
      prompt: "Find the latest dining and lifestyle ticket event near me on District",
      bgColor: "bg-[#7a1fa2]",
      textColor: "text-white",
      angleClass: "rotate-2 hover:rotate-0",
      renderIcon: () => (
        <div className="w-10 h-10 bg-white/10 rounded-full flex items-center justify-center border border-white/20">
          <span className="text-lg">🎟️</span>
        </div>
      ),
      logoText: "district",
      tagline: "DY SONATO"
    }
  ];

  // Helper to extract clean user first name from metadata user details or fallback to "Bhavesh"
  const welcomeName = "Bhavesh";

  return (
    <div className="flex-1 flex flex-col h-full bg-gradient-to-tr from-[#fafbf7] via-[#f1f7fe] to-[#edf5ff] overflow-hidden relative font-sans select-none">
      
      {/* Pristine high-fidelity consistent mesh with premium sunlit gold, sky blue, and white gradient waves */}
      <div className="absolute inset-0 pointer-events-none z-0 overflow-hidden">
        {/* Soft sunlit amber glow left-center */}
        <div className="absolute top-[15%] -left-[10%] w-[55%] h-[50%] rounded-full bg-[#fdf8d7]/70 filter blur-[110px] opacity-75" />
        
        {/* Deep luxurious sky blue flow bottom-right */}
        <div className="absolute -bottom-[12%] right-[5%] w-[60%] h-[55%] rounded-full bg-[#dbeeff]/75 filter blur-[125px] opacity-80" />
        
        {/* Glowing bright pristine white highlight top-right */}
        <div className="absolute top-[-5%] right-[10%] w-[50%] h-[45%] rounded-full bg-white/95 filter blur-[100px] opacity-95" />
        
        {/* Soft pastel pale mint-blue accent center for visual richness */}
        <div className="absolute top-[35%] left-[20%] w-[45%] h-[45%] rounded-full bg-[#e3f4f1]/55 filter blur-[110px] opacity-70" />
      </div>

      {/* Top Header Section compatible for both mobile & desktop */}
      <header className="shrink-0 bg-white/50 border-b border-slate-200/40 px-5 py-3.5 flex items-center justify-between backdrop-blur-md sticky top-0 z-30 select-none">
        <div className="flex items-center gap-3">
          {/* Sidebar toggler (visible only when collapsed to prevent redundancy) */}
          {isSidebarCollapsed && (
            <button
              type="button"
              onClick={onToggleLeft}
              className="p-2 rounded-xl text-indigo-600 bg-indigo-50 border border-indigo-200 hover:bg-indigo-100/80 hover:border-indigo-300 shadow-sm transition-all duration-300 active:scale-95 cursor-pointer"
              title="Expand sidebar"
            >
              <Menu className="w-5 h-5 stroke-[2.2]" />
            </button>
          )}
          
          <div className="flex items-center gap-2">
            <span className="font-bold text-slate-800 text-[15px] tracking-tight font-display">Companion</span>
            <span className="hidden sm:inline-block bg-emerald-500/10 text-emerald-700 border border-emerald-500/20 px-2 py-0.5 rounded-full text-[10px] font-semibold">
              Ready
            </span>
          </div>
        </div>

        {/* User Workspace Profile Status */}
        <div
          className="flex items-center gap-2 bg-white/90 text-slate-800 border border-slate-200/70 pl-2 pr-3.5 py-1.5 rounded-full text-xs font-bold shadow-3xs max-h-9 select-none"
          id="toggle-profile-btn"
        >
          <div className="w-5.5 h-5.5 rounded-full bg-gradient-to-tr from-indigo-600 to-violet-600 text-white flex items-center justify-center font-bold text-[10.5px] shadow-sm select-none">
            B
          </div>
          <span className="hidden sm:inline text-slate-700 font-display">Bhavesh</span>
          <span className="bg-emerald-500 text-white text-[10px] w-5 h-5 rounded-full flex items-center justify-center font-mono font-bold">
            {connectedCount}
          </span>
        </div>
      </header>

      {/* Main Mode Viewport Renderer */}
      <div className="flex-1 overflow-hidden relative z-10 flex flex-col">
        
        <div className="flex-1 flex flex-col h-full overflow-hidden">
          {/* Scrollable conversation bubble arena */}
          <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 md:px-8 py-6 space-y-6 no-scrollbar">
            
            {messages.length === 0 ? (
              /* GORGEOUS HIGH-FIDELITY ONBOARDING DASHBOARD AS THE MAIN HOME SCREEN FOR ZOMATO / UBER / REMINDERS */
              <div className="h-full min-h-0 flex flex-col justify-center items-center px-4 py-1.5 md:py-2 space-y-4 md:space-y-6 select-none w-full max-w-4xl mx-auto z-10 relative">
                
                {/* Header Container with Title and Floating Robot Mascot side-by-side - ENTERS SEQUENTIALLY AFTER IMAGE SPIN COMPLETES */}
                <motion.div 
                  initial={{ opacity: 0, y: -15 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ 
                    delay: 1.5, 
                    duration: 0.7, 
                    ease: "easeOut" 
                  }}
                  className="w-full flex flex-col md:flex-row items-center justify-between gap-3 md:gap-6 relative shrink-0"
                >
                  
                  {/* Left Column: Simple Elegant Welcome Greeting */}
                  <div className="space-y-1 flex-1 text-center md:text-left">
                    <h1 className="font-display font-black text-slate-900 text-2xl md:text-[36px] tracking-tight leading-tight">
                      Hi {welcomeName || "Bhavesh"}, how can I help you today?
                    </h1>
                  </div>

                  {/* Right Column: Floating AI Robot Mascot Companion */}
                  <div className="flex items-center gap-2.5 shrink-0 relative mt-1 md:mt-0">
                    {/* Speech Bubble popup */}
                    <div className="mb-2 relative bg-white border border-slate-150/85 px-3 py-1.5 rounded-2xl shadow-sm text-center">
                      <span className="text-[10px] font-bold text-slate-900 block leading-tight font-sans">
                        Hey there! 👋
                      </span>
                      <span className="text-[10px] font-bold text-slate-800 block leading-tight font-sans mt-0.5">
                        Need a boost?
                      </span>
                      {/* Tail arrow pointer */}
                      <div className="absolute right-[-5px] top-5 w-2.5 h-2.5 bg-white border-r border-b border-slate-100 rotate-[-45deg] z-10" />
                    </div>

                    {/* Cute Futuristic Capsule Robot */}
                    <div className="w-14 h-14 bg-gradient-to-b from-white to-slate-100 rounded-full border border-slate-200 shadow-md flex items-center justify-center relative animate-bounce shrink-0" style={{ animationDuration: '4s' }}>
                      {/* Glowing Face Shield Panel */}
                      <div className="w-11 h-8 bg-[#121420] rounded-[12px] border border-slate-700 flex items-center justify-center relative shadow-inner overflow-hidden">
                        {/* Digital glowing cyan LED eyes smiley */}
                        <div className="flex items-center gap-1">
                          <span className="text-cyan-400 text-xs font-black leading-none animate-pulse">^</span>
                          <span className="text-cyan-400 text-xs font-black leading-none animate-pulse">^</span>
                        </div>
                        {/* Interactive scanning pulse wave */}
                        <div className="w-full h-[1px] bg-cyan-400/20 absolute top-2.5 left-0 animate-pulse" />
                      </div>
                      
                      {/* Left and Right arms */}
                      <div className="absolute -left-1 top-5 w-2 h-4.5 bg-slate-100 border border-slate-200/80 rounded-full transform -rotate-12 shadow-3xs" />
                      <div className="absolute -right-1 top-5 w-2 h-4.5 bg-slate-100 border border-slate-200/80 rounded-full transform rotate-12 shadow-3xs" />
                      
                      {/* Metallic glossy reflection shield */}
                      <div className="absolute top-0.5 left-1.5 w-3 h-1 bg-white/40 rounded-full filter blur-[0.5px]" />
                    </div>
                  </div>

                </motion.div>

                {/* High-Fidelity Mobile App Deck matching the image precisely */}
                <div className="w-full relative z-10 flex flex-col items-center justify-center min-h-0 flex-1 max-h-[48vh] md:max-h-[58vh] -mt-1 md:-mt-3">
                  <div className="relative w-full max-w-lg md:max-w-2xl lg:max-w-3xl mx-auto h-full flex items-center justify-center transition-transform duration-500 hover:scale-[1.015]">
                    {/* Soft ambient glow behind the phones transparent image */}
                    <div className="absolute inset-x-12 top-4 bottom-4 bg-gradient-to-tr from-sky-300/20 via-pink-350/15 to-indigo-400/20 rounded-full filter blur-[45px] opacity-75 pointer-events-none" />
                    <motion.img 
                      initial={{ opacity: 0, scale: 0.95 }}
                      animate={{ opacity: 1, scale: 1 }}
                      transition={{ 
                        type: "spring",
                        stiffness: 70,
                        damping: 15,
                        duration: 1.0
                      }}
                      src="https://i.postimg.cc/pT4dvmdP/pakkaaaaa.png" 
                      alt="App Companions Collage" 
                      className="max-w-full max-h-full object-contain relative z-10 drop-shadow-[0_12px_28px_rgba(30,41,59,0.12)]"
                      referrerPolicy="no-referrer"
                    />
                  </div>
                </div>

              </div>
            ) : (
              /* Active message threads list */
              <div className="w-full space-y-6 pb-26">
                {messages.map((msg, index) => (
                  <ChatMessageItem key={msg.id} msg={msg} />
                ))}

                {/* Pending bot loading bar */}
                {isPending && (
                  <motion.div 
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    className="flex items-center gap-3 pl-3"
                  >
                    <div className="w-8 h-8 rounded-full bg-slate-900 text-white flex items-center justify-center font-bold text-[12px] animate-bounce font-display">
                      C
                    </div>
                    <div className="bg-white/80 border border-slate-200/60 py-2.5 px-4 rounded-full flex items-center gap-2 shadow-xs backdrop-blur-xs">
                      <span className="w-1.5 h-1.5 bg-indigo-600 rounded-full animate-ping" />
                      <span className="text-slate-500 font-medium text-xs italic">
                        Thinking & coordinating server bindings...
                      </span>
                    </div>
                  </motion.div>
                )}
              </div>
            )}
          </div>

          {/* Bottom Custom Styled Chat Formulation form */}
          <div className="p-4 bg-gradient-to-t from-[#edf5ff]/95 via-[#edf5ff]/20 to-transparent sticky bottom-0 left-0 right-0 z-20">
            
            {/* Modular Horizontal Prompt Options Choice Ribbon */}
            <div className="w-full flex items-center gap-2 overflow-x-auto pb-3 mb-1.5 no-scrollbar scroll-smooth">
              {promptOptions.map((option) => {
                const IconComponent = option.icon;
                return (
                  <button
                    key={option.id}
                    type="button"
                    onClick={() => onSuggestionClick(option.prompt)}
                    className={`shrink-0 flex items-center gap-2 border px-3 py-1.5 rounded-full text-xs font-bold transition-all duration-200 active:scale-95 cursor-pointer shadow-[0_1px_2px_rgba(0,0,0,0.03)] hover:shadow-sm ${option.color}`}
                  >
                    <div className="p-1 rounded-md shrink-0">
                      <IconComponent className="w-3.5 h-3.5" />
                    </div>
                    <span>{option.label}</span>
                  </button>
                );
              })}
            </div>

            <form 
              onSubmit={handleSubmit}
              className="w-full bg-white border border-slate-200/80 shadow-md shadow-slate-300/10 rounded-full flex items-center p-1.5 gap-2"
            >
              {/* Plus button inside text field */}
              <button
                type="button"
                className="w-9 h-9 text-slate-400 hover:text-slate-650 hover:bg-slate-50 rounded-full flex items-center justify-center transition-colors shrink-0 cursor-pointer"
                title="Plus Attachment"
              >
                <Paperclip className="w-4.5 h-4.5" />
              </button>

              {isListening ? (
                /* Dynamic interactive live-listening soundwave view instead of typical text input */
                <div className="flex-1 flex items-center justify-between px-3">
                  <div className="flex items-center gap-3">
                    <span className="text-xs font-semibold text-indigo-650 animate-pulse font-display">
                      Listening... Speak now
                    </span>
                    <div className="flex items-center gap-0.5">
                      <span className="w-1 h-3 bg-indigo-500 rounded-full animate-bounce" style={{ animationDelay: '0.1s' }} />
                      <span className="w-1 h-5 bg-indigo-600 rounded-full animate-bounce" style={{ animationDelay: '0.3s' }} />
                      <span className="w-1 h-4 bg-indigo-500 rounded-full animate-bounce" style={{ animationDelay: '0.5s' }} />
                      <span className="w-1 h-1.5 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: '0s' }} />
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => setIsListening(false)}
                    className="text-[10px] font-black tracking-widest uppercase text-red-500 hover:text-red-650 bg-red-50 hover:bg-red-100/80 px-3 py-1 rounded-full transition-colors cursor-pointer"
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <input
                  type="text"
                  value={inputValue}
                  onChange={(e) => setInputValue(e.target.value)}
                  placeholder="Write here..."
                  disabled={isPending}
                  className="flex-1 bg-transparent border-none outline-none text-[13.5px] text-slate-800 placeholder-slate-400 py-2.5 px-1 focus:ring-0 min-w-0"
                />
              )}

              {/* Mic action triggers simulation directly on the workspace */}
              <button
                type="button"
                onClick={() => {
                  if (isListening) {
                    setIsListening(false);
                  } else {
                    startVoiceListening();
                  }
                }}
                className={`w-9 h-9 rounded-full flex items-center justify-center transition-all shrink-0 cursor-pointer ${
                  isListening 
                    ? "bg-indigo-650 text-white shadow-md animate-pulse" 
                    : "text-slate-400 hover:text-indigo-650 hover:bg-indigo-50"
                }`}
                title={isListening ? "Cancel speech input" : "Speak to virtual routing core"}
              >
                <Mic className="w-4.5 h-4.5" />
              </button>

              {/* Send action */}
              <button
                type="submit"
                disabled={!inputValue.trim() || isPending || isListening}
                className={`w-9.5 h-9.5 rounded-full flex items-center justify-center transition-all shrink-0 cursor-pointer ${
                  inputValue.trim() && !isPending && !isListening
                    ? "bg-slate-900 opacity-100 text-white shadow-md hover:bg-slate-800"
                    : "bg-slate-100 text-slate-300"
                }`}
              >
                <Send className="w-4 h-4 stroke-[2.2]" />
              </button>
            </form>
          </div>
        </div>
      </div>

    </div>
  );
}
