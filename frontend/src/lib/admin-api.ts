export interface AdminAccount {
  id: string;
  email: string;
  display_name: string;
  status: "active" | "suspended" | string;
  role: "user" | "admin" | string;
  quota_tokens: number;
  used_tokens: number;
  remaining_tokens: number;
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
