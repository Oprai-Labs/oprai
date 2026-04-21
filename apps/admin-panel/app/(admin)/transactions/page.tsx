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
import { fetchTransactions, downloadCsv, type PaginatedResponse, type Transaction } from "@/lib/admin-api";
import { formatDate, truncateAddress } from "@/lib/utils";
import { useFilters } from "@/hooks/use-filters";
import { TX_STATUS_OPTIONS, TX_ACTION_OPTIONS } from "@/lib/constants";
import { ExternalLink } from "lucide-react";

export default function TransactionsPage() {
  const router = useRouter();
  const { filters, setFilter, setSearch, clearFilters, nextPage, prevPage, toParams } = useFilters({
    search: "", status: "", action: "", from: "", to: "",
  });
  const [data, setData] = useState<PaginatedResponse<Transaction> | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    fetchTransactions(toParams())
      .then(setData)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [toParams]);

  useEffect(() => { load(); }, [load]);

  const statusBadgeVariant = (s: string) => {
    if (s === "confirmed") return "success" as const;
    if (s === "pending") return "warning" as const;
    if (s === "failed") return "error" as const;
    if (s === "submitted") return "info" as const;
    return "default" as const;
  };

  return (
    <div>
      <AdminHeader title="Transactions" description="View all platform transactions" />
      <div className="space-y-4 p-8">
        <FilterBar
          searchPlaceholder="Search by tx hash or wallet..."
          onSearch={setSearch}
          filters={[
            {
              key: "status",
              label: "All Statuses",
              options: TX_STATUS_OPTIONS,
              value: filters.status,
              onChange: (v) => setFilter("status", v),
            },
            {
              key: "action",
              label: "All Actions",
              options: TX_ACTION_OPTIONS,
              value: filters.action,
              onChange: (v) => setFilter("action", v),
            },
          ]}
          dateRange={{
            from: filters.from,
            to: filters.to,
            onChange: (from, to) => { setFilter("from", from); setFilter("to", to); },
          }}
          onClear={clearFilters}
          actions={<ExportButton onExport={() => downloadCsv("transactions")} />}
        />

        <Card>
          <CardContent className="p-0">
            <DataTable
              loading={loading}
              columns={[
                { key: "user_wallet", header: "Wallet", render: (t) => (
                  <span className="font-mono text-xs">{truncateAddress(t.user_wallet, 6)}</span>
                )},
                { key: "action", header: "Action", render: (t) => <Badge>{t.action}</Badge> },
                { key: "protocol", header: "Protocol", render: (t) => t.protocol || "-" },
                { key: "status", header: "Status", render: (t) => <StatusDot status={t.status} /> },
                { key: "tx_hash", header: "TX Hash", render: (t) => t.tx_hash ? (
                  <a
                    href={`https://solscan.io/tx/${t.tx_hash}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 font-mono text-xs text-accent hover:underline"
                    onClick={(e) => e.stopPropagation()}
                  >
                    {truncateAddress(t.tx_hash, 8)}
                    <ExternalLink className="h-3 w-3" />
                  </a>
                ) : "-" },
                { key: "created_at", header: "Date", render: (t) => formatDate(t.created_at) },
              ]}
              data={data?.data || []}
              onRowClick={(t) => router.push(`/transactions/${t.id}`)}
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
