"use client";

import { useEffect, useState } from "react";
import { AdminHeader } from "@/components/layout/admin-header";
import { StatCard } from "@/components/ui/stat-card";
import { ChartCard } from "@/components/data/chart-card";
import { Tabs } from "@/components/ui/tabs";
import { DataTable } from "@/components/data/data-table";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { TrendIndicator } from "@/components/ui/trend-indicator";
import { fetchAnalyticsOverview, fetchTopUsers, fetchProtocolStats, type AnalyticsOverview, type TopUser, type ProtocolStat } from "@/lib/admin-api";
import { truncateAddress } from "@/lib/utils";
import { CHART_COLORS, CHART_TOOLTIP_STYLE, AXIS_STYLE } from "@/lib/constants";
import { Users, ArrowLeftRight, MessageSquare } from "lucide-react";
import {
  LineChart, Line, BarChart, Bar, PieChart, Pie, Cell,
  XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from "recharts";

export default function AnalyticsPage() {
  const [period, setPeriod] = useState("30");
  const [overview, setOverview] = useState<AnalyticsOverview | null>(null);
  const [topUsers, setTopUsers] = useState<TopUser[] | null>(null);
  const [protocols, setProtocols] = useState<ProtocolStat[] | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      fetchAnalyticsOverview(Number(period)),
      fetchTopUsers(),
      fetchProtocolStats(),
    ])
      .then(([ov, tu, ps]) => {
        setOverview(ov);
        setTopUsers(tu);
        setProtocols(ps);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [period]);

  if (loading) {
    return (
      <div>
        <AdminHeader title="Analytics" description="Platform analytics and trends" />
        <div className="space-y-6 p-8">
          <div className="grid grid-cols-3 gap-4">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-28" />
            ))}
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Skeleton className="h-72" />
            <Skeleton className="h-72" />
          </div>
        </div>
      </div>
    );
  }

  const periodTabs = [
    { key: "7", label: "7 days" },
    { key: "30", label: "30 days" },
    { key: "90", label: "90 days" },
  ];

  const signupData = (overview?.dailyActiveUsers ?? []).map((d) => ({
    date: new Date(d.date).toLocaleDateString("en-US", { month: "short", day: "numeric" }),
    count: d.count,
  }));

  const txData = (overview?.dailyTransactions ?? []).map((d) => ({
    date: new Date(d.date).toLocaleDateString("en-US", { month: "short", day: "numeric" }),
    count: d.count,
  }));

  const protocolData = (protocols ?? []).map((p) => ({
    protocol: p.protocol,
    count: p.count,
  }));

  return (
    <div>
      <AdminHeader title="Analytics" description="Platform analytics and trends" />
      <div className="space-y-6 p-8">
        <Tabs tabs={periodTabs} active={period} onChange={setPeriod} />

        {/* Growth metrics */}
        {overview?.growth && (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <StatCard
              title="User Growth"
              value={overview.growth.users.current}
              icon={Users}
              trend={overview.growth.users.rate}
              subtitle={`vs prev ${period}d`}
            />
            <StatCard
              title="Transaction Growth"
              value={overview.growth.transactions.current}
              icon={ArrowLeftRight}
              trend={overview.growth.transactions.rate}
              subtitle={`vs prev ${period}d`}
            />
            <StatCard
              title="Session Growth"
              value={overview.growth.sessions.current}
              icon={MessageSquare}
              trend={overview.growth.sessions.rate}
              subtitle={`vs prev ${period}d`}
            />
          </div>
        )}

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <ChartCard title="Daily New Users" subtitle={`Last ${period} days`}>
            {signupData.length > 0 ? (
              <ResponsiveContainer width="100%" height={250}>
                <LineChart data={signupData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(222, 20%, 25%)" />
                  <XAxis dataKey="date" {...AXIS_STYLE} />
                  <YAxis {...AXIS_STYLE} />
                  <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
                  <Line type="monotone" dataKey="count" stroke={CHART_COLORS[0]} strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex h-[250px] items-center justify-center text-fg-muted">
                No data yet
              </div>
            )}
          </ChartCard>

          <ChartCard title="Daily Transactions" subtitle={`Last ${period} days`}>
            {txData.length > 0 ? (
              <ResponsiveContainer width="100%" height={250}>
                <LineChart data={txData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(222, 20%, 25%)" />
                  <XAxis dataKey="date" {...AXIS_STYLE} />
                  <YAxis {...AXIS_STYLE} />
                  <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
                  <Line type="monotone" dataKey="count" stroke={CHART_COLORS[1]} strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex h-[250px] items-center justify-center text-fg-muted">
                No data yet
              </div>
            )}
          </ChartCard>
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <ChartCard title="Protocol Usage">
            {protocolData.length > 0 ? (
              <ResponsiveContainer width="100%" height={250}>
                <BarChart data={protocolData}>
                  <XAxis dataKey="protocol" {...AXIS_STYLE} />
                  <YAxis {...AXIS_STYLE} />
                  <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
                  <Bar dataKey="count" fill={CHART_COLORS[0]} radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex h-[250px] items-center justify-center text-fg-muted">
                No data yet
              </div>
            )}
          </ChartCard>

          <ChartCard title="Protocol Distribution">
            {protocolData.length > 0 ? (
              <ResponsiveContainer width="100%" height={250}>
                <PieChart>
                  <Pie
                    data={protocolData}
                    dataKey="count"
                    nameKey="protocol"
                    cx="50%"
                    cy="50%"
                    outerRadius={80}
                    label={(entry: { protocol: string }) => entry.protocol}
                  >
                    {protocolData.map((_, i: number) => (
                      <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
                </PieChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex h-[250px] items-center justify-center text-fg-muted">
                No data yet
              </div>
            )}
          </ChartCard>
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Top Users by Transactions</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <DataTable
                columns={[
                  { key: "wallet_address", header: "Wallet", render: (u) => (
                    <span className="font-mono text-xs">{truncateAddress(u.wallet_address, 6)}</span>
                  )},
                  { key: "display_name", header: "Name", render: (u) => u.display_name || "-" },
                  { key: "tx_count", header: "Transactions" },
                ]}
                data={topUsers ?? []}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Top Users by Messages</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <DataTable
                columns={[
                  { key: "wallet_address", header: "Wallet", render: (u) => (
                    <span className="font-mono text-xs">{truncateAddress(u.wallet_address, 6)}</span>
                  )},
                  { key: "display_name", header: "Name", render: (u) => u.display_name || "-" },
                  { key: "total_volume_sol", header: "Volume (SOL)" },
                ]}
                data={topUsers ?? []}
              />
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
