import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../../environments/environment';

export interface DashboardStats {
  totalUsers: number;
  activeSessions: number;
  totalMessages: number;
  totalTransactions: number;
  usersToday: number;
  messagestoday: number;
}

export interface AdminUser {
  wallet: string;
  createdAt: string;
  updatedAt?: string;
  sessionCount?: number;
  messageCount?: number;
}

export interface UserListResponse {
  users: AdminUser[];
  total: number;
  page: number;
  limit: number;
}

export interface AdminTransaction {
  id: string;
  userId: string;
  userWallet: string;
  txHash?: string;
  chain: string;
  status: string;
  action: string;
  protocol?: string;
  parameters?: Record<string, unknown>;
  estimatedFee?: number;
  actualFee?: number;
  errorMessage?: string;
  initiatedAt: string;
  submittedAt?: string;
  confirmedAt?: string;
  createdAt: string;
}

export interface AdminSession {
  id: string;
  wallet: string;
  title: string;
  messageCount: number;
  createdAt: string;
  updatedAt: string;
}

/** A user-submitted report from Help → Report Issue. */
export interface AdminIssueReport {
  id: string;
  wallet: string;
  category: string;
  subject: string;
  description: string;
  /** Route, user agent, viewport — captured by the client at submit time. */
  context: Record<string, string> | null;
  status: 'open' | 'in_progress' | 'resolved' | 'dismissed';
  admin_note: string | null;
  created_at: string;
  updated_at: string;
}

export interface AuditLog {
  id: string;
  adminUsername: string;
  action: string;
  targetType?: string;
  targetId?: string;
  ipAddress?: string;
  details?: string;
  createdAt: string;
}

export interface IPLog {
  id: string;
  walletAddress: string;
  ipAddress: string;
  userAgent?: string;
  country?: string;
  success: boolean;
  failureReason?: string;
  loggedAt: string;
}

export interface IPWalletSummary {
  ipAddress: string;
  walletCount: number;
  loginCount: number;
  failCount: number;
  firstSeen: string;
  lastSeen: string;
  wallets: string[];
}

export interface AdminAdminUser {
  id: string;
  username: string;
  role: string;
  mustChangePassword: boolean;
  lastLoginAt?: string;
  createdAt: string;
}

export interface PaginatedResponse<T> {
  data: T[];
  total: number;
  page: number;
  limit: number;
  totalPages: number;
}

export interface AnalyticsTimeseries {
  timeseries: Array<{ date: string; users: number; sessions: number; tx: number; messages: number }>;
  days: number;
}

export interface LoginResponse {
  token: string;
  expiresAt: string;
}

