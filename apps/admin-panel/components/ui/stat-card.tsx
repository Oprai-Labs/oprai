import { Card } from "./card";
import { cn, formatNumber } from "@/lib/utils";
import { TrendIndicator } from "./trend-indicator";
import type { LucideIcon } from "lucide-react";

interface StatCardProps {
  title: string;
  value: number;
  icon: LucideIcon;
  subtitle?: string;
  trend?: number;
  className?: string;
}

export function StatCard({ title, value, icon: Icon, subtitle, trend, className }: StatCardProps) {
  return (
    <Card className={cn("group relative overflow-hidden p-5", className)}>
      {/* Subtle gradient accent */}
      <div className="pointer-events-none absolute -right-6 -top-6 h-24 w-24 rounded-full bg-accent/[0.04] blur-2xl transition-all duration-500 group-hover:bg-accent/[0.08]" />
      <div className="relative flex items-start justify-between">
        <div className="space-y-2">
          <p className="text-[13px] font-medium text-fg-muted">{title}</p>
          <p className="text-2xl font-semibold tracking-tight text-fg">{formatNumber(value)}</p>
          <div className="flex items-center gap-2">
            {trend !== undefined && <TrendIndicator value={trend} />}
            {subtitle && <span className="text-[11px] text-fg-subtle">{subtitle}</span>}
          </div>
        </div>
        <div className="rounded-xl bg-accent-subtle p-2.5">
          <Icon className="h-5 w-5 text-accent" />
        </div>
      </div>
    </Card>
  );
}
