"use client";

import { useEffect } from "react";
import { cn } from "@/lib/utils";
import { X, CheckCircle, AlertCircle, Info } from "lucide-react";

export interface ToastData {
  id: string;
  type: "success" | "error" | "info";
  message: string;
}

interface ToastProps extends ToastData {
  onDismiss: (id: string) => void;
}

const icons = {
  success: CheckCircle,
  error: AlertCircle,
  info: Info,
};

const styles = {
  success: "border-semantic-success/20 bg-bg-surface",
  error: "border-semantic-danger/20 bg-bg-surface",
  info: "border-semantic-info/20 bg-bg-surface",
};

const iconStyles = {
  success: "text-semantic-success",
  error: "text-semantic-danger",
  info: "text-semantic-info",
};

export function Toast({ id, type, message, onDismiss }: ToastProps) {
  const Icon = icons[type];

  useEffect(() => {
    const timer = setTimeout(() => onDismiss(id), 5000);
    return () => clearTimeout(timer);
  }, [id, onDismiss]);

  return (
    <div
      className={cn(
        "flex items-center gap-3 rounded-xl border px-4 py-3 shadow-lg backdrop-blur-sm animate-slide-up",
        styles[type]
      )}
    >
      <Icon className={cn("h-4 w-4 shrink-0", iconStyles[type])} />
      <p className="flex-1 text-[13px] text-fg">{message}</p>
      <button
        onClick={() => onDismiss(id)}
        className="rounded-md p-0.5 text-fg-subtle transition-colors hover:text-fg-muted"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

export function ToastContainer({ toasts, onDismiss }: { toasts: ToastData[]; onDismiss: (id: string) => void }) {
  if (toasts.length === 0) return null;

  return (
    <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2">
      {toasts.map((toast) => (
        <Toast key={toast.id} {...toast} onDismiss={onDismiss} />
      ))}
    </div>
  );
}
