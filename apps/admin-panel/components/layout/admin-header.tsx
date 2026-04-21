"use client";

import { Breadcrumbs } from "@/components/ui/breadcrumbs";
import type { ReactNode } from "react";

interface AdminHeaderProps {
  title: string;
  description?: string;
  breadcrumbs?: { label: string; href?: string }[];
  actions?: ReactNode;
}

export function AdminHeader({ title, description, breadcrumbs, actions }: AdminHeaderProps) {
  return (
    <header className="border-b border-line-muted bg-bg-surface/50 px-8 py-5">
      <div className="flex items-center justify-between">
        <div>
          {breadcrumbs && breadcrumbs.length > 0 && (
            <Breadcrumbs items={breadcrumbs} className="mb-2" />
          )}
          <h1 className="text-xl font-semibold tracking-[-0.01em] text-fg">{title}</h1>
          {description && <p className="mt-1 text-[13px] text-fg-subtle">{description}</p>}
        </div>
        {actions && <div className="flex items-center gap-3">{actions}</div>}
      </div>
    </header>
  );
}
