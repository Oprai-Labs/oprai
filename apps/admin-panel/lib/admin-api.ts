// ─── Response types ────────────────────────────────────────────────────────────

export interface User {
  id: string;
  wallet_address: string;
  display_name: string | null;
  status: string;
  role: string;
  session_count: number;
  tx_count: number;
  created_at: string;
  updated_at?: string;
}

export interface UserOverview {
  user: User;
  sessionCount: number;
  txCount: number;
  totalVolumeSol: number;
  lastActive: string | null;
}

export interface Transaction {
  id: string;
  user_wallet: string;
  action: string;
  status: string;
  signature: string | null;
  amount_sol: number | null;
  created_at: string;
}

export interface Session {
  id: string;
  user_wallet: string;
  title: string;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
}

export interface IpLog {
  id: string;
  user_wallet: string;
  ip_address: string;
  user_agent: string | null;
  created_at: string;
}

export interface AuditLog {
  id: string;
  admin_username: string;
  action: string;
  target_type: string | null;
  target_id: string | null;
  details: string | null;
  created_at: string;
}

export interface AdminUser {
  id: string;
  username: string;
  created_at: string;
}

export interface DashboardStats {
  totalUsers: number;
  usersToday: number;
  totalTransactions: number;
  txToday: number;
  sessionsActive: number;
  totalSessions: number;
  messagesToday: number;
  totalMessages: number;
  txByStatus: Array<{ status: string; count: number }>;
  txByAction: Array<{ action: string; count: number }>;
  recentUsers: User[];
  recentTransactions: Transaction[];
  trends: {
    users7dAgo: number;
    tx7dAgo: number;
  };
}

export interface AnalyticsOverview {
  totalUsers: number;
  newUsers: number;
  totalTransactions: number;
  totalSessions: number;
  dailyActiveUsers: Array<{ date: string; count: number }>;
  dailyTransactions: Array<{ date: string; count: number }>;
  volumeByDay: Array<{ date: string; volume: number }>;
}

export interface TopUser {
  wallet_address: string;
  display_name: string | null;
  tx_count: number;
  total_volume_sol: number;
}

export interface ProtocolStat {
  protocol: string;
  count: number;
  volume_sol: number;
}

export interface ActionStat {
  action: string;
  count: number;
  date: string;
}

export interface PaginatedResponse<T> {
  data: T[];
  page: number;
  pageSize: number;
  total: number;
  totalPages: number;
}

// ─── Client setup ──────────────────────────────────────────────────────────────

const ADMIN_API_URL = (
  process.env.NEXT_PUBLIC_ADMIN_API_BASE?.replace(/\/$/, "") || "http://localhost:3050"
);

export class AdminApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
    this.name = "AdminApiError";
  }
}

interface ApiOptions extends Omit<RequestInit, "headers"> {
  headers?: Record<string, string>;
}

// Default generic is `unknown` (not `any`) so callers that omit T get a type error
// instead of silently receiving an untyped value. Pass an explicit type argument
// for every endpoint function defined below.
//
// All requests use `credentials: 'include'` so the oprai-admin-token HttpOnly cookie
// is sent automatically. No manual token header management needed.
export async function adminApi<T = unknown>(path: string, options: ApiOptions = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...options.headers,
  };

  const url = `${ADMIN_API_URL}${path}`;
  const response = await fetch(url, {
    ...options,
    headers,
    credentials: "include", // sends oprai-admin-token HttpOnly cookie
  });

  if (response.status === 401) {
    if (typeof window !== "undefined") {
      window.location.href = "/login";
    }
    throw new AdminApiError("Unauthorized", 401);
  }

  if (!response.ok) {
    const body = await response.json().catch(() => ({ error: response.statusText })) as { error?: string };
    throw new AdminApiError(body.error || response.statusText, response.status);
  }

  return response.json() as Promise<T>;
}

