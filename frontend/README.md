# AutoHR Frontend

Next.js 14 (App Router) + TypeScript + Tailwind + shadcn/ui。

## 本地开发

### 在容器中（推荐）

```bash
# 在项目根目录
make up
# 访问 http://localhost:3000
```

### 不使用 Docker

```bash
cd frontend
pnpm install
pnpm dev
```

## 目录结构

```
frontend/
├── app/                   # App Router 路由
│   ├── layout.tsx
│   ├── page.tsx           # 首页
│   ├── (auth)/            # 登录/注册（任务 5）
│   ├── jobs/              # 职位（任务 7）
│   ├── candidates/        # 候选人详情（任务 24）
│   ├── uploads/           # 上传中心（任务 9）
│   └── admin/             # 后台（任务 25）
├── components/            # 复用组件
│   └── ui/                # shadcn/ui 组件
├── lib/                   # API client、auth、utils
├── hooks/                 # TanStack Query hooks
└── stores/                # Zustand 全局状态
```

## shadcn/ui 添加新组件

```bash
pnpm dlx shadcn-ui@latest add button
pnpm dlx shadcn-ui@latest add dialog table form
```

## 测试

```bash
pnpm test           # Vitest 单元测试
pnpm test:e2e       # Playwright E2E（任务 26）
```
