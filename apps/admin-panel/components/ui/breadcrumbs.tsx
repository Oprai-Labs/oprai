import Link from "next/link";
import { cn } from "@/lib/utils";
import { ChevronRight } from "lucide-react";

interface Breadcrumb {
  label: string;
  href?: string;
}

interface BreadcrumbsProps {
  items: Breadcrumb[];
  className?: string;
}

export function Breadcrumbs({ items, className }: BreadcrumbsProps) {
  return (
    <nav className={cn("flex items-center gap-1 text-[13px]", className)}>
      {items.map((item, idx) => (
        <span key={idx} className="flex items-center gap-1">
          {idx > 0 && <ChevronRight className="h-3 w-3 text-fg-subtle" />}
          {item.href ? (
            <Link
              href={item.href}
              className="text-fg-subtle transition-colors hover:text-fg-muted"
            >
              {item.label}
            </Link>
          ) : (
            <span className="font-medium text-fg-muted">{item.label}</span>
          )}
        </span>
      ))}
    </nav>
  );
}
