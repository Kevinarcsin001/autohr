import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * 任务 26 E2E：Admin 后台（LLM 配置 CRUD + 统计页面）。
 *
 * 覆盖：
 * - 非 admin 访问 admin 页 → 拒绝提示
 * - admin 加载 LLM 配置列表（含全局默认）
 * - upsert 新配置 → 列表刷新
 * - 删除配置 → confirm → 列表移除
 * - 统计页：8 概要卡 + 时间序列图 + 维度表渲染
 * - 统计页：7d / 30d 切换
 */

const ADMIN_ME = {
  id: "u-admin",
  email: "admin@example.com",
  name: "Admin",
  role: "admin",
  team_id: "t-1",
};

const MEMBER_ME = {
  id: "u-member",
  email: "member@example.com",
  name: "Member",
  role: "member",
  team_id: "t-1",
};

async function mockMe(page: Page, role: "admin" | "member") {
  await page.route("**/api/auth/me", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(role === "admin" ? ADMIN_ME : MEMBER_ME),
    });
  });
  await page.route("**/api/auth/refresh", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ access_token: "fake-token" }),
    });
  });
}

test.describe("Admin LLM 配置（任务 25/26）", () => {
  test("非 admin 访问 LLM 配置页 → 权限不足提示", async ({ page }) => {
    await mockMe(page, "member");
    await page.goto("/admin/llm");

    await expect(page.getByText(/权限不足/)).toBeVisible({ timeout: 5_000 });
    await expect(
      page.getByText(/仅团队管理员可访问 LLM 路由配置/),
    ).toBeVisible();
  });

  test("admin 列表加载（含全局默认）", async ({ page }) => {
    await mockMe(page, "admin");
    const items = [
      {
        id: "cfg-1",
        team_id: null,
        scope: "extractor",
        primary: "zhipu",
        fallback: "qwen",
        model_overrides: null,
        timeout_seconds: null,
        circuit_breaker_failures: null,
        updated_at: "2026-06-18T10:00:00Z",
      },
      {
        id: "cfg-2",
        team_id: "t-1",
        scope: "scorer",
        primary: "qwen",
        fallback: "zhipu",
        model_overrides: null,
        timeout_seconds: 60,
        circuit_breaker_failures: 5,
        updated_at: "2026-06-18T11:00:00Z",
      },
    ];
    await page.route("**/api/admin/llm-configs", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items }),
      });
    });

    await page.goto("/admin/llm");

    await expect(page.getByText("LLM 路由配置")).toBeVisible();
    // 列表 badge 是精确文本（下拉 option 含 — 后缀），用 exact: true 避免歧义
    await expect(page.getByText("结构化抽取", { exact: true })).toBeVisible();
    await expect(page.getByText("评分", { exact: true })).toBeVisible();
    // 全局 / 本团队标签
    await expect(page.getByText("全局")).toBeVisible();
    await expect(page.getByText("本团队")).toBeVisible();
  });

  test("upsert 新配置 → 列表刷新", async ({ page }) => {
    await mockMe(page, "admin");

    let created = false;
    await page.route("**/api/admin/llm-configs**", async (route) => {
      const req = route.request();
      if (req.method() === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            items: created
              ? [
                  {
                    id: "cfg-new",
                    team_id: "t-1",
                    scope: "interview",
                    primary: "mock",
                    fallback: null,
                    model_overrides: null,
                    timeout_seconds: null,
                    circuit_breaker_failures: null,
                    updated_at: new Date().toISOString(),
                  },
                ]
              : [],
          }),
        });
      } else if (req.method() === "POST") {
        created = true;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            config: {
              id: "cfg-new",
              team_id: "t-1",
              scope: "interview",
              primary: "mock",
              fallback: null,
              model_overrides: null,
              timeout_seconds: null,
              circuit_breaker_failures: null,
              updated_at: new Date().toISOString(),
            },
            created: true,
          }),
        });
      }
    });

    await page.goto("/admin/llm");
    // 初始空
    await expect(page.getByText("暂无配置")).toBeVisible();

    // 选用途为「面试问题」
    await page.locator("#scope").selectOption("interview");
    await page.getByRole("button", { name: "保存" }).click();

    // 列表刷新后出现（badge 精确文本，避开同名 option）
    await expect(page.getByText("面试问题", { exact: true })).toBeVisible({
      timeout: 5_000,
    });
  });

  test("删除配置 confirm 后列表移除", async ({ page }) => {
    await mockMe(page, "admin");
    let deleted = false;
    const cfg = {
      id: "cfg-del",
      team_id: "t-1",
      scope: "scorer",
      primary: "qwen",
      fallback: "zhipu",
      model_overrides: null,
      timeout_seconds: null,
      circuit_breaker_failures: null,
      updated_at: "2026-06-18T10:00:00Z",
    };
    await page.route("**/api/admin/llm-configs", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: deleted ? [] : [cfg] }),
      });
    });
    await page.route("**/api/admin/llm-configs/*", async (route) => {
      if (route.request().method() === "DELETE") {
        deleted = true;
        await route.fulfill({ status: 204 });
      } else {
        await route.continue();
      }
    });

    await page.goto("/admin/llm");
    // 列表 badge 精确文本（"评分 — 评分维度计算" option 是子串）
    await expect(page.getByText("评分", { exact: true })).toBeVisible();

    // 拦截 confirm dialog
    page.on("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: /删除/ }).click();

    // 列表应清空
    await expect(page.getByText("暂无配置")).toBeVisible({ timeout: 5_000 });
  });
});

