import { cn } from "@/lib/utils";
import { Inbox } from "lucide-react";
import type { LucideIcon } from "lucide-react";

interface EmptyStateProps {
  icon?: LucideIcon;
  title?: string;
  description?: string;
  className?: string;
}

export function EmptyState({
  icon: Icon = Inbox,
  title = "No data found",
  description,
  className,
}: EmptyStateProps) {
  return (
    <div className={cn("flex flex-col items-center justify-center py-16", className)}>
      <div className="rounded-xl bg-bg-hover p-3">
        <Icon className="h-6 w-6 text-fg-subtle" />
      </div>
      <h3 className="mt-3 text-[13px] font-medium text-fg-muted">{title}</h3>
      {description && (
        <p className="mt-1 text-[12px] text-fg-subtle">{description}</p>
      )}
    </div>
  );
}
