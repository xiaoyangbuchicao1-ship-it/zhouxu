export interface AdminAccount {
  id: string;
  email: string;
  display_name: string;
  status: "active" | "suspended" | string;
  role: "user" | "admin" | string;
  quota_tokens: number;
  used_tokens: number;
  remaining_tokens: number;
  last_login_ip?: string | null;
  last_login_at?: string | null;
}

export interface AdminUsageLog {
  id: string;
  account_id: string;
  email?: string | null;
  display_name?: string | null;
  run_id: string;
  conversation_id?: string | null;
  model: string;
  mode: string;
  status: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  created_at: string;
  completed_at?: string | null;
}

export interface AdminAuditLog {
  id: string;
  event_id: string;
  actor_account_id?: string | null;
  actor_email?: string | null;
  actor_role: string;
  target_account_id?: string | null;
  target_email?: string | null;
  category: string;
  action: string;
  success: boolean;
  status_code: number;
  request_id?: string | null;
  ip_address?: string | null;
  user_agent?: string | null;
  details: Record<string, unknown>;
  created_at: string;
}

export interface AdminUserActivity {
  id: string;
  kind: "audit" | "usage" | string;
  account_id: string;
  email?: string | null;
  action: string;
  status: string;
  ip_address?: string | null;
  details: Record<string, unknown>;
  created_at: string;
}

export class AdminApiError extends Error {
  constructor(public code: string, public status = 0) {
    super(code);
    this.name = "AdminApiError";
  }
}

const apiBase = (import.meta.env.VITE_API_BASE_URL || "/api").replace(/\/$/, "");

async function request<T>(token: string, path: string, options: RequestInit = {}) {
  const response = await fetch(`${apiBase}${path}`, {
    ...options,
    cache: "no-store",
    credentials: "omit",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...(options.headers || {}),
    },
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    throw new AdminApiError(typeof data?.detail === "string" ? data.detail : "request_failed", response.status);
  }
  return data as T;
}

export async function listAdminUsers(token: string, params: { search?: string; status?: string } = {}) {
  const query = new URLSearchParams();
  query.set("limit", "200");
  if (params.search) query.set("search", params.search);
  if (params.status && params.status !== "all") query.set("status", params.status);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return request<{ accounts: AdminAccount[]; total: number }>(token, `/admin/users${suffix}`);
}

export async function updateAdminUser(
  token: string,
  accountId: string,
  changes: Partial<Pick<AdminAccount, "status" | "role" | "quota_tokens" | "used_tokens">>,
) {
  return request<AdminAccount>(token, `/admin/users/${encodeURIComponent(accountId)}`, {
    method: "PATCH",
    body: JSON.stringify(changes),
  });
}

export async function resetAdminUserPassword(token: string, accountId: string, password: string) {
  await request<null>(token, `/admin/users/${encodeURIComponent(accountId)}/password`, {
    method: "POST",
    body: JSON.stringify({ password }),
  });
}

export async function deleteAdminUser(token: string, accountId: string) {
  await request<null>(token, `/admin/users/${encodeURIComponent(accountId)}`, {
    method: "DELETE",
  });
}

export async function listAdminUsageLogs(token: string, params: { search?: string; status?: string } = {}) {
  const query = new URLSearchParams({ limit: "200" });
  if (params.search) query.set("search", params.search);
  if (params.status && params.status !== "all") query.set("status", params.status);
  return request<{ items: AdminUsageLog[]; total: number }>(token, `/admin/logs/usage?${query.toString()}`);
}

export async function listAdminAuditLogs(token: string, params: { search?: string; category?: string } = {}) {
  const query = new URLSearchParams({ limit: "200" });
  if (params.search) query.set("search", params.search);
  if (params.category && params.category !== "all") query.set("category", params.category);
  return request<{ items: AdminAuditLog[]; total: number }>(token, `/admin/logs/audit?${query.toString()}`);
}

export async function listAdminUserActivity(token: string, accountId: string) {
  return request<{ items: AdminUserActivity[]; total: number }>(
    token,
    `/admin/users/${encodeURIComponent(accountId)}/activity?limit=200`,
  );
}
