import React, { useState } from "react";
import { motion } from "motion/react";
import { Bell, X, Check, Clock, Plus, Trash2, Search, Sparkles } from "lucide-react";

interface Reminder {
  id: string;
  category: string;
  text: string;
  time: string;
  completed: boolean;
}

interface RemindersPopupProps {
  isOpen: boolean;
  onClose: () => void;
  reminders: Reminder[];
  onToggleReminder: (id: string) => void;
  onAddReminder: (text: string, category: "daily" | "today") => void;
  onDeleteReminder?: (id: string) => void;
  onClearCompleted?: () => void;
}

export function RemindersPopup({
  isOpen,
  onClose,
  reminders,
  onToggleReminder,
  onAddReminder,
  onDeleteReminder,
  onClearCompleted,
}: RemindersPopupProps) {
  const [newText, setNewText] = useState("");
  const [category, setCategory] = useState<"daily" | "today">("today");
  const [searchQuery, setSearchQuery] = useState("");
  const [filterActive, setFilterActive] = useState<"all" | "pending" | "completed">("all");

  if (!isOpen) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newText.trim()) return;
    onAddReminder(newText.trim(), category);
    setNewText("");
  };

  // Filter & Search logic
  const filteredReminders = reminders.filter((rem) => {
    const matchesSearch = rem.text.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesTab =
      filterActive === "all" ||
      (filterActive === "pending" && !rem.completed) ||
      (filterActive === "completed" && rem.completed);
    return matchesSearch && matchesTab;
  });

  const dailyCount = reminders.filter((r) => r.category === "daily" && !r.completed).length;
  const todayCount = reminders.filter((r) => r.category === "today" && !r.completed).length;
  const completedCount = reminders.filter((r) => r.completed).length;

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center p-4 pt-[max(1rem,var(--safe-top))] pb-[max(1rem,var(--safe-bottom))]">
      {/* Dimmed backdrop with intense glass blur */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        onClick={onClose}
        className="absolute inset-0 bg-slate-950/70 backdrop-blur-md"
      />

      {/* Floating Center Card */}
      <motion.div
        initial={{ opacity: 0, scale: 0.95, y: 15 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.95, y: 15 }}
        transition={{ type: "spring", duration: 0.4, bounce: 0.15 }}
        className="relative bg-white border border-slate-100 rounded-3xl w-full max-w-2xl shadow-[0_30px_70px_-15px_rgba(15,23,42,0.22)] flex flex-col max-h-[88vh] overflow-hidden text-slate-800"
      >
        {/* Subtle upper glow line */}
        <div className="absolute top-0 inset-x-0 h-[3px] bg-gradient-to-r from-purple-500 via-indigo-500 to-indigo-600" />

        {/* Header Header */}
        <div className="px-6 py-5 border-b border-slate-100 flex items-center justify-between bg-gradient-to-b from-slate-50/50 to-white">
          <div className="flex items-center gap-3.5">
            <div className="w-11 h-11 rounded-2xl bg-purple-50 text-purple-600 border border-purple-100 flex items-center justify-center shadow-xs">
              <Bell className="w-5.5 h-5.5 stroke-[2.2] animate-bounce" style={{ animationDuration: '3s' }} />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="text-lg font-extrabold text-slate-900 tracking-tight font-sans">
                  Companion Reminders Hub
                </h3>
                <span className="bg-purple-100/70 text-purple-700/90 text-[10px] font-bold px-2 py-0.5 rounded-full flex items-center gap-1">
                  <Sparkles className="w-2.5 h-2.5" /> Core Applet
                </span>
              </div>
              <p className="text-xs text-slate-400 font-medium tracking-wide mt-0.5">
                Active habits, physical fitness stretching timers, and delivery monitors.
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="w-9 h-9 rounded-full bg-slate-100/60 hover:bg-slate-150 border border-slate-200/50 text-slate-450 hover:text-slate-705 flex items-center justify-center transition-all cursor-pointer"
          >
            <X className="w-4.5 h-4.5 stroke-[2.5]" />
          </button>
        </div>

        {/* Filter Control Bar */}
        <div className="px-6 py-3.5 bg-slate-50/40 border-b border-slate-100 flex flex-col sm:flex-row gap-3 items-center justify-between">
          <div className="flex items-center gap-1.5 bg-slate-100/90 p-1 rounded-xl w-full sm:w-auto">
            {(["all", "pending", "completed"] as const).map((tab) => (
              <button
                key={tab}
                onClick={() => setFilterActive(tab)}
                className={`flex-1 sm:flex-none capitalize text-xs font-bold px-3.5 py-1.5 rounded-lg transition-all duration-150 cursor-pointer ${
                  filterActive === tab
                    ? "bg-white text-slate-900 shadow-3xs"
                    : "text-slate-500 hover:text-slate-800"
                }`}
              >
                {tab === "completed" ? `Completed (${completedCount})` : tab}
              </button>
            ))}
          </div>

          {/* Quick Search bar */}
          <div className="relative w-full sm:w-60 shrink-0">
            <Search className="absolute left-3 top-2.5 w-4 h-4 text-slate-450 stroke-[2]" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search active prompts..."
              className="w-full bg-slate-100/80 border border-slate-200/40 focus:border-purple-300 rounded-xl pl-9 pr-4 py-2 text-xs text-slate-800 placeholder:text-slate-450 focus:outline-none transition-all"
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery("")}
                className="absolute right-2.5 top-2.5 text-xs text-slate-400 hover:text-slate-650"
              >
                Clean
              </button>
            )}
          </div>
        </div>

        {/* Modal Scrollable Workspace */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6 scrollbar-thin scrollbar-thumb-slate-300 scrollbar-track-transparent">
          {/* Grid Panel for Categories */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Column A: Daily Routines */}
            <div className="space-y-4">
              <div className="flex items-center justify-between pb-2 border-b border-purple-100/60">
                <span className="text-[11.5px] font-extrabold text-purple-700 tracking-wider uppercase font-sans flex items-center gap-1.5">
                  ⏱️ Daily Routines
                </span>
                <span className="bg-purple-50 text-purple-700 px-2 py-0.5 rounded-full text-[9.5px] font-bold font-mono border border-purple-100/60">
                  {dailyCount} Pending
                </span>
              </div>

              <div className="space-y-2.5">
                {filteredReminders.filter((r) => r.category === "daily").length === 0 ? (
                  <div className="text-center py-6 px-4 bg-slate-50/50 border border-dashed border-slate-150 rounded-2xl">
                    <p className="text-[11.5px] font-medium text-slate-400">No active routines scheduled</p>
                  </div>
                ) : (
                  filteredReminders
                    .filter((r) => r.category === "daily")
                    .map((rem) => (
                      <div
                        key={rem.id}
                        className={`group relative flex items-start gap-3.5 p-3.5 rounded-2xl border transition-all duration-200 ${
                          rem.completed
                            ? "bg-slate-50/80 border-slate-100 text-slate-400 opacity-70"
                            : "bg-white border-slate-200/90 shadow-3xs hover:shadow-2xs hover:border-purple-250 hover:bg-slate-50/10"
                        }`}
                      >
                        <button
                          onClick={() => onToggleReminder(rem.id)}
                          className={`w-5 h-5 rounded-full border flex items-center justify-center shrink-0 mt-0.5 transition-all cursor-pointer ${
                            rem.completed
                              ? "bg-purple-600 border-purple-600 text-white shadow-3xs"
                              : "border-slate-300 bg-white hover:border-purple-500 text-transparent group-hover:bg-purple-50/50"
                          }`}
                        >
                          <Check className="w-3.5 h-3.5 stroke-[3.5]" />
                        </button>

                        <div className="min-w-0 flex-1 cursor-pointer" onClick={() => onToggleReminder(rem.id)}>
                          <p
                            className={`text-[13px] font-semibold leading-relaxed tracking-tight ${
                              rem.completed ? "line-through text-slate-400" : "text-slate-800"
                            }`}
                          >
                            {rem.text}
                          </p>
                          <div className="flex items-center gap-1.5 mt-1.5 text-slate-400">
                            <Clock className="w-3 h-3 text-slate-350" />
                            <span className="text-[10px] font-bold font-mono tracking-wide">{rem.time}</span>
                          </div>
                        </div>

                        {onDeleteReminder && (
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              onDeleteReminder(rem.id);
                            }}
                            className="opacity-0 group-hover:opacity-100 p-1.5 rounded-lg hover:bg-red-50 text-slate-350 hover:text-red-500 transition-all cursor-pointer"
                            title="Delete reminder"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        )}
                      </div>
                    ))
                )}
              </div>
            </div>

            {/* Column B: Today Actions */}
            <div className="space-y-4">
              <div className="flex items-center justify-between pb-2 border-b border-indigo-100/60">
                <span className="text-[11.5px] font-extrabold text-indigo-700 tracking-wider uppercase font-sans flex items-center gap-1.5">
                  📅 Today Actions
                </span>
                <span className="bg-indigo-50 text-indigo-750 px-2 py-0.5 rounded-full text-[9.5px] font-bold font-mono border border-indigo-150">
                  {todayCount} Active
                </span>
              </div>

              <div className="space-y-2.5">
                {filteredReminders.filter((r) => r.category === "today").length === 0 ? (
                  <div className="text-center py-6 px-4 bg-slate-50/50 border border-dashed border-slate-150 rounded-2xl">
                    <p className="text-[11.5px] font-medium text-slate-400">No on-demand reminders loaded</p>
                  </div>
                ) : (
                  filteredReminders
                    .filter((r) => r.category === "today")
                    .map((rem) => (
                      <div
                        key={rem.id}
                        className={`group relative flex items-start gap-3.5 p-3.5 rounded-2xl border transition-all duration-200 ${
                          rem.completed
                            ? "bg-slate-50/80 border-slate-100 text-slate-400 opacity-70"
                            : "bg-white border-slate-200/90 shadow-3xs hover:shadow-2xs hover:border-indigo-250 hover:bg-slate-50/10"
                        }`}
                      >
                        <button
                          onClick={() => onToggleReminder(rem.id)}
                          className={`w-5 h-5 rounded-full border flex items-center justify-center shrink-0 mt-0.5 transition-all cursor-pointer ${
                            rem.completed
                              ? "bg-indigo-600 border-indigo-600 text-white shadow-3xs"
                              : "border-slate-300 bg-white hover:border-indigo-500 text-transparent group-hover:bg-indigo-50/50"
                          }`}
                        >
                          <Check className="w-3.5 h-3.5 stroke-[3.5]" />
                        </button>

                        <div className="min-w-0 flex-1 cursor-pointer" onClick={() => onToggleReminder(rem.id)}>
                          <p
                            className={`text-[13px] font-semibold leading-relaxed tracking-tight ${
                              rem.completed ? "line-through text-slate-400" : "text-slate-800"
                            }`}
                          >
                            {rem.text}
                          </p>
                          <div className="flex items-center gap-1.5 mt-1.5 text-slate-400">
                            <Clock className="w-3 h-3 text-slate-350" />
                            <span className="text-[10px] font-bold font-mono tracking-wide">{rem.time}</span>
                          </div>
                        </div>

                        {onDeleteReminder && (
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              onDeleteReminder(rem.id);
                            }}
                            className="opacity-0 group-hover:opacity-100 p-1.5 rounded-lg hover:bg-red-50 text-slate-350 hover:text-red-500 transition-all cursor-pointer"
                            title="Delete reminder"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        )}
                      </div>
                    ))
                )}
              </div>
            </div>
          </div>

          {/* Quick Append Form */}
          <form onSubmit={handleSubmit} className="bg-gradient-to-br from-slate-50 to-slate-100/65 p-4.5 rounded-2xl border border-slate-200/70 shadow-3xs relative overflow-hidden">
            {/* Soft decorative visual blur behind */}
            <div className="absolute right-0 bottom-0 w-32 h-32 bg-purple-200/20 rounded-full blur-2xl pointer-events-none" />

            <div className="flex items-center justify-between mb-3">
              <h4 className="text-[12px] font-extrabold text-slate-705 text-slate-705 uppercase tracking-wider flex items-center gap-1.5 font-sans">
                <Plus className="w-4 h-4 text-purple-600" /> Quick Add New Prompt
              </h4>
              {completedCount > 0 && onClearCompleted && (
                <button
                  type="button"
                  onClick={onClearCompleted}
                  className="text-[10.5px] font-bold text-slate-450 hover:text-red-500 hover:underline cursor-pointer"
                >
                  Clear Completed ({completedCount})
                </button>
              )}
            </div>

            <div className="flex flex-col sm:flex-row gap-3">
              <div className="relative flex-1">
                <input
                  type="text"
                  value={newText}
                  onChange={(e) => setNewText(e.target.value)}
                  placeholder="Task details (e.g. Confirm Zoom sync, post parcel check...)"
                  className="w-full bg-white border border-slate-200 rounded-xl pl-4 pr-10 py-2.5 text-xs text-slate-850 placeholder:text-slate-400 focus:outline-none focus:border-purple-400 focus:shadow-xs transition-all"
                  required
                />
              </div>

              <div className="flex gap-2 min-w-[200px]">
                <select
                  value={category}
                  onChange={(e) => setCategory(e.target.value as "daily" | "today")}
                  className="bg-white border border-slate-200 rounded-xl px-3 text-xs text-slate-705 font-medium focus:outline-none cursor-pointer focus:border-purple-400"
                >
                  <option value="daily">Daily Habit Schedule</option>
                  <option value="today">Today Specific Action</option>
                </select>

                <button
                  type="submit"
                  className="bg-slate-900 hover:bg-slate-950 text-white text-xs font-bold px-5 rounded-xl shadow-xs transition-colors cursor-pointer shrink-0 flex items-center gap-1.5 active:scale-95"
                >
                  Add Task
                </button>
              </div>
            </div>
          </form>
        </div>

        {/* Modal Footer */}
        <div className="border-t border-slate-100 px-6 py-4 flex items-center justify-end bg-slate-50/80">
          <button
            onClick={onClose}
            className="bg-slate-100 hover:bg-slate-200 border border-slate-200 text-slate-705 font-bold text-xs px-4.5 py-2.5 rounded-xl transition-all cursor-pointer"
          >
            Close Settings
          </button>
        </div>
      </motion.div>
    </div>
  );
}
