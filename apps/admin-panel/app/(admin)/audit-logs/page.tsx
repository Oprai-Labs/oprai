"use client";

import { useEffect, useState, useCallback } from "react";
import { AdminHeader } from "@/components/layout/admin-header";
import { Card, CardContent } from "@/components/ui/card";
import { DataTable } from "@/components/data/data-table";
import { FilterBar } from "@/components/data/filter-bar";
import { Pagination } from "@/components/ui/pagination";
import { Badge } from "@/components/ui/badge";
import { fetchAuditLogs, type PaginatedResponse, type AuditLog } from "@/lib/admin-api";
import { formatDate } from "@/lib/utils";
import { useFilters } from "@/hooks/use-filters";
import { AUDIT_ACTION_LABELS } from "@/lib/constants";
import Link from "next/link";

const ACTION_OPTIONS = Object.entries(AUDIT_ACTION_LABELS).map(([value, label]) => ({ value, label }));

const TARGET_TYPE_OPTIONS = [
  { value: "user", label: "User" },
  { value: "session", label: "Session" },
  { value: "admin", label: "Admin" },
  { value: "export", label: "Export" },
];

export default function AuditLogsPage() {
  const { filters, setFilter, setSearch, clearFilters, nextPage, prevPage, toParams } = useFilters({
    search: "", action: "", targetType: "", from: "", to: "",
  });
  const [data, setData] = useState<PaginatedResponse<AuditLog> | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    fetchAuditLogs(toParams())
      .then(setData)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [toParams]);

  useEffect(() => { load(); }, [load]);

  const getTargetLink = (targetType: string, targetId: string) => {
    if (targetType === "user") return `/users/${targetId}`;
    if (targetType === "session") return `/sessions/${targetId}`;
    return null;
  };

  return (
    <div>
      <AdminHeader title="Audit Logs" description="Track all admin actions" />
      <div className="space-y-4 p-8">
        <FilterBar
          searchPlaceholder="Search..."
          onSearch={setSearch}
          filters={[
            {
              key: "action",
              label: "All Actions",
              options: ACTION_OPTIONS,
              value: filters.action,
              onChange: (v) => setFilter("action", v),
            },
            {
              key: "targetType",
              label: "All Targets",
              options: TARGET_TYPE_OPTIONS,
              value: filters.targetType,
              onChange: (v) => setFilter("targetType", v),
            },
          ]}
          dateRange={{
            from: filters.from,
            to: filters.to,
            onChange: (from, to) => { setFilter("from", from); setFilter("to", to); },
          }}
          onClear={clearFilters}
        />
        <Card>
          <CardContent className="p-0">
            <DataTable
              loading={loading}
              columns={[
                { key: "created_at", header: "Timestamp", render: (l) => formatDate(l.created_at) },
                { key: "admin_username", header: "Admin", render: (l) => (
                  <span className="font-medium">{l.admin_username}</span>
                )},
                { key: "action", header: "Action", render: (l) => (
                  <Badge>{AUDIT_ACTION_LABELS[l.action] || l.action}</Badge>
                )},
                { key: "target", header: "Target", render: (l) => {
                  if (!l.target_id) return "-";
                  const link = getTargetLink(l.target_type, l.target_id);
                  if (link) {
                    return (
                      <Link href={link} className="text-accent hover:underline text-xs">
                        {l.target_type}:{l.target_id.slice(0, 8)}...
                      </Link>
                    );
                  }
                  return <span className="text-xs">{l.target_type}:{l.target_id.slice(0, 8)}...</span>;
                }},
                { key: "details", header: "Details", render: (l) => (
                  <span className="text-xs text-fg-muted">
                    {l.details ? JSON.stringify(l.details) : "-"}
                  </span>
                )},
                { key: "ip_address", header: "IP", render: (l) => (
                  <span className="font-mono text-xs">{l.ip_address || "-"}</span>
                )},
              ]}
              data={data?.data || []}
            />
            {data && (
              <Pagination
                page={data.page}
                totalPages={data.totalPages}
                onPrev={prevPage}
                onNext={nextPage}
              />
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