test.describe("Admin 统计（任务 25/26）", () => {
  test("非 admin 访问统计页 → 拒绝", async ({ page }) => {
    await mockMe(page, "member");
    await page.goto("/admin/stats");
    await expect(page.getByText(/权限不足/)).toBeVisible({ timeout: 5_000 });
  });

  test("统计概要卡 + 时间序列 + 维度表渲染", async ({ page }) => {
    await mockMe(page, "admin");
    const stats = {
      summary: {
        range: "7d",
        total_calls: 100,
        success_count: 90,
        failed_count: 10,
        success_rate: 0.9,
        total_tokens_in: 50000,
        total_tokens_out: 80000,
        total_cost_cny: 12.34,
        p50_latency_ms: 600,
        p95_latency_ms: 1500,
        p99_latency_ms: 2500,
      },
      by_scope: {
        dimension: "scope",
        items: [
          {
            key: "extractor",
            total_calls: 60,
            success_count: 55,
            failed_count: 5,
            total_tokens_in: 30000,
            total_tokens_out: 50000,
            total_cost_cny: 7.5,
          },
        ],
      },
      by_adapter: {
        dimension: "adapter",
        items: [
          {
            key: "zhipu",
            total_calls: 80,
            success_count: 75,
            failed_count: 5,
            total_tokens_in: 40000,
            total_tokens_out: 60000,
            total_cost_cny: 10.0,
          },
        ],
      },
      time_series: {
        range: "7d",
        granularity: "day",
        points: [
          {
            timestamp: "2026-06-12T00:00:00Z",
            total_calls: 10,
            success_count: 9,
            failed_count: 1,
            total_cost_cny: 1.2,
          },
          {
            timestamp: "2026-06-13T00:00:00Z",
            total_calls: 20,
            success_count: 18,
            failed_count: 2,
            total_cost_cny: 2.4,
          },
        ],
      },
    };
    await page.route("**/api/admin/stats*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(stats),
      });
    });

    await page.goto("/admin/stats");

    // 概要卡
    await expect(page.getByText("总调用")).toBeVisible();
    await expect(page.getByText("100")).toBeVisible();
    await expect(page.getByText("90.0%").first()).toBeVisible();
    // 维度卡
    await expect(page.getByText("按用途")).toBeVisible();
    await expect(page.getByText("按适配器")).toBeVisible();
    await expect(page.getByText("extractor")).toBeVisible();
    await expect(page.getByText("zhipu")).toBeVisible();
    // 时间序列标题
    await expect(page.getByText("时间序列")).toBeVisible();
  });

  test("7d / 30d 切换触发新请求", async ({ page }) => {
    await mockMe(page, "admin");
    let lastRange = "7d";
    await page.route("**/api/admin/stats*", async (route) => {
      const url = route.request().url();
      const match = url.match(/range=([^&]+)/);
      if (match) lastRange = decodeURIComponent(match[1]);
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          summary: {
            range: lastRange,
            total_calls: lastRange === "7d" ? 7 : 30,
            success_count: lastRange === "7d" ? 7 : 30,
            failed_count: 0,
            success_rate: 1,
            total_tokens_in: 0,
            total_tokens_out: 0,
            total_cost_cny: 0,
            p50_latency_ms: null,
            p95_latency_ms: null,
            p99_latency_ms: null,
          },
          by_scope: { dimension: "scope", items: [] },
          by_adapter: { dimension: "adapter", items: [] },
          time_series: {
            range: lastRange,
            granularity: "day",
            points: [],
          },
        }),
      });
    });

    await page.goto("/admin/stats");
    await expect(page.getByText("总调用")).toBeVisible();
    await expect(page.getByText(/^7$/).first()).toBeVisible({ timeout: 5_000 });

    // 切到 30d
    await page.locator("#range").selectOption("30d");

    // 应触发新请求 → 显示 30
    await expect(page.getByText(/^30$/).first()).toBeVisible({
      timeout: 5_000,
    });
  });
});
