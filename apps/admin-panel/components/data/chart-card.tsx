import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { ReactNode } from "react";

interface ChartCardProps {
  title: string;
  subtitle?: string;
  children: ReactNode;
  loading?: boolean;
  height?: number;
  className?: string;
}

export function ChartCard({ title, subtitle, children, loading, height = 250, className }: ChartCardProps) {
  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {subtitle && <p className="mt-0.5 text-[12px] text-fg-subtle">{subtitle}</p>}
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton style={{ height }} className="rounded-lg" />
        ) : (
          children
        )}
      </CardContent>
    </Card>
  );
}
