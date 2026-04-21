"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { AdminHeader } from "@/components/layout/admin-header";
import { Card, CardContent } from "@/components/ui/card";
import { DataTable } from "@/components/data/data-table";
import { FilterBar } from "@/components/data/filter-bar";
import { ExportButton } from "@/components/data/export-button";
import { Pagination } from "@/components/ui/pagination";
import { DropdownMenu, DropdownItem } from "@/components/ui/dropdown-menu";
import { fetchSessions, downloadCsv, closeSession as apiCloseSession, type PaginatedResponse, type Session } from "@/lib/admin-api";
import { formatDate, truncateAddress } from "@/lib/utils";
import { useFilters } from "@/hooks/use-filters";
import { Eye, XCircle } from "lucide-react";

export default function SessionsPage() {
  const router = useRouter();
  const { filters, setFilter, setSearch, clearFilters, nextPage, prevPage, toParams } = useFilters({
    search: "", from: "", to: "",
  });
  const [data, setData] = useState<PaginatedResponse<Session> | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    fetchSessions(toParams())
      .then(setData)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [toParams]);

  useEffect(() => { load(); }, [load]);

  const handleClose = async (id: string) => {
    try {
      await apiCloseSession(id);
      load();
    } catch (err) {
      console.error("Failed to close session:", err);
    }
  };

  return (
    <div>
      <AdminHeader title="Sessions" description="View all chat sessions" />
      <div className="space-y-4 p-8">
        <FilterBar
          searchPlaceholder="Search by wallet or title..."
          onSearch={setSearch}
          dateRange={{
            from: filters.from,
            to: filters.to,
            onChange: (from, to) => { setFilter("from", from); setFilter("to", to); },
          }}
          onClear={clearFilters}
          actions={<ExportButton onExport={() => downloadCsv("sessions")} />}
        />
        <Card>
          <CardContent className="p-0">
            <DataTable
              loading={loading}
              columns={[
                { key: "user_id", header: "User", render: (s) => (
                  <span className="font-mono text-xs">{truncateAddress(s.user_id, 6)}</span>
                )},
                { key: "title", header: "Title", render: (s) => s.title || "Untitled" },
                { key: "message_count", header: "Messages", render: (s) => s.message_count ?? 0 },
                { key: "created_at", header: "Created", render: (s) => formatDate(s.created_at) },
              ]}
              data={data?.data || []}
              onRowClick={(s) => router.push(`/sessions/${s.id}`)}
              rowActions={(s) => (
                <DropdownMenu>
                  <DropdownItem onClick={() => router.push(`/sessions/${s.id}`)}>
                    <Eye className="h-4 w-4" /> View Messages
                  </DropdownItem>
                  <DropdownItem onClick={() => handleClose(s.id)} variant="destructive">
                    <XCircle className="h-4 w-4" /> Close Session
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
