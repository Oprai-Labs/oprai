"use client";

import { cn } from "@/lib/utils";
import { Calendar } from "lucide-react";

interface DateRangePickerProps {
  from: string;
  to: string;
  onChange: (from: string, to: string) => void;
  className?: string;
}

export function DateRangePicker({ from, to, onChange, className }: DateRangePickerProps) {
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <Calendar className="h-3.5 w-3.5 text-fg-subtle" />
      <input
        type="date"
        value={from}
        onChange={(e) => onChange(e.target.value, to)}
        className="h-9 rounded-lg border border-line bg-bg-elevated/50 px-2 text-[13px] text-fg shadow-xs transition-all duration-150 focus:border-accent/40 focus:outline-none focus:ring-2 focus:ring-accent/15"
      />
      <span className="text-[11px] text-fg-subtle">to</span>
      <input
        type="date"
        value={to}
        onChange={(e) => onChange(from, e.target.value)}
        className="h-9 rounded-lg border border-line bg-bg-elevated/50 px-2 text-[13px] text-fg shadow-xs transition-all duration-150 focus:border-accent/40 focus:outline-none focus:ring-2 focus:ring-accent/15"
      />
    </div>
  );
}
