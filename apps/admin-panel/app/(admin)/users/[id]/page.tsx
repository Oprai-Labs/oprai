"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { AdminHeader } from "@/components/layout/admin-header";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { DataTable } from "@/components/data/data-table";
import { Pagination } from "@/components/ui/pagination";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { StatusDot } from "@/components/ui/status-dot";
import { Tabs } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { StatCard } from "@/components/ui/stat-card";
import { Dialog, DialogHeader, DialogTitle, DialogDescription, DialogFooter, DialogClose } from "@/components/ui/dialog";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { ToastContainer } from "@/components/ui/toast";
import {
  fetchUser,
  fetchUserSessions,
  fetchUserTransactions,
  fetchUserIpLogs,
  fetchUserAuditLogs,
  updateUserStatus,
  updateUserRole,
  type User,
  type PaginatedResponse,
  type Session,
  type Transaction,
  type IpLog,
  type AuditLog,
} from "@/lib/admin-api";
import { formatDate, truncateAddress } from "@/lib/utils";
import { useToast } from "@/hooks/use-toast";
import { MessageSquare, ArrowLeftRight, Globe, Shield, Copy } from "lucide-react";

type TabKey = "overview" | "sessions" | "transactions" | "ip-logs" | "audit";

export default function UserDetailPage() {
  const params = useParams();
  const router = useRouter();
  const userId = params.id as string;
  const { toasts, dismissToast, success, error } = useToast();

  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<TabKey>("overview");

  const [tabData, setTabData] = useState<PaginatedResponse<Session> | PaginatedResponse<Transaction> | PaginatedResponse<IpLog> | { data: AuditLog[] } | null>(null);
  const [tabLoading, setTabLoading] = useState(false);
  const [tabPage, setTabPage] = useState(1);

  // Dialogs
  const [statusDialog, setStatusDialog] = useState<{ open: boolean; status: string }>({ open: false, status: "" });
  const [statusReason, setStatusReason] = useState("");
  const [roleDialog, setRoleDialog] = useState(false);
  const [newRole, setNewRole] = useState("");
  const [actionLoading, setActionLoading] = useState(false);

  useEffect(() => {
    fetchUser(userId)
      .then(setUser)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [userId]);

  const loadTab = useCallback(() => {
    if (activeTab === "overview") return;
    setTabLoading(true);
    const p = { page: String(tabPage), limit: "20" };

    if (activeTab === "audit") {
      fetchUserAuditLogs(userId)
        .then((res) => setTabData({ data: res.data }))
        .catch(console.error)
        .finally(() => setTabLoading(false));
      return;
    }

    const fetcher =
      activeTab === "sessions" ? fetchUserSessions :
      activeTab === "transactions" ? fetchUserTransactions :
      fetchUserIpLogs;

    fetcher(userId, p)
      .then(setTabData)
      .catch(console.error)
      .finally(() => setTabLoading(false));
  }, [userId, activeTab, tabPage]);

  useEffect(() => { loadTab(); }, [loadTab]);

  const switchTab = (t: string) => {
    setActiveTab(t as TabKey);
    setTabPage(1);
    setTabData(null);
  };

  const handleStatusChange = async () => {
    setActionLoading(true);
    try {
      await updateUserStatus(userId, statusDialog.status, statusReason);
      // Re-fetch user to get the authoritative updated state from the backend
      const updated = await fetchUser(userId);
      setUser(updated);
      setStatusDialog({ open: false, status: "" });
      setStatusReason("");
      success(`User ${statusDialog.status} successfully`);
    } catch (err: unknown) {
      error(err instanceof Error ? err.message : "Failed to update status");
    } finally {
      setActionLoading(false);
    }
  };

  const handleRoleChange = async () => {
    if (!newRole) return;
    setActionLoading(true);
    try {
      await updateUserRole(userId, newRole);
      // Re-fetch user to get the authoritative updated state from the backend
      const updated = await fetchUser(userId);
      setUser(updated);
      setRoleDialog(false);
      setNewRole("");
      success("Role updated successfully");
    } catch (err: unknown) {
      error(err instanceof Error ? err.message : "Failed to update role");
    } finally {
      setActionLoading(false);
    }
  };

  const copyWallet = () => {
    navigator.clipboard.writeText(user?.wallet_address || "");
    success("Wallet copied to clipboard");
  };

  if (loading) {
    return (
      <div>
        <AdminHeader title="User Detail" breadcrumbs={[{ label: "Users", href: "/users" }, { label: "Loading..." }]} />
        <div className="p-8"><Skeleton className="h-64" /></div>
      </div>
    );
  }

  if (!user) {
    return (
      <div>
        <AdminHeader title="User Not Found" breadcrumbs={[{ label: "Users", href: "/users" }, { label: "Not Found" }]} />
        <div className="p-8 text-fg-muted">User not found.</div>
      </div>
    );
  }

  const statusBadgeVariant = (s: string) => {
    if (s === "confirmed") return "success" as const;
    if (s === "pending") return "warning" as const;
    if (s === "failed") return "error" as const;
    return "default" as const;
  };

  const userStatus = user.status || "active";

  const tabItems = [
    { key: "overview", label: "Overview" },
    { key: "sessions", label: "Sessions" },
    { key: "transactions", label: "Transactions" },
    { key: "ip-logs", label: "IP History" },
    { key: "audit", label: "Audit Log" },
  ];

  return (
    <div>
      <AdminHeader
        title={user.display_name || truncateAddress(user.wallet_address, 8)}
        breadcrumbs={[
          { label: "Users", href: "/users" },
          { label: truncateAddress(user.wallet_address, 6) },
        ]}
        actions={
          <div className="flex gap-2">
            {userStatus === "active" && (
              <>
                <Button variant="outline" size="sm" onClick={() => setStatusDialog({ open: true, status: "suspended" })}>
                  Suspend
                </Button>
                <Button variant="destructive" size="sm" onClick={() => setStatusDialog({ open: true, status: "banned" })}>
                  Ban
                </Button>
              </>
            )}
            {(userStatus === "suspended" || userStatus === "banned") && (
              <Button variant="default" size="sm" onClick={() => setStatusDialog({ open: true, status: "active" })}>
                Reactivate
              </Button>
            )}
            <Button variant="outline" size="sm" onClick={() => { setNewRole(user.role); setRoleDialog(true); }}>
              Change Role
            </Button>
          </div>
        }
      />

      <div className="space-y-6 p-8">
        {/* Profile card */}
        <Card>
          <CardContent className="p-6">
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <div>
                <p className="text-xs text-fg-muted">Wallet</p>
                <div className="mt-1 flex items-center gap-1">
                  <p className="font-mono text-sm">{truncateAddress(user.wallet_address, 8)}</p>
                  <button onClick={copyWallet} className="rounded p-0.5 text-fg-muted hover:text-fg">
                    <Copy className="h-3 w-3" />
                  </button>
                </div>
              </div>
              <div>
                <p className="text-xs text-fg-muted">Display Name</p>
                <p className="mt-1 text-sm">{user.display_name || "-"}</p>
              </div>
              <div>
                <p className="text-xs text-fg-muted">Status</p>
                <p className="mt-1"><StatusDot status={userStatus} /></p>
              </div>
              <div>
                <p className="text-xs text-fg-muted">Role</p>
                <p className="mt-1"><Badge>{user.role}</Badge></p>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Stats row */}
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <StatCard title="Sessions" value={Number(user.session_count || 0)} icon={MessageSquare} />
          <StatCard title="Transactions" value={Number(user.tx_count || 0)} icon={ArrowLeftRight} />
          <StatCard title="Messages" value={Number(user.message_count || 0)} icon={MessageSquare} />
          <StatCard title="Logins" value={Number(user.login_count || 0)} icon={Globe} />
        </div>

        {/* Tabs */}
        <Tabs tabs={tabItems} active={activeTab} onChange={switchTab} />

        {activeTab === "overview" && (
          <Card>
            <CardContent className="p-6">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <p className="text-xs text-fg-muted">Created</p>
                  <p className="mt-1 text-sm">{formatDate(user.created_at)}</p>
                </div>
                {user.status_changed_at && (
                  <>
                    <div>
                      <p className="text-xs text-fg-muted">Status Changed</p>
                      <p className="mt-1 text-sm">{formatDate(user.status_changed_at)}</p>
                    </div>
                    <div>
                      <p className="text-xs text-fg-muted">Changed By</p>
                      <p className="mt-1 text-sm">{user.status_changed_by || "-"}</p>
                    </div>
                    {user.status_reason && (
                      <div>
                        <p className="text-xs text-fg-muted">Reason</p>
                        <p className="mt-1 text-sm">{user.status_reason}</p>
                      </div>
                    )}
                  </>
                )}
              </div>
            </CardContent>
          </Card>
        )}

        {activeTab !== "overview" && (
          <Card>
            <CardContent className="p-0">
              {activeTab === "sessions" && (
                <DataTable
                  loading={tabLoading}
                  columns={[
                    { key: "title", header: "Title", render: (s) => s.title || "Untitled" },
                    { key: "message_count", header: "Messages", render: (s) => s.message_count ?? 0 },
                    { key: "created_at", header: "Created", render: (s) => formatDate(s.created_at) },
                  ]}
                  data={tabData?.data || []}
                  onRowClick={(s) => router.push(`/sessions/${s.id}`)}
                />
              )}
              {activeTab === "transactions" && (
                <DataTable
                  loading={tabLoading}
                  columns={[
                    { key: "action", header: "Action", render: (t) => <Badge>{t.action}</Badge> },
                    { key: "protocol", header: "Protocol", render: (t) => t.protocol || "-" },
                    { key: "status", header: "Status", render: (t) => (
                      <Badge variant={statusBadgeVariant(t.status)}>{t.status}</Badge>
                    )},
                    { key: "tx_hash", header: "TX Hash", render: (t) => t.tx_hash ? (
                      <span className="font-mono text-xs">{truncateAddress(t.tx_hash, 8)}</span>
                    ) : "-" },
                    { key: "created_at", header: "Date", render: (t) => formatDate(t.created_at) },
                  ]}
                  data={tabData?.data || []}
                />
              )}
              {activeTab === "ip-logs" && (
                <DataTable
                  loading={tabLoading}
                  columns={[
                    { key: "ip_address", header: "IP Address", render: (l) => (
                      <span className="font-mono text-xs">{l.ip_address}</span>
                    )},
                    { key: "user_agent", header: "User Agent", className: "max-w-xs", render: (l) => (
                      <span className="block truncate text-xs text-fg-muted" title={l.user_agent}>
                        {l.user_agent}
                      </span>
                    )},
                    { key: "created_at", header: "Timestamp", render: (l) => formatDate(l.created_at) },
                  ]}
                  data={tabData?.data || []}
                />
              )}
              {activeTab === "audit" && (
                <DataTable
                  loading={tabLoading}
                  columns={[
                    { key: "created_at", header: "Timestamp", render: (l) => formatDate(l.created_at) },
                    { key: "admin_username", header: "Admin" },
                    { key: "action", header: "Action", render: (l) => <Badge>{l.action}</Badge> },
                    { key: "details", header: "Details", render: (l) => (
                      <span className="text-xs text-fg-muted">
                        {l.details ? JSON.stringify(l.details) : "-"}
                      </span>
                    )},
                  ]}
                  data={tabData?.data || []}
                />
              )}
              {tabData?.totalPages > 1 && (
                <Pagination
                  page={tabData.page}
                  totalPages={tabData.totalPages}
                  onPrev={() => setTabPage((p) => Math.max(1, p - 1))}
                  onNext={() => setTabPage((p) => p + 1)}
                />
              )}
            </CardContent>
          </Card>
        )}
      </div>

      {/* Status change dialog */}
      <Dialog open={statusDialog.open} onClose={() => setStatusDialog({ open: false, status: "" })}>
        <DialogClose onClose={() => setStatusDialog({ open: false, status: "" })} />
        <DialogHeader>
          <DialogTitle>
            {statusDialog.status === "active" ? "Reactivate" : statusDialog.status === "suspended" ? "Suspend" : "Ban"} User
          </DialogTitle>
          <DialogDescription>
            {statusDialog.status === "active"
              ? "This will restore the user's access."
              : `This will ${statusDialog.status === "suspended" ? "temporarily suspend" : "permanently ban"} the user.`}
          </DialogDescription>
        </DialogHeader>
        <div className="mt-4">
          <label className="text-sm text-fg-muted">Reason (optional)</label>
          <Textarea
            value={statusReason}
            onChange={(e) => setStatusReason(e.target.value)}
            placeholder="Provide a reason..."
            className="mt-1"
          />
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => setStatusDialog({ open: false, status: "" })} disabled={actionLoading}>
            Cancel
          </Button>
          <Button
            variant={statusDialog.status === "active" ? "default" : "destructive"}
            onClick={handleStatusChange}
            disabled={actionLoading}
          >
            {actionLoading ? "Processing..." : "Confirm"}
          </Button>
        </DialogFooter>
      </Dialog>

      {/* Role change dialog */}
      <Dialog open={roleDialog} onClose={() => setRoleDialog(false)}>
        <DialogClose onClose={() => setRoleDialog(false)} />
        <DialogHeader>
          <DialogTitle>Change User Role</DialogTitle>
          <DialogDescription>Update the role for this user.</DialogDescription>
        </DialogHeader>
        <div className="mt-4">
          <label className="text-sm text-fg-muted">New Role</label>
          <Input
            value={newRole}
            onChange={(e) => setNewRole(e.target.value)}
            placeholder="e.g. user, premium, moderator"
            className="mt-1"
          />
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => setRoleDialog(false)} disabled={actionLoading}>
            Cancel
          </Button>
          <Button onClick={handleRoleChange} disabled={actionLoading || !newRole}>
            {actionLoading ? "Updating..." : "Update Role"}
          </Button>
        </DialogFooter>
      </Dialog>

      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}
