"use client";

import Link from "next/link";
import {
  ArrowRight,
  Briefcase,
  FileUp,
  GitMerge,
  ListChecks,
  PackageOpen,
  Plus,
  ScanSearch,
  ScrollText,
  Upload,
  Users,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useAuthStore } from "@/stores/authStore";
import { useDashboardStats } from "@/hooks/useDashboardStats";

const WORKFLOW_STEPS = [
  {
    step: 1,
    title: "上传简历",
    desc: "拖拽上传 PDF/Word/图片，支持批量导入",
    href: "/uploads",
    icon: FileUp,
    color: "bg-blue-50 text-blue-600 dark:bg-blue-950/30 dark:text-blue-400",
  },
  {
    step: 2,
    title: "创建职位",
    desc: "定义 JD 与硬性筛选条件",
    href: "/jobs/new",
    icon: Briefcase,
    color: "bg-violet-50 text-violet-600 dark:bg-violet-950/30 dark:text-violet-400",
  },
  {
    step: 3,
    title: "智能筛选",
    desc: "进入简历库，选择候选人关联职位后点击「触发筛选」",
    href: "/resumes",
    icon: ScanSearch,
    color: "bg-amber-50 text-amber-600 dark:bg-amber-950/30 dark:text-amber-400",
  },
  {
    step: 4,
    title: "决策审阅",
    desc: "在候选人详情页查看评分/推荐理由/面试问题，支持 HR 改判",
    href: "/resumes",
    icon: ListChecks,
    color: "bg-emerald-50 text-emerald-600 dark:bg-emerald-950/30 dark:text-emerald-400",
  },
];

