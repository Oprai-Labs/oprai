import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-lg text-sm font-medium transition-all duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 focus-visible:ring-offset-2 focus-visible:ring-offset-bg-base disabled:pointer-events-none disabled:opacity-40",
  {
    variants: {
      variant: {
        default:
          "bg-accent text-fg-on-accent shadow-sm shadow-accent/20 hover:bg-accent-hover active:bg-accent-hover/90",
        secondary:
          "bg-bg-elevated text-fg border border-line hover:bg-bg-hover hover:border-line/80 active:bg-bg-active",
        ghost:
          "text-fg-muted hover:bg-bg-hover hover:text-fg active:bg-bg-active",
        destructive:
          "bg-semantic-danger/10 text-semantic-danger border border-semantic-danger/20 hover:bg-semantic-danger/20 active:bg-semantic-danger/25",
        outline:
          "border border-line text-fg-muted hover:bg-bg-hover hover:text-fg hover:border-line/80 active:bg-bg-active",
      },
      size: {
        default: "h-9 px-4 py-2",
        sm: "h-8 px-3 text-xs",
        lg: "h-11 px-6",
        icon: "h-9 w-9",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => (
    <button
      className={cn(buttonVariants({ variant, size, className }))}
      ref={ref}
      {...props}
    />
  )
);
Button.displayName = "Button";

export { Button, buttonVariants };
