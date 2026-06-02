/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

export interface McpTool {
  name: string;
  description: string;
  parameters: {
    type: "OBJECT";
    properties: Record<string, {
      type: string;
      description?: string;
      enum?: string[];
    }>;
    required?: string[];
  };
}

export interface McpServer {
  id: string;
  name: string;
  iconText: string;
  iconBgColor: string;
  iconTextColor: string;
  toolsCount: number;
  description: string;
  connected: boolean;
  tools: McpTool[];
}

export interface ChatMessage {
  id: string;
  sender: "user" | "assistant" | "system" | "mcp";
  text: string;
  timestamp: string;
  mcpCall?: {
    serverName: string;
    toolName: string;
    arguments: Record<string, any>;
    status: "executing" | "success" | "failed";
    result?: string;
  };
}

export interface SystemLog {
  id: string;
  timestamp: string;
  type: "info" | "success" | "mcp_connect" | "mcp_call" | "error";
  message: string;
  details?: string;
}
