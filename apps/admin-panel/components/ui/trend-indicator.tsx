import { cn } from "@/lib/utils";
import { TrendingUp, TrendingDown, Minus } from "lucide-react";

interface TrendIndicatorProps {
  value: number;
  suffix?: string;
  className?: string;
}

export function TrendIndicator({ value, suffix = "%", className }: TrendIndicatorProps) {
  if (value === 0) {
    return (
      <span className={cn("inline-flex items-center gap-0.5 text-[11px] font-medium text-fg-subtle", className)}>
        <Minus className="h-3 w-3" />
        0{suffix}
      </span>
    );
  }

  const isPositive = value > 0;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-0.5 text-[11px] font-medium",
        isPositive ? "text-semantic-success" : "text-semantic-danger",
        className
      )}
    >
      {isPositive ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
      {isPositive ? "+" : ""}{value}{suffix}
    </span>
  );
}
