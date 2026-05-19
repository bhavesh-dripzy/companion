export interface UserStatus {
  authenticated: boolean;
  username: string;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  username: string;
}

export interface ToolProvider {
  id: string;
  label: string;
  available: boolean;
  mcp_url?: string;
  custom?: boolean;
  tool_count?: number;
}

export interface Address {
  address_id: string;
  location_name: string;
  latitude?: number;
  longitude?: number;
}

export interface Message {
  role: 'user' | 'assistant';
  content: string;
  meta?: string[];
  trace?: any[];
}

export interface QueryResponse {
  assistant_message: string;
  selected_tool_providers: string[];
  tool_execution: {
    ok: boolean;
    error?: string;
  };
  observable_trace: any[];
}

export interface McpStatus {
  status: string;
  detail: string;
  mcp_url?: string;
  diagnostics?: any;
  token_status?: any;
}
