import Link from "next/link";

export default function HomePage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col items-center justify-center gap-8 p-8">
      <div className="flex flex-col items-center gap-3 text-center">
        <h1 className="text-5xl font-bold tracking-tight">AutoHR</h1>
        <p className="text-lg text-muted-foreground">
          智能简历筛选助手
        </p>
        <p className="max-w-xl text-sm text-muted-foreground">
          多源简历采集（上传 / 招聘平台 / 邮件）· 双 LLM（智谱 GLM-4 /
          通义千问）解析与评分 · 推荐理由与面试问题生成
        </p>
      </div>

      <div className="rounded-lg border border-border bg-card p-6 text-card-foreground shadow-sm">
        <h2 className="mb-3 text-lg font-semibold">脚手架已就绪 ✓</h2>
        <p className="mb-4 text-sm text-muted-foreground">
          后续任务将逐步实现登录、职位管理、上传、解析、评分等功能。
        </p>
        <ul className="space-y-1.5 text-sm">
          <li className="flex items-center gap-2">
            <span className="text-emerald-500">✓</span>
            <span>Next.js 14 + TypeScript + Tailwind</span>
          </li>
          <li className="flex items-center gap-2">
            <span className="text-emerald-500">✓</span>
            <span>FastAPI 后端 + PostgreSQL + Redis + MinIO</span>
          </li>
          <li className="flex items-center gap-2">
            <span className="text-emerald-500">✓</span>
            <span>Celery worker + beat（异步任务）</span>
          </li>
          <li className="flex items-center gap-2 text-muted-foreground">
            <span>○</span>
            <span>登录 / 团队（任务 5-6）</span>
          </li>
          <li className="flex items-center gap-2 text-muted-foreground">
            <span>○</span>
            <span>职位 + 硬性条件（任务 7）</span>
          </li>
          <li className="flex items-center gap-2 text-muted-foreground">
            <span>○</span>
            <span>简历采集 + 评分（任务 8-22）</span>
          </li>
        </ul>
      </div>

      <div className="flex gap-3 text-sm">
        <Link
          href="/login"
          className="rounded-md bg-primary px-4 py-2 font-medium text-primary-foreground hover:bg-primary/90"
        >
          登录
        </Link>
        <Link
          href="/register"
          className="rounded-md border border-border px-4 py-2 text-muted-foreground hover:bg-accent hover:text-accent-foreground"
        >
          注册
        </Link>
        <Link
          href="/docs"
          className="rounded-md border border-border px-4 py-2 text-muted-foreground hover:bg-accent hover:text-accent-foreground"
          target="_blank"
        >
          API 文档（/docs）
        </Link>
      </div>
    </main>
  );
}
