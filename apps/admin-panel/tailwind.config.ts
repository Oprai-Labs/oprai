import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        bg: {
          base: "hsl(var(--bg-base))",
          surface: "hsl(var(--bg-surface))",
          elevated: "hsl(var(--bg-elevated))",
          hover: "hsl(var(--bg-hover))",
          active: "hsl(var(--bg-active))",
          overlay: "hsl(var(--bg-overlay))",
        },
        fg: {
          DEFAULT: "hsl(var(--fg-default))",
          muted: "hsl(var(--fg-muted))",
          subtle: "hsl(var(--fg-subtle))",
          "on-accent": "hsl(var(--fg-on-accent))",
        },
        line: {
          DEFAULT: "hsl(var(--border-default))",
          muted: "hsl(var(--border-muted))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          hover: "hsl(var(--accent-hover))",
          muted: "hsl(var(--accent-muted))",
          subtle: "hsl(var(--accent-subtle))",
        },
        semantic: {
          success: "hsl(var(--success))",
          warning: "hsl(var(--warning))",
          danger: "hsl(var(--danger))",
          info: "hsl(var(--info))",
        },
        // Keep backward compat aliases for pages that use old tokens
        surface: {
          DEFAULT: "hsl(var(--bg-elevated))",
          muted: "hsl(var(--bg-surface))",
          subtle: "hsl(var(--bg-hover))",
          raised: "hsl(var(--bg-active))",
          panel: "hsl(var(--bg-base))",
        },
        text: {
          primary: "hsl(var(--fg-default))",
          secondary: "hsl(var(--fg-muted))",
          tertiary: "hsl(var(--fg-subtle))",
        },
        border: {
          subtle: "hsl(var(--border-default))",
          DEFAULT: "hsl(var(--border-default))",
        },
        brand: {
          primary: "hsl(var(--accent))",
          accent: "hsl(var(--accent-hover))",
        },
      },
      fontFamily: {
        sans: ["var(--font-inter)", "-apple-system", "BlinkMacSystemFont", "system-ui", "sans-serif"],
        mono: ["var(--font-jetbrains)", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
      },
      borderRadius: {
        DEFAULT: "8px",
        lg: "10px",
        xl: "12px",
        "2xl": "16px",
      },
      boxShadow: {
        "xs": "0 1px 2px 0 rgb(0 0 0 / 0.3)",
        "soft": "0 2px 8px -2px rgb(0 0 0 / 0.4), 0 0 0 1px rgb(255 255 255 / 0.03)",
        "md": "0 4px 16px -4px rgb(0 0 0 / 0.5), 0 0 0 1px rgb(255 255 255 / 0.03)",
        "lg": "0 8px 32px -8px rgb(0 0 0 / 0.6), 0 0 0 1px rgb(255 255 255 / 0.04)",
        "glow": "0 0 20px -5px hsl(var(--accent) / 0.2)",
      },
      animation: {
        "in": "fadeIn 150ms ease-out",
        "slide-up": "slideUp 200ms ease-out",
      },
      keyframes: {
        fadeIn: {
          from: { opacity: "0" },
          to: { opacity: "1" },
        },
        slideUp: {
          from: { opacity: "0", transform: "translateY(4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};

export default config;
