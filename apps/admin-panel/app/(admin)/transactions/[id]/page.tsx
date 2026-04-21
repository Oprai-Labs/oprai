"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { AdminHeader } from "@/components/layout/admin-header";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { StatusDot } from "@/components/ui/status-dot";
import { Skeleton } from "@/components/ui/skeleton";
import { fetchTransaction, type Transaction } from "@/lib/admin-api";
import { formatDate, truncateAddress } from "@/lib/utils";
import { ExternalLink, Copy } from "lucide-react";
import Link from "next/link";

export default function TransactionDetailPage() {
  const params = useParams();
  const txId = params.id as string;
  const [tx, setTx] = useState<Transaction | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchTransaction(txId)
      .then(setTx)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [txId]);

  if (loading) {
    return (
      <div>
        <AdminHeader title="Transaction Detail" breadcrumbs={[{ label: "Transactions", href: "/transactions" }, { label: "Loading..." }]} />
        <div className="p-8"><Skeleton className="h-64" /></div>
      </div>
    );
  }

  if (!tx) {
    return (
      <div>
        <AdminHeader title="Transaction Not Found" breadcrumbs={[{ label: "Transactions", href: "/transactions" }, { label: "Not Found" }]} />
        <div className="p-8 text-fg-muted">Transaction not found.</div>
      </div>
    );
  }

  const statusBadgeVariant = (s: string) => {
    if (s === "confirmed") return "success" as const;
    if (s === "pending") return "warning" as const;
    if (s === "failed") return "error" as const;
    if (s === "submitted") return "info" as const;
    return "default" as const;
  };

  const copyHash = () => navigator.clipboard.writeText(tx.tx_hash || "");

  return (
    <div>
      <AdminHeader
        title="Transaction Detail"
        breadcrumbs={[
          { label: "Transactions", href: "/transactions" },
          { label: tx.tx_hash ? truncateAddress(tx.tx_hash, 8) : txId },
        ]}
      />
      <div className="space-y-6 p-8">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-3">
              <CardTitle>Transaction</CardTitle>
              <Badge variant={statusBadgeVariant(tx.status)}>{tx.status}</Badge>
              <Badge>{tx.action}</Badge>
            </div>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-4">
              {tx.tx_hash && (
                <div>
                  <p className="text-xs text-fg-muted">TX Hash</p>
                  <div className="mt-1 flex items-center gap-2">
                    <span className="font-mono text-sm">{truncateAddress(tx.tx_hash, 12)}</span>
                    <button onClick={copyHash} className="text-fg-muted hover:text-fg">
                      <Copy className="h-3 w-3" />
                    </button>
                    <a
                      href={`https://solscan.io/tx/${tx.tx_hash}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-accent hover:underline"
                    >
                      <ExternalLink className="h-3 w-3" />
                    </a>
                  </div>
                </div>
              )}
              <div>
                <p className="text-xs text-fg-muted">User Wallet</p>
                <p className="mt-1 font-mono text-sm">{truncateAddress(tx.user_wallet, 8)}</p>
              </div>
              <div>
                <p className="text-xs text-fg-muted">Protocol</p>
                <p className="mt-1 text-sm">{tx.protocol || "-"}</p>
              </div>
              <div>
                <p className="text-xs text-fg-muted">Created</p>
                <p className="mt-1 text-sm">{formatDate(tx.created_at)}</p>
              </div>
              {tx.updated_at && (
                <div>
                  <p className="text-xs text-fg-muted">Updated</p>
                  <p className="mt-1 text-sm">{formatDate(tx.updated_at)}</p>
                </div>
              )}
            </div>
          </CardContent>
        </Card>

        {tx.params && (
          <Card>
            <CardHeader>
              <CardTitle>Parameters</CardTitle>
            </CardHeader>
            <CardContent>
              <pre className="overflow-x-auto rounded-lg bg-bg-hover p-4 text-xs text-fg">
                {JSON.stringify(tx.params, null, 2)}
              </pre>
            </CardContent>
          </Card>
        )}

        {tx.error && (
          <Card>
            <CardHeader>
              <CardTitle className="text-red-400">Error</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-red-400">{tx.error}</p>
            </CardContent>
          </Card>
        )}

        {tx.session_id && (
          <Card>
            <CardContent className="p-6">
              <p className="text-xs text-fg-muted">Related Session</p>
              <Link href={`/sessions/${tx.session_id}`} className="mt-1 text-sm text-accent hover:underline">
                View Session
              </Link>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
