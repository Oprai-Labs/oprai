import { forwardRef, type InputHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, type, ...props }, ref) => (
    <input
      type={type}
      className={cn(
        "flex h-9 w-full rounded-lg border border-line bg-bg-elevated/50 px-3 py-1 text-sm text-fg shadow-xs placeholder:text-fg-subtle transition-all duration-150 focus:border-accent/40 focus:outline-none focus:ring-2 focus:ring-accent/15 hover:border-line/80 disabled:cursor-not-allowed disabled:opacity-40",
        className
      )}
      ref={ref}
      {...props}
    />
  )
);
Input.displayName = "Input";

export { Input };