function qs(params?: Record<string, string>): string {
  if (!params) return "";
  const filtered = Object.fromEntries(Object.entries(params).filter(([, v]) => v));
  if (Object.keys(filtered).length === 0) return "";
  return `?${new URLSearchParams(filtered).toString()}`;
}

// ─── Auth ──────────────────────────────────────────────────────────────────────

export interface LoginResponse {
  ok: boolean;
  token: string;
  expiresAt: string;
  username: string;
}

export async function adminLogin(username: string, password: string): Promise<LoginResponse> {
  // The backend sets the oprai-admin-token HttpOnly cookie on success.
  // No localStorage storage needed.
  return adminApi<LoginResponse>("/admin/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export interface VerifyResponse {
  ok: boolean;
  username: string;
}

export async function adminVerify(): Promise<VerifyResponse> {
  return adminApi<VerifyResponse>("/admin/auth/verify");
}

export async function adminLogout(): Promise<void> {
  await adminApi("/admin/auth/logout", { method: "POST" }).catch(() => {});
}

// ─── Dashboard ─────────────────────────────────────────────────────────────────

export async function fetchDashboardStats(): Promise<DashboardStats> {
  return adminApi<DashboardStats>("/admin/dashboard/stats");
}

// ─── Users ─────────────────────────────────────────────────────────────────────

export async function fetchUsers(params?: Record<string, string>): Promise<PaginatedResponse<User>> {
  return adminApi<PaginatedResponse<User>>(`/admin/users${qs(params)}`);
}

export async function fetchUser(id: string): Promise<User> {
  return adminApi<User>(`/admin/users/${id}`);
}

export async function fetchUserOverview(id: string): Promise<UserOverview> {
  return adminApi<UserOverview>(`/admin/users/${id}/overview`);
}

export async function fetchUserSessions(id: string, params?: Record<string, string>): Promise<PaginatedResponse<Session>> {
  return adminApi<PaginatedResponse<Session>>(`/admin/users/${id}/sessions${qs(params)}`);
}

export async function fetchUserTransactions(id: string, params?: Record<string, string>): Promise<PaginatedResponse<Transaction>> {
  return adminApi<PaginatedResponse<Transaction>>(`/admin/users/${id}/transactions${qs(params)}`);
}

export async function fetchUserIpLogs(id: string, params?: Record<string, string>): Promise<PaginatedResponse<IpLog>> {
  return adminApi<PaginatedResponse<IpLog>>(`/admin/users/${id}/ip-logs${qs(params)}`);
}

export async function fetchUserAuditLogs(id: string): Promise<{ data: AuditLog[] }> {
  return adminApi<{ data: AuditLog[] }>(`/admin/users/${id}/audit-logs`);
}

export interface UpdateStatusResponse { ok: boolean }
export async function updateUserStatus(id: string, status: string, reason?: string): Promise<UpdateStatusResponse> {
  return adminApi<UpdateStatusResponse>(`/admin/users/${id}/status`, {
    method: "PATCH",
    body: JSON.stringify({ status, reason }),
  });
}

export interface UpdateRoleResponse { ok: boolean }
export async function updateUserRole(id: string, role: string): Promise<UpdateRoleResponse> {
  return adminApi<UpdateRoleResponse>(`/admin/users/${id}/role`, {
    method: "PATCH",
    body: JSON.stringify({ role }),
  });
}

// ─── Transactions ──────────────────────────────────────────────────────────────

export async function fetchTransactions(params?: Record<string, string>): Promise<PaginatedResponse<Transaction>> {
  return adminApi<PaginatedResponse<Transaction>>(`/admin/transactions${qs(params)}`);
}

export async function fetchTransaction(id: string): Promise<Transaction> {
  return adminApi<Transaction>(`/admin/transactions/${id}`);
}

// ─── Sessions ──────────────────────────────────────────────────────────────────

export async function fetchSessions(params?: Record<string, string>): Promise<PaginatedResponse<Session>> {
  return adminApi<PaginatedResponse<Session>>(`/admin/sessions${qs(params)}`);
}

export async function fetchSessionMessages(id: string): Promise<{ messages: Message[] }> {
  return adminApi<{ messages: Message[] }>(`/admin/sessions/${id}/messages`);
}

export async function closeSession(id: string): Promise<{ ok: boolean }> {
  return adminApi<{ ok: boolean }>(`/admin/sessions/${id}/close`, { method: "POST" });
}

// ─── IP Logs ───────────────────────────────────────────────────────────────────

export async function fetchIpLogs(params?: Record<string, string>): Promise<PaginatedResponse<IpLog>> {
  return adminApi<PaginatedResponse<IpLog>>(`/admin/ip-logs${qs(params)}`);
}

export async function fetchIpLogsByIp(ip: string): Promise<IpLog[]> {
  return adminApi<IpLog[]>(`/admin/ip-logs/by-ip/${encodeURIComponent(ip)}`);
}

// ─── Analytics ─────────────────────────────────────────────────────────────────

export async function fetchAnalyticsOverview(days = 30): Promise<AnalyticsOverview> {
  // Clamp and validate `days` before embedding in URL
  const safeDays = Math.max(1, Math.min(365, Math.floor(Number(days) || 30)));
  return adminApi<AnalyticsOverview>(`/admin/analytics/overview?days=${safeDays}`);
}

export async function fetchTopUsers(): Promise<TopUser[]> {
  return adminApi<TopUser[]>(`/admin/analytics/top-users`);
}

export async function fetchProtocolStats(): Promise<ProtocolStat[]> {
  return adminApi<ProtocolStat[]>(`/admin/analytics/protocol-stats`);
}

export async function fetchActionStats(days = 30): Promise<ActionStat[]> {
  const safeDays = Math.max(1, Math.min(365, Math.floor(Number(days) || 30)));
  return adminApi<ActionStat[]>(`/admin/analytics/action-stats?days=${safeDays}`);
}

// ─── Audit Logs ────────────────────────────────────────────────────────────────

export async function fetchAuditLogs(params?: Record<string, string>): Promise<PaginatedResponse<AuditLog>> {
  return adminApi<PaginatedResponse<AuditLog>>(`/admin/audit-logs${qs(params)}`);
}

// ─── Admin Users ───────────────────────────────────────────────────────────────

export async function fetchAdminUsers(): Promise<{ data: AdminUser[] }> {
  return adminApi<{ data: AdminUser[] }>("/admin/admin-users");
}

export interface CreateAdminUserResponse {
  ok: boolean;
  admin: AdminUser;
  generatedPassword: string;
}

export async function createAdminUser(username: string): Promise<CreateAdminUserResponse> {
  return adminApi<CreateAdminUserResponse>("/admin/admin-users", {
    method: "POST",
    body: JSON.stringify({ username }),
  });
}

export async function deleteAdminUser(id: string): Promise<{ ok: boolean }> {
  return adminApi<{ ok: boolean }>(`/admin/admin-users/${id}`, { method: "DELETE" });
}

export interface ResetPasswordResponse { ok: boolean; generatedPassword: string }
export async function resetAdminPassword(id: string): Promise<ResetPasswordResponse> {
  return adminApi<ResetPasswordResponse>(`/admin/admin-users/${id}/password`, {
    method: "PATCH",
  });
}

// ─── Export ────────────────────────────────────────────────────────────────────

export async function downloadCsv(type: "users" | "transactions" | "sessions" | "ip-logs", params?: Record<string, string>): Promise<void> {
  const url = `${ADMIN_API_URL}/admin/export/${type}${qs(params)}`;
  const response = await fetch(url, {
    credentials: "include", // sends oprai-admin-token HttpOnly cookie
  });

  if (!response.ok) throw new AdminApiError("Export failed", response.status);

  const blob = await response.blob();
  const downloadUrl = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = downloadUrl;
  link.download = `oprai-${type}-${new Date().toISOString().split("T")[0]}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(downloadUrl);
}