@Injectable({ providedIn: 'root' })
export class AdminApiService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = environment.adminApiBase;

  /**
   * Admin login (username/password auth, separate from wallet auth).
   * The backend sets an HttpOnly `oprai-admin-token` cookie on success.
   */
  login(username: string, password: string): Observable<LoginResponse> {
    return this.http.post<LoginResponse>(`${this.baseUrl}/admin/auth/login`, {
      username,
      password,
    });
  }

  /**
   * Verify the current admin session cookie.
   * Returns 200 if the HttpOnly cookie is present and valid, 401 otherwise.
   * Used by the admin route guard for authoritative session validation.
   */
  verify(): Observable<void> {
    return this.http.get<void>(`${this.baseUrl}/admin/auth/verify`);
  }

  /**
   * Admin logout — instructs the backend to clear the HttpOnly cookie
   * by setting it with Max-Age=0. Must be called instead of
   * localStorage.removeItem so the session is actually invalidated.
   */
  adminLogout(): Observable<void> {
    return this.http.post<void>(`${this.baseUrl}/admin/auth/logout`, {});
  }

  /**
   * Get dashboard statistics
   */
  getDashboardStats(): Observable<DashboardStats> {
    return this.http.get<DashboardStats>(`${this.baseUrl}/admin/dashboard/stats`);
  }

  /**
   * Get users with pagination, search, and sorting
   */
  getUsers(params: {
    page?: number;
    limit?: number;
    search?: string;
    sortBy?: string;
    sortOrder?: 'asc' | 'desc';
  }): Observable<UserListResponse> {
    const queryParams: Record<string, string> = {};
    if (params.page) queryParams['page'] = params.page.toString();
    if (params.limit) queryParams['limit'] = params.limit.toString();
    if (params.search) queryParams['search'] = params.search;
    if (params.sortBy) queryParams['sortBy'] = params.sortBy;
    if (params.sortOrder) queryParams['sortOrder'] = params.sortOrder;

    return this.http.get<UserListResponse>(`${this.baseUrl}/admin/users`, {
      params: queryParams,
    });
  }

  /**
   * Get a single user by wallet address
   */
  getUser(wallet: string): Observable<AdminUser> {
    return this.http.get<AdminUser>(`${this.baseUrl}/admin/users/${wallet}`);
  }

  /**
   * Get transactions with pagination and filtering
   */
  getTransactions(params: {
    page?: number;
    limit?: number;
    status?: string;
    action?: string;
    search?: string;
    from?: string;
    to?: string;
  }): Observable<PaginatedResponse<AdminTransaction>> {
    const queryParams: Record<string, string> = {};
    if (params.page) queryParams['page'] = params.page.toString();
    if (params.limit) queryParams['limit'] = params.limit.toString();
    if (params.status) queryParams['status'] = params.status;
    if (params.action) queryParams['action'] = params.action;
    if (params.search) queryParams['search'] = params.search;
    if (params.from) queryParams['from'] = params.from;
    if (params.to) queryParams['to'] = params.to;

    return this.http.get<PaginatedResponse<AdminTransaction>>(
      `${this.baseUrl}/admin/transactions`,
      { params: queryParams }
    );
  }

  /**
   * Get a single transaction by ID
   */
  getTransaction(id: string): Observable<AdminTransaction> {
    return this.http.get<AdminTransaction>(`${this.baseUrl}/admin/transactions/${id}`);
  }

  /**
   * Get chat sessions with pagination
   */
  getSessions(params: {
    page?: number;
    limit?: number;
    search?: string;
    from?: string;
    to?: string;
  }): Observable<PaginatedResponse<AdminSession>> {
    const queryParams: Record<string, string> = {};
    if (params.page) queryParams['page'] = params.page.toString();
    if (params.limit) queryParams['limit'] = params.limit.toString();
    if (params.search) queryParams['search'] = params.search;
    if (params.from) queryParams['from'] = params.from;
    if (params.to) queryParams['to'] = params.to;

    return this.http.get<PaginatedResponse<AdminSession>>(
      `${this.baseUrl}/admin/sessions`,
      { params: queryParams }
    );
  }

  /**
   * Get messages for a specific session
   */
  getSessionMessages(sessionId: string): Observable<{ data: unknown[] }> {
    return this.http.get<{ data: unknown[] }>(
      `${this.baseUrl}/admin/sessions/${sessionId}/messages`
    );
  }

  /**
   * User issue reports (Help → Report Issue). Open ones come back first —
   * the queue's job is "what still needs an answer".
   */
  getIssueReports(params: {
    page?: number;
    limit?: number;
    status?: string;
    category?: string;
    search?: string;
  }): Observable<PaginatedResponse<AdminIssueReport>> {
    const queryParams: Record<string, string> = {};
    if (params.page) queryParams['page'] = params.page.toString();
    if (params.limit) queryParams['limit'] = params.limit.toString();
    if (params.status) queryParams['status'] = params.status;
    if (params.category) queryParams['category'] = params.category;
    if (params.search) queryParams['search'] = params.search;

    return this.http.get<PaginatedResponse<AdminIssueReport>>(
      `${this.baseUrl}/admin/issues`,
      { params: queryParams }
    );
  }

  /** Set a report's status and optionally leave a note the user will see. */
  updateIssueReport(id: string, status: string, note?: string): Observable<{ ok: boolean }> {
    return this.http.patch<{ ok: boolean }>(
      `${this.baseUrl}/admin/issues/${id}`,
      { status, note: note ?? '' }
    );
  }

  /**
   * Close / archive a session
   */
  closeSession(sessionId: string): Observable<{ ok: boolean }> {
    return this.http.post<{ ok: boolean }>(
      `${this.baseUrl}/admin/sessions/${sessionId}/close`,
      {}
    );
  }

  /**
   * Get audit logs with filtering
   */
  getAuditLogs(params: {
    page?: number;
    limit?: number;
    admin?: string;
    action?: string;
    from?: string;
    to?: string;
  }): Observable<PaginatedResponse<AuditLog>> {
    const queryParams: Record<string, string> = {};
    if (params.page) queryParams['page'] = params.page.toString();
    if (params.limit) queryParams['limit'] = params.limit.toString();
    if (params.admin) queryParams['admin'] = params.admin;
    if (params.action) queryParams['action'] = params.action;
    if (params.from) queryParams['from'] = params.from;
    if (params.to) queryParams['to'] = params.to;

    return this.http.get<PaginatedResponse<AuditLog>>(
      `${this.baseUrl}/admin/audit-logs`,
      { params: queryParams }
    );
  }

  /**
   * Get IP logs with filtering
   */
  getIPLogs(params: {
    page?: number;
    limit?: number;
    ip?: string;
    wallet?: string;
    success?: string;
    from?: string;
    to?: string;
  }): Observable<PaginatedResponse<IPLog>> {
    const queryParams: Record<string, string> = {};
    if (params.page) queryParams['page'] = params.page.toString();
    if (params.limit) queryParams['limit'] = params.limit.toString();
    if (params.ip) queryParams['ip'] = params.ip;
    if (params.wallet) queryParams['wallet'] = params.wallet;
    if (params.success) queryParams['success'] = params.success;
    if (params.from) queryParams['from'] = params.from;
    if (params.to) queryParams['to'] = params.to;

    return this.http.get<PaginatedResponse<IPLog>>(
      `${this.baseUrl}/admin/ip-logs`,
      { params: queryParams }
    );
  }

  /**
   * Get suspicious IPs (multiple wallets from one IP)
   */
  getSuspiciousIPs(minWallets?: number): Observable<{ data: IPWalletSummary[] }> {
    const queryParams: Record<string, string> = {};
    if (minWallets) queryParams['minWallets'] = minWallets.toString();

    return this.http.get<{ data: IPWalletSummary[] }>(
      `${this.baseUrl}/admin/ip-logs/suspicious`,
      { params: queryParams }
    );
  }

  /**
   * Get analytics timeseries data
   */
  getAnalyticsTimeseries(days?: number): Observable<AnalyticsTimeseries> {
    const queryParams: Record<string, string> = {};
    if (days) queryParams['days'] = days.toString();

    return this.http.get<AnalyticsTimeseries>(
      `${this.baseUrl}/admin/analytics/timeseries`,
      { params: queryParams }
    );
  }

  /**
   * Get top wallets by activity
   */
  getTopWallets(limit?: number): Observable<{ data: unknown[] }> {
    const queryParams: Record<string, string> = {};
    if (limit) queryParams['limit'] = limit.toString();

    return this.http.get<{ data: unknown[] }>(
      `${this.baseUrl}/admin/analytics/top-wallets`,
      { params: queryParams }
    );
  }

  /**
   * Get transaction counts grouped by action/protocol
   */
  getTxByProtocol(): Observable<{ data: Array<{ action: string; count: number }> }> {
    return this.http.get<{ data: Array<{ action: string; count: number }> }>(
      `${this.baseUrl}/admin/analytics/tx-by-protocol`
    );
  }

  /**
   * Get all admin users
   */
  getAdminUsers(): Observable<{ data: AdminAdminUser[] }> {
    return this.http.get<{ data: AdminAdminUser[] }>(`${this.baseUrl}/admin/admin-users`);
  }

  /**
   * Create a new admin user (password auto-generated)
   */
  createAdminUser(username: string): Observable<{ ok: boolean; admin: AdminAdminUser; generatedPassword: string }> {
    return this.http.post<{ ok: boolean; admin: AdminAdminUser; generatedPassword: string }>(
      `${this.baseUrl}/admin/admin-users`,
      { username }
    );
  }

  /**
   * Delete an admin user
   */
  deleteAdminUser(id: string): Observable<{ ok: boolean }> {
    return this.http.delete<{ ok: boolean }>(`${this.baseUrl}/admin/admin-users/${id}`);
  }

  /**
   * Reset an admin user's password (new password auto-generated)
   */
  resetAdminPassword(id: string): Observable<{ ok: boolean; generatedPassword: string }> {
    return this.http.post<{ ok: boolean; generatedPassword: string }>(
      `${this.baseUrl}/admin/admin-users/${id}/reset-password`,
      {}
    );
  }

  /**
   * Export audit logs as CSV
   */
  exportAuditLogs(from?: string, to?: string): Observable<Blob> {
    const queryParams: Record<string, string> = {};
    if (from) queryParams['from'] = from;
    if (to) queryParams['to'] = to;

    return this.http.get(`${this.baseUrl}/admin/audit-logs/export`, {
      params: queryParams,
      responseType: 'blob',
    });
  }

  /**
   * Export data as CSV
   */
  exportData(type: 'users' | 'transactions' | 'sessions'): Observable<Blob> {
    return this.http.get(`${this.baseUrl}/admin/export/${type}`, {
      responseType: 'blob',
    });
  }
}