export default function DashboardPage() {
  const user = useAuthStore((s) => s.user);
  const status = useAuthStore((s) => s.status);
  const { data: stats } = useDashboardStats();

  if (status === "loading") {
    return <div className="p-8">加载中...</div>;
  }
  if (!user) {
    return (
      <div className="p-8">
        <p>未登录</p>
        <Link href="/login" className="text-primary underline">
          前往登录
        </Link>
      </div>
    );
  }

  const isAdmin = user.role === "admin";

  return (
    <div className="mx-auto max-w-6xl space-y-8 px-4 py-8 sm:px-6 lg:px-8">
      {/* 欢迎横幅 */}
      <div className="overflow-hidden rounded-xl bg-gradient-to-r from-slate-900 to-slate-700 p-6 text-white sm:p-8 dark:from-slate-800 dark:to-slate-900">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="text-2xl font-bold">欢迎回来，{user.name}</h1>
            <p className="mt-1 text-sm text-slate-300">
              {isAdmin ? "团队管理员" : "团队成员"} · 智能简历筛选工作台
            </p>
          </div>
          <div className="flex gap-2">
            <Button asChild size="sm" variant="secondary">
              <Link href="/uploads">
                <Upload className="mr-1.5 h-4 w-4" />
                上传简历
              </Link>
            </Button>
            <Button asChild size="sm" variant="secondary">
              <Link href="/jobs/new">
                <Plus className="mr-1.5 h-4 w-4" />
                新建职位
              </Link>
            </Button>
          </div>
        </div>
      </div>

      {/* 实时统计 */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
        {[
          { label: "总候选人", value: stats?.total_candidates ?? "-", href: "/resumes", color: "text-blue-600" },
          { label: "待处理", value: stats?.pending_candidates ?? "-", href: "/resumes", color: "text-amber-600" },
          { label: "已通过", value: stats?.passed_candidates ?? "-", href: "/resumes", color: "text-emerald-600" },
          { label: "已淘汰", value: stats?.disqualified_candidates ?? "-", href: "/resumes", color: "text-red-500" },
          { label: "招聘中", value: stats?.active_jobs ?? "-", href: "/jobs", color: "text-violet-600" },
          { label: "待复核", value: stats?.pending_reviews ?? "-", href: "/resumes", color: "text-orange-500" },
        ].map(({ label, value, href, color }) => (
          <Link key={label} href={href}>
            <Card className="h-full transition-colors hover:border-primary/40">
              <CardContent className="flex flex-col items-center justify-center py-4 text-center">
                <span className={`text-2xl font-bold tabular-nums ${color}`}>{value}</span>
                <span className="mt-1 text-xs text-muted-foreground">{label}</span>
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>

      {/* 工作流指引 */}
      <div>
        <div className="mb-4 flex items-center gap-2">
          <h2 className="text-base font-semibold">快速开始</h2>
          <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
            4 步流程
          </span>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {WORKFLOW_STEPS.map(
            ({ step, title, desc, href, icon: Icon, color }) => (
              <Link key={step} href={href}>
                <Card className="group h-full shadow-xs transition-all hover:shadow-md hover:ring-1 hover:ring-border">
                  <CardContent className="flex flex-col gap-3 p-5">
                    <div
                      className={`flex h-10 w-10 items-center justify-center rounded-xl ${color}`}
                    >
                      <Icon className="h-5 w-5" />
                    </div>
                    <div>
                      <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                        步骤 {step}
                      </span>
                      <h3 className="mt-1 text-sm font-semibold">{title}</h3>
                      <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                        {desc}
                      </p>
                    </div>
                  </CardContent>
                </Card>
              </Link>
            ),
          )}
        </div>
      </div>

      {/* 下半区 */}
      <div className="grid gap-6 lg:grid-cols-3">
        {/* 账户信息 */}
        <Card className="lg:col-span-1">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-semibold">账户信息</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center gap-3 rounded-lg bg-muted/40 px-3 py-2.5">
              <div className="flex h-9 w-9 items-center justify-center rounded-full bg-primary/10 text-sm font-semibold text-primary">
                {user.name?.charAt(0) ?? "?"}
              </div>
              <div className="min-w-0">
                <div className="truncate text-sm font-medium">{user.name}</div>
                <div className="truncate text-xs text-muted-foreground">
                  {user.email}
                </div>
              </div>
            </div>
            <div className="space-y-1.5 text-sm">
              <div className="flex justify-between">
                <span className="text-muted-foreground">角色</span>
                <span className="font-medium">
                  {isAdmin ? "团队管理员" : "普通成员"}
                </span>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* 快速入口 */}
        <Card className="lg:col-span-2">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-semibold">常用功能</CardTitle>
            <CardDescription>快速访问各模块</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid gap-2 sm:grid-cols-2">
              <Link
                href="/jobs"
                className="flex items-center gap-3 rounded-lg border p-3 text-sm transition-colors hover:bg-accent"
              >
                <Briefcase className="h-4 w-4 shrink-0 text-muted-foreground" />
                <span className="flex-1">职位管理</span>
                <ArrowRight className="h-3.5 w-3.5 text-muted-foreground/50" />
              </Link>
              <Link
                href="/uploads"
                className="flex items-center gap-3 rounded-lg border p-3 text-sm transition-colors hover:bg-accent"
              >
                <Upload className="h-4 w-4 shrink-0 text-muted-foreground" />
                <span className="flex-1">简历上传</span>
                <ArrowRight className="h-3.5 w-3.5 text-muted-foreground/50" />
              </Link>
              <Link
                href="/imports"
                className="flex items-center gap-3 rounded-lg border p-3 text-sm transition-colors hover:bg-accent"
              >
                <PackageOpen className="h-4 w-4 shrink-0 text-muted-foreground" />
                <span className="flex-1">平台导入</span>
                <ArrowRight className="h-3.5 w-3.5 text-muted-foreground/50" />
              </Link>
              {isAdmin && (
                <>
                  <Link
                    href="/admin/members"
                    className="flex items-center gap-3 rounded-lg border p-3 text-sm transition-colors hover:bg-accent"
                  >
                    <Users className="h-4 w-4 shrink-0 text-muted-foreground" />
                    <span className="flex-1">成员管理</span>
                    <ArrowRight className="h-3.5 w-3.5 text-muted-foreground/50" />
                  </Link>
                  <Link
                    href="/admin/stats"
                    className="flex items-center gap-3 rounded-lg border p-3 text-sm transition-colors hover:bg-accent"
                  >
                    <ScanSearch className="h-4 w-4 shrink-0 text-muted-foreground" />
                    <span className="flex-1">调用统计</span>
                    <ArrowRight className="h-3.5 w-3.5 text-muted-foreground/50" />
                  </Link>
                  <Link
                    href="/admin/dedup"
                    className="flex items-center gap-3 rounded-lg border p-3 text-sm transition-colors hover:bg-accent"
                  >
                    <GitMerge className="h-4 w-4 shrink-0 text-muted-foreground" />
                    <span className="flex-1">去重审核</span>
                    <ArrowRight className="h-3.5 w-3.5 text-muted-foreground/50" />
                  </Link>
                  <Link
                    href="/admin/audit-logs"
                    className="flex items-center gap-3 rounded-lg border p-3 text-sm transition-colors hover:bg-accent"
                  >
                    <ScrollText className="h-4 w-4 shrink-0 text-muted-foreground" />
                    <span className="flex-1">审计日志</span>
                    <ArrowRight className="h-3.5 w-3.5 text-muted-foreground/50" />
                  </Link>
                </>
              )}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
