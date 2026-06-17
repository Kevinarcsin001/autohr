import Link from "next/link";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-gradient-to-b from-slate-50 to-slate-100 px-4 py-12 dark:from-slate-950 dark:to-slate-900">
      <Link
        href="/"
        className="mb-8 text-2xl font-bold tracking-tight text-slate-900 dark:text-slate-100"
      >
        AutoHR
      </Link>
      <div className="w-full max-w-sm">{children}</div>
      <p className="mt-8 text-center text-xs text-slate-500">
        智能简历筛选助手 · 多源采集 · 双 LLM 评分
      </p>
    </div>
  );
}
