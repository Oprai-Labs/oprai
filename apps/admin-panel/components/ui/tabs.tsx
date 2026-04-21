"use client";

import { cn } from "@/lib/utils";

interface Tab {
  key: string;
  label: string;
}

interface TabsProps {
  tabs: Tab[];
  active: string;
  onChange: (key: string) => void;
  className?: string;
}

export function Tabs({ tabs, active, onChange, className }: TabsProps) {
  return (
    <div className={cn("flex gap-0.5 rounded-lg bg-bg-elevated p-1", className)}>
      {tabs.map((tab) => (
        <button
          key={tab.key}
          onClick={() => onChange(tab.key)}
          className={cn(
            "rounded-md px-3.5 py-1.5 text-[13px] font-medium transition-all duration-150",
            active === tab.key
              ? "bg-bg-surface text-fg shadow-xs"
              : "text-fg-subtle hover:text-fg-muted"
          )}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
