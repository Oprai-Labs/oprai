import { cn } from "@/lib/utils";

const dotColors: Record<string, string> = {
  active: "bg-semantic-success shadow-semantic-success/40",
  suspended: "bg-semantic-warning shadow-semantic-warning/40",
  banned: "bg-semantic-danger shadow-semantic-danger/40",
  confirmed: "bg-semantic-success shadow-semantic-success/40",
  pending: "bg-semantic-warning shadow-semantic-warning/40",
  failed: "bg-semantic-danger shadow-semantic-danger/40",
  submitted: "bg-semantic-info shadow-semantic-info/40",
  open: "bg-semantic-success shadow-semantic-success/40",
  closed: "bg-fg-subtle shadow-fg-subtle/40",
};

interface StatusDotProps {
  status: string;
  label?: string;
  className?: string;
}

export function StatusDot({ status, label, className }: StatusDotProps) {
  const color = dotColors[status] || "bg-fg-subtle";
  const displayLabel = label || status;

  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <span className={cn("h-1.5 w-1.5 rounded-full shadow-sm", color)} />
      <span className="text-[13px] capitalize text-fg">{displayLabel}</span>
    </span>
  );
}
