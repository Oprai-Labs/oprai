"use client";

import { useEffect, useState, useCallback } from "react";
import { AdminHeader } from "@/components/layout/admin-header";
import { Card, CardContent } from "@/components/ui/card";
import { DataTable } from "@/components/data/data-table";
import { FilterBar } from "@/components/data/filter-bar";
import { ExportButton } from "@/components/data/export-button";
import { Pagination } from "@/components/ui/pagination";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { fetchIpLogs, downloadCsv, type PaginatedResponse, type IpLog } from "@/lib/admin-api";
import { formatDate, truncateAddress } from "@/lib/utils";
import { useFilters } from "@/hooks/use-filters";
import Link from "next/link";

export default function IpLogsPage() {
  const { filters, setFilter, setSearch, clearFilters, nextPage, prevPage, toParams } = useFilters({
    search: "", from: "", to: "", multiAccountOnly: "",
  });
  const [data, setData] = useState<PaginatedResponse<IpLog> | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    const params = toParams();
    // Route search to appropriate field
    if (params.search) {
      if (/^\d+\./.test(params.search) || params.search.includes(":")) {
        params.ip = params.search;
      } else {
        params.wallet = params.search;
      }
      delete params.search;
    }
    fetchIpLogs(params)
      .then(setData)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [toParams]);

  useEffect(() => { load(); }, [load]);

  return (
    <div>
      <AdminHeader title="IP Logs" description="Track login IP addresses" />
      <div className="space-y-4 p-8">
        <FilterBar
          searchPlaceholder="Search by wallet or IP..."
          onSearch={setSearch}
          dateRange={{
            from: filters.from,
            to: filters.to,
            onChange: (from, to) => { setFilter("from", from); setFilter("to", to); },
          }}
          onClear={clearFilters}
          actions={
            <div className="flex items-center gap-3">
              <Switch
                checked={filters.multiAccountOnly === "true"}
                onChange={(v) => setFilter("multiAccountOnly", v ? "true" : "")}
                label="Multi-account only"
              />
              <ExportButton onExport={() => downloadCsv("ip-logs")} />
            </div>
          }
        />
        <Card>
          <CardContent className="p-0">
            <DataTable
              loading={loading}
              columns={[
                { key: "wallet_address", header: "Wallet", render: (l) => (
                  <Link href={`/users`} className="font-mono text-xs text-accent hover:underline">
                    {truncateAddress(l.wallet_address, 6)}
                  </Link>
                )},
                { key: "ip_address", header: "IP Address", render: (l) => (
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs">{l.ip_address}</span>
                    {Number(l.wallets_on_ip) > 1 && (
                      <Badge variant="warning">{l.wallets_on_ip} wallets</Badge>
                    )}
                  </div>
                )},
                { key: "user_agent", header: "User Agent", className: "max-w-xs", render: (l) => (
                  <span className="block truncate text-xs text-fg-muted" title={l.user_agent}>
                    {l.user_agent}
                  </span>
                )},
                { key: "created_at", header: "Timestamp", render: (l) => formatDate(l.created_at) },
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
