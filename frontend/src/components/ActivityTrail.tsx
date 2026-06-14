/**
 * Human activity trail — replaces the raw meta chips ("DISTRICT",
 * "TOOL EXECUTION FAILED.") and the "View trace details" debug block with a
 * warm, collapsed status pill + friendly step rail. The transparency stays;
 * the dread goes. Reads the same Message fields the old blocks read
 * (meta / trace / toolsOk / toolsError) so persisted chats render too.
 */

import React, { useState } from 'react';
import { ChevronDown, Check, AlertTriangle } from 'lucide-react';

const PROVIDER_INFO: Record<string, { label: string; color: string }> = {
  zomato: { label: 'Zomato', color: '#E23744' },
  swiggy_food: { label: 'Swiggy', color: '#FC8019' },
  swiggy: { label: 'Swiggy', color: '#FC8019' },
  swiggy_instamart: { label: 'Instamart', color: '#E8632F' },
  instamart: { label: 'Instamart', color: '#E8632F' },
  zepto: { label: 'Zepto', color: '#7B2CFA' },
  district: { label: 'District', color: '#7C3AED' },
  kite: { label: 'Kite', color: '#387ED1' },
  maps: { label: 'Maps', color: '#1a9d6b' },
  calendar: { label: 'Calendar', color: '#2b2f72' },
  twilio: { label: 'WhatsApp', color: '#1a9d6b' },
  whatsapp: { label: 'WhatsApp', color: '#1a9d6b' },
};

const TASK_LABELS: Record<string, string> = {
  food_delivery: 'food order',
  grocery_delivery: 'grocery order',
  going_out: 'going out',
  maps_query: 'directions',
  reminder_calendar: 'calendar',
  stocks_trading: 'trading',
  general_chat: 'chat',
};

interface ActivityTrailProps {
  meta?: string[];
  trace?: any[];
  toolsOk?: boolean;
  toolsError?: string;
}

const isProviderTag = (value: string): boolean => value in PROVIDER_INFO;

const friendlySteps = (trace: any[]): { text: string; warn?: boolean }[] => {
  const steps: { text: string; warn?: boolean }[] = [];
  for (const event of trace) {
    if (!event || typeof event !== 'object') continue;
    switch (event.event) {
      case 'route': {
        const task = TASK_LABELS[String(event.task)] || String(event.task || '').replace(/_/g, ' ');
        if (task) steps.push({ text: `Understood as ${task}` });
        break;
      }
      case 'plan': {
        const info = PROVIDER_INFO[String(event.provider)] || { label: String(event.provider || '') };
        const n = Number(event.tools) || 0;
        const names: string[] = Array.isArray(event.tool_names) ? event.tool_names : [];
        const toolsText = names.length > 0 ? ` · ${names.join(', ')}` : '';
        steps.push({ text: `Planned ${n} step${n === 1 ? '' : 's'} on ${info.label}${toolsText}` });
        break;
      }
      case 'execute': {
        const blocked = Number(event.blocked) || 0;
        const providerTools: any[] = Array.isArray(event.provider_tools) ? event.provider_tools : [];
        if (providerTools.length > 0) {
          for (const pt of providerTools) {
            const info = PROVIDER_INFO[String(pt.provider)] || { label: String(pt.provider || '') };
            const tools: any[] = Array.isArray(pt.tools) ? pt.tools : [];
            for (const t of tools) {
              if (!t.ok) {
                steps.push({ text: `${info.label}: ${t.tool} failed (${t.error || 'unknown'})`, warn: true });
              } else {
                steps.push({ text: `${info.label}: ${t.tool} ✓` });
              }
            }
          }
          if (blocked > 0) {
            steps.push({ text: `${blocked} step${blocked === 1 ? '' : 's'} held for your confirmation`, warn: true });
          }
        } else {
          steps.push({
            text: blocked > 0 ? `Ran the plan — ${blocked} step${blocked === 1 ? '' : 's'} held for your confirmation` : 'Ran the plan',
          });
        }
        break;
      }
      case 'collect': {
        const n = Number(event.attachments) || 0;
        const byProvider: Record<string, number> = event.by_provider && typeof event.by_provider === 'object' ? event.by_provider : {};
        const providerEntries = Object.entries(byProvider);
        if (providerEntries.length > 0) {
          for (const [pid, count] of providerEntries) {
            const info = PROVIDER_INFO[pid] || { label: pid };
            if (count === 0) {
              steps.push({ text: `${info.label}: no results`, warn: true });
            } else {
              steps.push({ text: `${info.label}: ${count} card${count === 1 ? '' : 's'}` });
            }
          }
        } else if (n > 0) {
          steps.push({ text: `Prepared ${n} card${n === 1 ? '' : 's'} for you` });
        }
        break;
      }
      default:
        break;
    }
  }
  return steps;
};

export const ActivityTrail: React.FC<ActivityTrailProps> = ({ meta, trace, toolsOk, toolsError }) => {
  const [open, setOpen] = useState(false);

  const providers = (meta || []).filter(isProviderTag);
  // Older persisted messages carried the ok/error string inside meta.
  const legacyError = (meta || []).find((m) => !isProviderTag(m) && m !== 'tools ok');
  const ok = toolsOk ?? !legacyError;
  const errorText = toolsError || legacyError;

  const primary = providers[0] ? PROVIDER_INFO[providers[0]] : undefined;
  const steps = friendlySteps(trace || []);
  const hasDetail = steps.length > 0 || !!errorText;
  if (!primary && !hasDetail) return null;

  const label = providers.length > 1
    ? providers.map((p) => PROVIDER_INFO[p].label).join(' + ')
    : primary?.label || 'Companion';

  return (
    <div className="mt-2 w-full">
      <button
        type="button"
        onClick={() => hasDetail && setOpen((v) => !v)}
        className={`inline-flex items-center gap-2 bg-white/80 border border-[#ece3d6] rounded-full pl-2.5 pr-3 py-1 text-[11px] font-semibold text-[#7a7388] transition-colors ${
          hasDetail ? 'cursor-pointer hover:border-[#ddd0bf] hover:text-[#221b2e]' : 'cursor-default'
        }`}
      >
        <span
          className="w-2 h-2 rounded-full shrink-0"
          style={{ background: primary?.color || '#7a7388' }}
        />
        <span>{label}</span>
        {ok ? (
          <Check size={11} className="text-[#1a9d6b]" strokeWidth={3} />
        ) : (
          <AlertTriangle size={11} className="text-[#e2a13c]" strokeWidth={2.5} />
        )}
        {hasDetail && (
          <ChevronDown size={11} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
        )}
      </button>

      {open && hasDetail && (
        <div className="mt-2 ml-2 border-l-2 border-[#ddd0bf] pl-4 flex flex-col gap-1.5">
          {steps.map((step, idx) => (
            <div key={idx} className="relative text-[12px] text-[#4a4458] flex items-center">
              <span
                className={`absolute -left-[21.5px] w-2.5 h-2.5 rounded-full bg-white border-2 ${
                  step.warn ? 'border-[#ffb347]' : 'border-[#1a9d6b]'
                }`}
              />
              {step.text}
            </div>
          ))}
          {errorText && (
            <div className="relative text-[12px] text-[#9a6a1f] flex items-center">
              <span className="absolute -left-[21.5px] w-2.5 h-2.5 rounded-full bg-[#fff4e0] border-2 border-[#ffb347]" />
              Hit a snag: {errorText}
            </div>
          )}
        </div>
      )}
    </div>
  );
};
