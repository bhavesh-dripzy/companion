/**
 * Shares per-provider MCP connection status + a "connect" action with deeply
 * nested cards (e.g. the grocery category picker's "Search in" chips), without
 * threading props through ChatArea → ChatMessageItem → AttachmentList.
 *
 * `isConnected(pid)` reads the same `/api/mcp/status/all/` map the rest of the app
 * uses (provider key → status; connected = "ready" | "ready_pending"). `connect(pid)`
 * opens the Apps modal straight to that provider's auth flow, so a not-yet-connected
 * chip can pair the app inline and then be used like a connected one.
 */
import { createContext, useContext } from 'react';
import type { McpStatus } from './types';

export interface McpProviderCtx {
  statuses: Record<string, McpStatus>;
  /** pid: 'swiggy_instamart' | 'zepto' | … */
  isConnected: (pid: string) => boolean;
  /** Open the connect/auth modal for this provider. */
  connect: (pid: string) => void;
}

const McpProviderContext = createContext<McpProviderCtx>({
  statuses: {},
  isConnected: () => false,
  connect: () => {},
});

export const McpProviderProvider = McpProviderContext.Provider;

export const useMcpProviders = (): McpProviderCtx => useContext(McpProviderContext);
