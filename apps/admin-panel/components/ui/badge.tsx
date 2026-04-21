import { cn } from "@/lib/utils";
import type { HTMLAttributes } from "react";

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: "default" | "success" | "warning" | "error" | "info";
}

const variantClasses: Record<string, string> = {
  default: "bg-bg-hover text-fg-muted border-line",
  success: "bg-semantic-success/10 text-semantic-success border-semantic-success/20",
  warning: "bg-semantic-warning/10 text-semantic-warning border-semantic-warning/20",
  error: "bg-semantic-danger/10 text-semantic-danger border-semantic-danger/20",
  info: "bg-semantic-info/10 text-semantic-info border-semantic-info/20",
};

export function Badge({ className, variant = "default", ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md border px-2 py-0.5 text-[11px] font-medium leading-none",
        variantClasses[variant],
        className
      )}
      {...props}
    />
  );
}
