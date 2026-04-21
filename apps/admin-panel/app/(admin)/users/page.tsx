"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { AdminHeader } from "@/components/layout/admin-header";
import { Card, CardContent } from "@/components/ui/card";
import { DataTable } from "@/components/data/data-table";
import { FilterBar } from "@/components/data/filter-bar";
import { ExportButton } from "@/components/data/export-button";
import { Pagination } from "@/components/ui/pagination";
import { Badge } from "@/components/ui/badge";
import { StatusDot } from "@/components/ui/status-dot";
import { DropdownMenu, DropdownItem } from "@/components/ui/dropdown-menu";
import { fetchUsers, downloadCsv, type PaginatedResponse, type User } from "@/lib/admin-api";
import { formatDate, truncateAddress } from "@/lib/utils";
import { useFilters } from "@/hooks/use-filters";
import { USER_STATUS_OPTIONS } from "@/lib/constants";
import { Eye, Ban, UserCog } from "lucide-react";

export default function UsersPage() {
  const router = useRouter();
  const { filters, page, setFilter, setSearch, clearFilters, nextPage, prevPage, toParams } = useFilters({
    search: "", status: "", role: "", from: "", to: "",
  });
  const [data, setData] = useState<PaginatedResponse<User> | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    fetchUsers(toParams())
      .then(setData)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [toParams]);

  useEffect(() => { load(); }, [load]);

  return (
    <div>
      <AdminHeader title="Users" description="Manage platform users" />
      <div className="space-y-4 p-8">
        <FilterBar
          searchPlaceholder="Search by wallet or name..."
          onSearch={setSearch}
          filters={[
            {
              key: "status",
              label: "All Statuses",
              options: USER_STATUS_OPTIONS,
              value: filters.status,
              onChange: (v) => setFilter("status", v),
            },
          ]}
          dateRange={{
            from: filters.from,
            to: filters.to,
            onChange: (from, to) => { setFilter("from", from); setFilter("to", to); },
          }}
          onClear={clearFilters}
          actions={<ExportButton onExport={() => downloadCsv("users")} />}
        />
        <Card>
          <CardContent className="p-0">
            <DataTable
              loading={loading}
              columns={[
                { key: "wallet_address", header: "Wallet", render: (u) => (
                  <span className="font-mono text-xs">{truncateAddress(u.wallet_address, 6)}</span>
                )},
                { key: "display_name", header: "Display Name", render: (u) => u.display_name || "-" },
                { key: "status", header: "Status", render: (u) => <StatusDot status={u.status || "active"} /> },
                { key: "role", header: "Role", render: (u) => <Badge>{u.role}</Badge> },
                { key: "session_count", header: "Sessions", render: (u) => u.session_count ?? 0 },
                { key: "tx_count", header: "Transactions", render: (u) => u.tx_count ?? 0 },
                { key: "created_at", header: "Created", render: (u) => formatDate(u.created_at) },
              ]}
              data={data?.data || []}
              onRowClick={(u) => router.push(`/users/${u.id}`)}
              rowActions={(u) => (
                <DropdownMenu>
                  <DropdownItem onClick={() => router.push(`/users/${u.id}`)}>
                    <Eye className="h-4 w-4" /> View Detail
                  </DropdownItem>
                  <DropdownItem onClick={() => router.push(`/users/${u.id}?action=ban`)}>
                    <Ban className="h-4 w-4" /> Ban User
                  </DropdownItem>
                  <DropdownItem onClick={() => router.push(`/users/${u.id}?action=role`)}>
                    <UserCog className="h-4 w-4" /> Change Role
                  </DropdownItem>
                </DropdownMenu>
              )}
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
