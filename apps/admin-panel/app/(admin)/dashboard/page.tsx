"use client";

import { useEffect, useState } from "react";
import { AdminHeader } from "@/components/layout/admin-header";
import { StatCard } from "@/components/ui/stat-card";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { DataTable } from "@/components/data/data-table";
import { ChartCard } from "@/components/data/chart-card";
import { Badge } from "@/components/ui/badge";
import { StatusDot } from "@/components/ui/status-dot";
import { Skeleton } from "@/components/ui/skeleton";
import { Users, ArrowLeftRight, MessageSquare, MessagesSquare } from "lucide-react";
import { fetchDashboardStats, type DashboardStats } from "@/lib/admin-api";
import { formatDate, truncateAddress } from "@/lib/utils";
import { CHART_COLORS, CHART_TOOLTIP_STYLE, AXIS_STYLE } from "@/lib/constants";
import {
  PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, LineChart, Line, CartesianGrid,
} from "recharts";

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchDashboardStats()
      .then(setStats)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div>
        <AdminHeader title="Dashboard" description="Overview of platform activity" />
        <div className="space-y-6 p-8">
          <div className="grid grid-cols-4 gap-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-28" />
            ))}
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Skeleton className="h-64" />
            <Skeleton className="h-64" />
          </div>
        </div>
      </div>
    );
  }

  if (!stats) return null;

  const calcTrend = (current: number, previous: number) => {
    if (previous === 0) return current > 0 ? 100 : 0;
    return Math.round(((current - previous) / previous) * 100);
  };

  const statusBadgeVariant = (s: string) => {
    if (s === "confirmed") return "success" as const;
    if (s === "pending") return "warning" as const;
    if (s === "failed") return "error" as const;
    return "default" as const;
  };

  return (
    <div>
      <AdminHeader title="Dashboard" description="Overview of platform activity" />
      <div className="space-y-6 p-8">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            title="Total Users"
            value={stats.totalUsers}
            icon={Users}
            subtitle={`${stats.usersToday} today`}
            trend={calcTrend(stats.totalUsers, stats.trends.users7dAgo)}
          />
          <StatCard
            title="Total Transactions"
            value={stats.totalTransactions}
            icon={ArrowLeftRight}
            subtitle={`${stats.txToday} today`}
            trend={calcTrend(stats.totalTransactions, stats.trends.tx7dAgo)}
          />
          <StatCard
            title="Active Sessions"
            value={stats.sessionsActive}
            icon={MessageSquare}
            subtitle={`${stats.totalSessions} total`}
          />
          <StatCard
            title="Messages Today"
            value={stats.messagesToday}
            icon={MessagesSquare}
            subtitle={`${stats.totalMessages} total`}
          />
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <ChartCard title="Transaction Status">
            {stats.txByStatus.length > 0 ? (
              <ResponsiveContainer width="100%" height={250}>
                <PieChart>
                  <Pie
                    data={stats.txByStatus}
                    dataKey="count"
                    nameKey="status"
                    cx="50%"
                    cy="50%"
                    outerRadius={80}
                    label={(entry: { status: string; count: number }) => `${entry.status} (${entry.count})`}
                  >
                    {stats.txByStatus.map((_, i: number) => (
                      <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
                </PieChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex h-[250px] items-center justify-center text-fg-muted">
                No transaction data yet
              </div>
            )}
          </ChartCard>

          <ChartCard title="Transaction Actions">
            {stats.txByAction.length > 0 ? (
              <ResponsiveContainer width="100%" height={250}>
                <BarChart data={stats.txByAction}>
                  <XAxis dataKey="action" {...AXIS_STYLE} />
                  <YAxis {...AXIS_STYLE} />
                  <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
                  <Bar dataKey="count" fill={CHART_COLORS[0]} radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex h-[250px] items-center justify-center text-fg-muted">
                No transaction data yet
              </div>
            )}
          </ChartCard>
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Recent Users</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <DataTable
                columns={[
                  { key: "wallet_address", header: "Wallet", render: (u) => (
                    <span className="font-mono text-xs">{truncateAddress(u.wallet_address)}</span>
                  )},
                  { key: "display_name", header: "Name", render: (u) => u.display_name || "-" },
                  { key: "status", header: "Status", render: (u) => <StatusDot status={u.status || "active"} /> },
                  { key: "created_at", header: "Joined", render: (u) => formatDate(u.created_at) },
                ]}
                data={stats.recentUsers}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Recent Transactions</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <DataTable
                columns={[
                  { key: "user_wallet", header: "Wallet", render: (t) => (
                    <span className="font-mono text-xs">{truncateAddress(t.user_wallet)}</span>
                  )},
                  { key: "action", header: "Action", render: (t) => <Badge>{t.action}</Badge> },
                  { key: "status", header: "Status", render: (t) => (
                    <Badge variant={statusBadgeVariant(t.status)}>{t.status}</Badge>
                  )},
                  { key: "created_at", header: "Date", render: (t) => formatDate(t.created_at) },
                ]}
                data={stats.recentTransactions}
              />
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
