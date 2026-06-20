"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import {
  Briefcase,
  ChevronDown,
  FileText,
  Home,
  LogOut,
  Menu,
  PackageOpen,
  Settings,
  Upload,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { useLogout } from "@/hooks/useAuth";
import { useAuthStore } from "@/stores/authStore";

const NAV_ITEMS = [
  { href: "/dashboard", label: "工作台", icon: Home },
  { href: "/jobs", label: "职位", icon: Briefcase },
  { href: "/uploads", label: "上传", icon: Upload },
  { href: "/imports", label: "导入", icon: PackageOpen },
  { href: "/resumes", label: "简历库", icon: FileText },
];

const ADMIN_ITEMS = [
  { href: "/admin/members", label: "成员管理" },
  { href: "/admin/llm", label: "LLM 配置" },
  { href: "/admin/email", label: "邮箱配置" },
  { href: "/admin/stats", label: "统计" },
  { href: "/admin/dedup", label: "去重审核" },
  { href: "/admin/audit-logs", label: "审计日志" },
];

export function AppNav() {
  const user = useAuthStore((s) => s.user);
  const logout = useLogout();
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [adminOpen, setAdminOpen] = useState(false);

  if (!user) return null;

  const isActive = (href: string) =>
    pathname === href || pathname.startsWith(href + "/");

  return (
    <nav className="sticky top-0 z-40 border-b border-border bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="mx-auto flex h-14 max-w-7xl items-center gap-4 px-4">
        {/* Logo */}
        <Link
          href="/dashboard"
          className="flex items-center gap-2 font-semibold tracking-tight text-foreground hover:text-primary"
        >
          <Briefcase className="h-5 w-5 text-primary" />
          <span className="hidden sm:inline">AutoHR</span>
        </Link>

        {/* Desktop nav */}
        <div className="hidden items-center gap-1 md:flex">
          {NAV_ITEMS.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              className={
                "flex items-center gap-1.5 rounded-md px-3 py-2 text-sm font-medium transition-colors " +
                (isActive(href)
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-accent hover:text-accent-foreground")
              }
            >
              <Icon className="h-4 w-4" />
              {label}
            </Link>
          ))}

          {/* Admin dropdown */}
          {user.role === "admin" && (
            <div className="relative">
              <button
                type="button"
                onClick={() => setAdminOpen(!adminOpen)}
                className={
                  "flex items-center gap-1.5 rounded-md px-3 py-2 text-sm font-medium transition-colors " +
                  (pathname.startsWith("/admin")
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent hover:text-accent-foreground")
                }
              >
                <Settings className="h-4 w-4" />
                管理
                <ChevronDown
                  className={
                    "h-3 w-3 transition-transform " +
                    (adminOpen ? "rotate-180" : "")
                  }
                />
              </button>
              {adminOpen && (
                <>
                  <div
                    className="fixed inset-0 z-10"
                    onClick={() => setAdminOpen(false)}
                  />
                  <div className="absolute left-0 top-full z-20 mt-1 w-36 rounded-md border border-border bg-popover p-1 shadow-md">
                    {ADMIN_ITEMS.map(({ href, label }) => (
                      <Link
                        key={href}
                        href={href}
                        onClick={() => setAdminOpen(false)}
                        className={
                          "block rounded-sm px-3 py-1.5 text-sm " +
                          (isActive(href)
                            ? "bg-accent text-accent-foreground"
                            : "text-muted-foreground hover:bg-accent hover:text-accent-foreground")
                        }
                      >
                        {label}
                      </Link>
                    ))}
                  </div>
                </>
              )}
            </div>
          )}
        </div>

        {/* Right */}
        <div className="ml-auto flex items-center gap-2">
          <span className="hidden text-xs text-muted-foreground sm:inline">
            {user.email}
          </span>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => logout.mutate()}
            disabled={logout.isPending}
            className="hidden md:flex"
          >
            <LogOut className="mr-1 h-4 w-4" />
            登出
          </Button>

          {/* Mobile toggle */}
          <Button
            variant="ghost"
            size="icon"
            className="md:hidden"
            onClick={() => setMobileOpen(!mobileOpen)}
          >
            {mobileOpen ? (
              <X className="h-5 w-5" />
            ) : (
              <Menu className="h-5 w-5" />
            )}
          </Button>
        </div>
      </div>

      {/* Mobile menu */}
      {mobileOpen && (
        <div className="border-t border-border px-4 py-3 md:hidden">
          <div className="space-y-1">
            {NAV_ITEMS.map(({ href, label, icon: Icon }) => (
              <Link
                key={href}
                href={href}
                onClick={() => setMobileOpen(false)}
                className={
                  "flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium " +
                  (isActive(href)
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent")
                }
              >
                <Icon className="h-4 w-4" />
                {label}
              </Link>
            ))}
            {user.role === "admin" && ADMIN_ITEMS.map(({ href, label }) => (
              <Link
                key={href}
                href={href}
                onClick={() => setMobileOpen(false)}
                className={
                  "flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium " +
                  (isActive(href)
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent")
                }
              >
                <Settings className="h-4 w-4" />
                {label}
              </Link>
            ))}
            <button
              type="button"
              onClick={() => {
                logout.mutate();
                setMobileOpen(false);
              }}
              disabled={logout.isPending}
              className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm font-medium text-muted-foreground hover:bg-accent"
            >
              <LogOut className="h-4 w-4" />
              登出
            </button>
          </div>
        </div>
      )}
    </nav>
  );
}
