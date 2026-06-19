import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright 配置（任务 23）。
 *
 * 假设：
 * - 前端 dev server 在 localhost:3000
 * - 后端 API 在 localhost:8000
 *
 * 运行：
 *   pnpm test:e2e         # headless
 *   pnpm test:e2e:ui      # UI 模式
 *
 * 覆盖范围（按 tasks.md 要求）：
 * - 排序（点击列头）
 * - 筛选（输入 + 提交）
 * - 列自定义（勾选/排序）
 * - 视图保存（输入名称 → 应用 → 删除）
 * - SSE 进度（mock /api/screening/pipeline/{id}/events）
 *
 * 注：完整 SSE 测试需要后端真实数据，简化版只验证 UI 控件可见。
 */
export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? "github" : "html",
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || "http://localhost:3000",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  // 不自动启动 webServer（开发流程中前端已在跑）
  // CI 中可通过 PLAYWRIGHT_BASE_URL 指向部署环境
});
