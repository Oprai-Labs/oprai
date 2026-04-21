"use client";

import { useEffect, useState } from "react";
import { AdminHeader } from "@/components/layout/admin-header";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { DataTable } from "@/components/data/data-table";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogHeader, DialogTitle, DialogDescription, DialogFooter, DialogClose } from "@/components/ui/dialog";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Input } from "@/components/ui/input";
import { ToastContainer } from "@/components/ui/toast";
import { DropdownMenu, DropdownItem } from "@/components/ui/dropdown-menu";
import { fetchAdminUsers, createAdminUser, deleteAdminUser, resetAdminPassword } from "@/lib/admin-api";
import { formatDate } from "@/lib/utils";
import { useToast } from "@/hooks/use-toast";
import { useAdminAuth } from "@/hooks/use-admin-auth";
import { Plus, Key, Trash2, Copy } from "lucide-react";

export default function SettingsPage() {
  const { toasts, dismissToast, success, error } = useToast();
  const { username: currentAdmin } = useAdminAuth();

  const [admins, setAdmins] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  // Dialogs
  const [createDialog, setCreateDialog] = useState(false);
  const [newUsername, setNewUsername] = useState("");
  const [generatedPassword, setGeneratedPassword] = useState<string | null>(null);
  const [deleteDialog, setDeleteDialog] = useState<{ open: boolean; id: string; username: string }>({ open: false, id: "", username: "" });
  const [resetDialog, setResetDialog] = useState<{ open: boolean; id: string; username: string }>({ open: false, id: "", username: "" });
  const [resetPassword, setResetPassword] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);

  const loadAdmins = () => {
    setLoading(true);
    fetchAdminUsers()
      .then((res) => setAdmins(res.data))
      .catch(console.error)
      .finally(() => setLoading(false));
  };

  useEffect(() => { loadAdmins(); }, []);

  const handleCreate = async () => {
    if (!newUsername) return;
    setActionLoading(true);
    try {
      const result = await createAdminUser(newUsername);
      setGeneratedPassword(result.generatedPassword);
      setNewUsername("");
      loadAdmins();
      success("Admin user created");
    } catch (err: unknown) {
      error(err instanceof Error ? err.message : "Failed to create admin user");
    } finally {
      setActionLoading(false);
    }
  };

  const handleDelete = async () => {
    setActionLoading(true);
    try {
      await deleteAdminUser(deleteDialog.id);
      setDeleteDialog({ open: false, id: "", username: "" });
      loadAdmins();
      success("Admin user deleted");
    } catch (err: unknown) {
      error(err instanceof Error ? err.message : "Failed to delete admin user");
    } finally {
      setActionLoading(false);
    }
  };

  const handleReset = async () => {
    setActionLoading(true);
    try {
      const result = await resetAdminPassword(resetDialog.id);
      setResetPassword(result.generatedPassword);
      success("Password reset successfully");
    } catch (err: unknown) {
      error(err instanceof Error ? err.message : "Failed to reset password");
    } finally {
      setActionLoading(false);
    }
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    success("Copied to clipboard");
  };

  return (
    <div>
      <AdminHeader title="Settings" description="Manage admin users and system settings" />
      <div className="space-y-6 p-8">
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle>Admin Users</CardTitle>
              <Button size="sm" onClick={() => { setCreateDialog(true); setGeneratedPassword(null); }} className="gap-2">
                <Plus className="h-4 w-4" /> Create Admin
              </Button>
            </div>
          </CardHeader>
          <CardContent className="p-0">
            <DataTable
              loading={loading}
              columns={[
                { key: "username", header: "Username", render: (a) => (
                  <span className="font-medium">{a.username}</span>
                )},
                { key: "role", header: "Role", render: (a) => <Badge>{a.role}</Badge> },
                { key: "createdAt", header: "Created", render: (a) => formatDate(a.createdAt) },
              ]}
              data={admins}
              rowActions={(a) => (
                <DropdownMenu>
                  <DropdownItem onClick={() => { setResetDialog({ open: true, id: a.id, username: a.username }); setResetPassword(null); }}>
                    <Key className="h-4 w-4" /> Reset Password
                  </DropdownItem>
                  {a.username !== currentAdmin && (
                    <DropdownItem onClick={() => setDeleteDialog({ open: true, id: a.id, username: a.username })} variant="destructive">
                      <Trash2 className="h-4 w-4" /> Delete
                    </DropdownItem>
                  )}
                </DropdownMenu>
              )}
            />
          </CardContent>
        </Card>
      </div>

      {/* Create Admin Dialog */}
      <Dialog open={createDialog} onClose={() => setCreateDialog(false)}>
        <DialogClose onClose={() => setCreateDialog(false)} />
        <DialogHeader>
          <DialogTitle>Create Admin User</DialogTitle>
          <DialogDescription>A secure password will be auto-generated.</DialogDescription>
        </DialogHeader>
        {generatedPassword ? (
          <div className="mt-4">
            <p className="text-sm text-fg-muted">Save this password - it will not be shown again:</p>
            <div className="mt-2 flex items-center gap-2 rounded-lg bg-bg-hover p-3">
              <code className="flex-1 font-mono text-sm text-emerald-400">{generatedPassword}</code>
              <button onClick={() => copyToClipboard(generatedPassword)} className="text-fg-muted hover:text-fg">
                <Copy className="h-4 w-4" />
              </button>
            </div>
            <DialogFooter>
              <Button onClick={() => setCreateDialog(false)}>Done</Button>
            </DialogFooter>
          </div>
        ) : (
          <>
            <div className="mt-4">
              <label className="text-sm text-fg-muted">Username</label>
              <Input
                value={newUsername}
                onChange={(e) => setNewUsername(e.target.value)}
                placeholder="Enter username (min 3 characters)"
                className="mt-1"
              />
            </div>
            <DialogFooter>
              <Button variant="ghost" onClick={() => setCreateDialog(false)} disabled={actionLoading}>
                Cancel
              </Button>
              <Button onClick={handleCreate} disabled={actionLoading || newUsername.length < 3}>
                {actionLoading ? "Creating..." : "Create"}
              </Button>
            </DialogFooter>
          </>
        )}
      </Dialog>

      {/* Delete Admin Dialog */}
      <AlertDialog
        open={deleteDialog.open}
        onClose={() => setDeleteDialog({ open: false, id: "", username: "" })}
        onConfirm={handleDelete}
        title={`Delete Admin: ${deleteDialog.username}`}
        description="This action cannot be undone. The admin user will lose all access."
        confirmLabel="Delete"
        loading={actionLoading}
      />

      {/* Reset Password Dialog */}
      <Dialog open={resetDialog.open} onClose={() => setResetDialog({ open: false, id: "", username: "" })}>
        <DialogClose onClose={() => setResetDialog({ open: false, id: "", username: "" })} />
        <DialogHeader>
          <DialogTitle>Reset Password: {resetDialog.username}</DialogTitle>
          <DialogDescription>A new secure password will be generated.</DialogDescription>
        </DialogHeader>
        {resetPassword ? (
          <div className="mt-4">
            <p className="text-sm text-fg-muted">New password (save this - shown once):</p>
            <div className="mt-2 flex items-center gap-2 rounded-lg bg-bg-hover p-3">
              <code className="flex-1 font-mono text-sm text-emerald-400">{resetPassword}</code>
              <button onClick={() => copyToClipboard(resetPassword)} className="text-fg-muted hover:text-fg">
                <Copy className="h-4 w-4" />
              </button>
            </div>
            <DialogFooter>
              <Button onClick={() => setResetDialog({ open: false, id: "", username: "" })}>Done</Button>
            </DialogFooter>
          </div>
        ) : (
          <DialogFooter>
            <Button variant="ghost" onClick={() => setResetDialog({ open: false, id: "", username: "" })} disabled={actionLoading}>
              Cancel
            </Button>
            <Button onClick={handleReset} disabled={actionLoading}>
              {actionLoading ? "Resetting..." : "Reset Password"}
            </Button>
          </DialogFooter>
        )}
      </Dialog>

      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}
