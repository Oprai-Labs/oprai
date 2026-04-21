"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import {
  LayoutDashboard,
  Users,
  ArrowLeftRight,
  MessageSquare,
  Globe,
  LogOut,
  BarChart3,
  ScrollText,
  Settings,
} from "lucide-react";
import { useAdminAuth } from "@/hooks/use-admin-auth";

const navGroups = [
  {
    label: "Overview",
    items: [
      { label: "Dashboard", href: "/dashboard", icon: LayoutDashboard },
      { label: "Analytics", href: "/analytics", icon: BarChart3 },
    ],
  },
  {
    label: "Data",
    items: [
      { label: "Users", href: "/users", icon: Users },
      { label: "Transactions", href: "/transactions", icon: ArrowLeftRight },
      { label: "Sessions", href: "/sessions", icon: MessageSquare },
      { label: "IP Logs", href: "/ip-logs", icon: Globe },
    ],
  },
  {
    label: "System",
    items: [
      { label: "Audit Logs", href: "/audit-logs", icon: ScrollText },
      { label: "Settings", href: "/settings", icon: Settings },
    ],
  },
];

export function AdminSidebar() {
  const pathname = usePathname();
  const { logout, username } = useAdminAuth();

  return (
    <aside className="flex h-screen w-[240px] flex-col border-r border-line-muted bg-bg-base">
      {/* Logo */}
      <div className="flex h-14 items-center gap-2.5 border-b border-line-muted px-5">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br from-accent to-accent-hover shadow-sm shadow-accent/20">
          <span className="text-xs font-bold text-fg-on-accent">O</span>
        </div>
        <span className="text-[15px] font-semibold tracking-tight text-fg">OPRAI</span>
        <span className="rounded-md border border-line-muted bg-bg-elevated px-1.5 py-0.5 text-[10px] font-medium text-fg-subtle">
          Admin
        </span>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto px-3 py-4">
        {navGroups.map((group) => (
          <div key={group.label} className="mb-5">
            <p className="mb-2 px-3 text-[10px] font-semibold uppercase tracking-[0.08em] text-fg-subtle">
              {group.label}
            </p>
            <div className="space-y-0.5">
              {group.items.map((item) => {
                const isActive = pathname === item.href || pathname.startsWith(item.href + "/");
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={cn(
                      "flex items-center gap-2.5 rounded-lg px-3 py-[7px] text-[13px] font-medium transition-all duration-100",
                      isActive
                        ? "bg-accent/10 text-accent"
                        : "text-fg-muted hover:bg-bg-hover hover:text-fg"
                    )}
                  >
                    <item.icon className={cn("h-4 w-4", isActive ? "text-accent" : "text-fg-subtle")} />
                    {item.label}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      {/* User + Logout */}
      <div className="border-t border-line-muted p-3">
        <div className="mb-2 flex items-center gap-2.5 px-3 py-1">
          <div className="flex h-6 w-6 items-center justify-center rounded-full bg-bg-elevated">
            <span className="text-[10px] font-semibold text-fg-muted">
              {username?.charAt(0).toUpperCase() || "A"}
            </span>
          </div>
          <span className="text-[13px] font-medium text-fg-muted">{username || "Admin"}</span>
        </div>
        <button
          onClick={logout}
          className="flex w-full items-center gap-2.5 rounded-lg px-3 py-[7px] text-[13px] font-medium text-fg-subtle transition-all duration-100 hover:bg-semantic-danger/8 hover:text-semantic-danger"
        >
          <LogOut className="h-4 w-4" />
          Sign Out
        </button>
      </div>
    </aside>
  );
}
