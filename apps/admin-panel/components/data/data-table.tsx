"use client";

import { cn } from "@/lib/utils";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/ui/empty-state";
import type { ReactNode } from "react";

export interface Column<T> {
  key: string;
  header: string;
  render?: (item: T) => ReactNode;
  className?: string;
  sortable?: boolean;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  data: T[];
  loading?: boolean;
  onRowClick?: (item: T) => void;
  emptyMessage?: string;
  emptyDescription?: string;
  sortKey?: string;
  sortDir?: "ASC" | "DESC";
  onSort?: (key: string) => void;
  rowActions?: (item: T) => ReactNode;
}

export function DataTable<T extends Record<string, any>>({
  columns,
  data,
  loading,
  onRowClick,
  emptyMessage = "No data found",
  emptyDescription,
  sortKey,
  sortDir,
  onSort,
  rowActions,
}: DataTableProps<T>) {
  const allColumns = rowActions
    ? [...columns, { key: "__actions", header: "", className: "w-10" } as Column<T>]
    : columns;

  if (loading) {
    return (
      <div className="space-y-px p-1">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-11 w-full rounded-lg" />
        ))}
      </div>
    );
  }

  if (data.length === 0) {
    return <EmptyState title={emptyMessage} description={emptyDescription} />;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead>
          <tr className="border-b border-line-muted">
            {allColumns.map((col) => (
              <th
                key={col.key}
                className={cn(
                  "px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wider text-fg-subtle",
                  col.sortable && onSort && "cursor-pointer select-none hover:text-fg-muted",
                  col.className
                )}
                onClick={() => {
                  if (col.sortable && onSort) onSort(col.key);
                }}
              >
                <span className="flex items-center gap-1">
                  {col.header}
                  {col.sortable && sortKey === col.key && (
                    <span className="text-accent">
                      {sortDir === "ASC" ? "\u2191" : "\u2193"}
                    </span>
                  )}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line-muted/50">
          {data.map((item, idx) => (
            <tr
              key={item.id || idx}
              onClick={() => onRowClick?.(item)}
              className={cn(
                "transition-colors duration-75",
                onRowClick && "cursor-pointer hover:bg-bg-hover/50"
              )}
            >
              {columns.map((col) => (
                <td key={col.key} className={cn("px-4 py-2.5 text-[13px] text-fg", col.className)}>
                  {col.render ? col.render(item) : item[col.key]}
                </td>
              ))}
              {rowActions && (
                <td className="px-4 py-2.5 text-[13px]" onClick={(e) => e.stopPropagation()}>
                  {rowActions(item)}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
