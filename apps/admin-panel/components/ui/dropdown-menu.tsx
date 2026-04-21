"use client";

import { useState, useRef, useEffect, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import { MoreHorizontal } from "lucide-react";

interface DropdownMenuProps {
  trigger?: ReactNode;
  children: ReactNode;
  align?: "left" | "right";
}

export function DropdownMenu({ trigger, children, align = "right" }: DropdownMenuProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  return (
    <div ref={ref} className="relative inline-block">
      <button
        onClick={(e) => { e.stopPropagation(); setOpen(!open); }}
        className="rounded-lg p-1.5 text-fg-muted transition-colors hover:bg-bg-hover hover:text-fg"
      >
        {trigger || <MoreHorizontal className="h-4 w-4" />}
      </button>
      {open && (
        <div
          className={cn(
            "absolute z-50 mt-1 min-w-[160px] rounded-xl border border-line-muted bg-bg-surface py-1 shadow-lg animate-in",
            align === "right" ? "right-0" : "left-0"
          )}
          onClick={() => setOpen(false)}
        >
          {children}
        </div>
      )}
    </div>
  );
}

interface DropdownItemProps {
  onClick?: () => void;
  children: ReactNode;
  variant?: "default" | "destructive";
  className?: string;
}

export function DropdownItem({ onClick, children, variant = "default", className }: DropdownItemProps) {
  return (
    <button
      onClick={(e) => { e.stopPropagation(); onClick?.(); }}
      className={cn(
        "flex w-full items-center gap-2 px-3 py-2 text-sm transition-colors",
        variant === "destructive"
          ? "text-semantic-danger hover:bg-semantic-danger/10"
          : "text-fg hover:bg-bg-hover",
        className
      )}
    >
      {children}
    </button>
  );
}
