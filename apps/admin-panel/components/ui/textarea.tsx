import { forwardRef, type TextareaHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => (
    <textarea
      className={cn(
        "flex min-h-[80px] w-full rounded-lg border border-line bg-bg-elevated/50 px-3 py-2 text-sm text-fg shadow-xs placeholder:text-fg-subtle transition-all duration-150 focus:border-accent/40 focus:outline-none focus:ring-2 focus:ring-accent/15 hover:border-line/80 disabled:cursor-not-allowed disabled:opacity-40",
        className
      )}
      ref={ref}
      {...props}
    />
  )
);
Textarea.displayName = "Textarea";

export { Textarea };
