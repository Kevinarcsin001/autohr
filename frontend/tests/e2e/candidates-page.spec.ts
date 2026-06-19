import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * 任务 23：候选人列表页 E2E smoke。
 *
 * 设计：用 page.route() mock 所有 /api/* 调用，让测试独立于后端运行。
 *
 * 覆盖（按 tasks.md 要求）：
 * - 关键 UI 元素渲染（三分组 / 筛选栏 / 工具栏）
 * - 排序：点击列头切换 sort_by / sort_order（写入 URL）
 * - 筛选：输入 skill 写入 URL query
 * - 列自定义：菜单可打开
 * - 视图保存：保存按钮可展开输入框
 * - SSE 进度：mock /events 端点验证进度提示渲染
 *
 * 注：access_token 仅存 zustand 内存，无法注入；
 * 通过 mock /api/auth/me 返回 authenticated 绕过登录守卫。
 */

const FAKE_TOKEN = "fake-access-token-for-e2e";
const FAKE_JOB_ID = "00000000-0000-0000-0000-000000000001";

interface MockOptions {
  page: Page;
  candidates?: unknown;
  meAuthenticated?: boolean;
  sseEvents?: string[];
}

async function mockApi({
  page,
  candidates = { items: [], total: 0, page: 1, page_size: 50, group_counts: { passed: 0, disqualified: 0, pending: 0 } },
  meAuthenticated = true,
  sseEvents = [],
}: MockOptions) {
  await page.route("**/api/auth/me", async (route: Route) => {
    if (meAuthenticated) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "u-1",
          email: "e2e@example.com",
          name: "E2E",
          role: "admin",
          team_id: "t-1",
        }),
      });
    } else {
      await route.fulfill({ status: 401 });
    }
  });

  await page.route("**/api/auth/refresh", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ access_token: FAKE_TOKEN }),
    });
  });

  await page.route(`**/api/jobs/${FAKE_JOB_ID}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: FAKE_JOB_ID,
        title: "E2E 测试职位",
        jd_text: "x",
        status: "active",
        current_version: 1,
        hard_requirements: {},
        created_at: new Date().toISOString(),
      }),
    });
  });

  await page.route("**/api/jobs/*/candidates**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(candidates),
    });
  });

  await page.route("**/api/screening/pipeline", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        run_id: "run-1",
        job_id: FAKE_JOB_ID,
        total: 1,
      }),
    });
  });

  // SSE 端点：返回固定事件序列后关闭流
  await page.route("**/api/screening/pipeline/*/events", async (route) => {
    if (sseEvents.length === 0) {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: "",
      });
      return;
    }
    const body = sseEvents.join("\n\n");
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      headers: { "Cache-Control": "no-cache" },
      body,
    });
  });
}

test.describe("候选人列表页（任务 23）", () => {
  test("页面加载 + 关键 UI 元素可见", async ({ page }) => {
    await mockApi({
      page,
      candidates: {
        items: [
          {
            id: "c-1",
            name: "张三",
            email: "zhang@example.com",
            phone: null,
            source_type: "upload",
            source_id: "s-1",
            screening_id: "sr-1",
            disqualified: false,
            screening_reasons: [],
            manually_overridden: false,
            score_id: "sc-1",
            total: 88,
            skill: 80,
            experience: 75,
            education_score: 90,
            stability: 70,
            potential: 85,
            model_used: "zhipu",
            education: "master",
            years_of_experience: 5,
            current_company: "ACME",
            skills: ["Python", "FastAPI"],
            group: "passed",
            created_at: "2026-06-01T10:00:00Z",
            updated_at: null,
          },
        ],
        total: 1,
        page: 1,
        page_size: 50,
        group_counts: { passed: 1, disqualified: 0, pending: 0 },
      },
    });

    await page.goto(`/jobs/${FAKE_JOB_ID}/candidates`);

    // 关键 UI
    await expect(page.locator("h1")).toContainText("候选人");
    await expect(page.getByRole("tab", { name: /全部/ })).toBeVisible();
    await expect(page.getByRole("tab", { name: /通过/ })).toBeVisible();
    await expect(page.getByRole("tab", { name: /淘汰/ })).toBeVisible();
    await expect(page.getByRole("tab", { name: /待复核/ })).toBeVisible();
    await expect(page.getByRole("button", { name: /触发筛选/ })).toBeVisible();
    await expect(
      page.getByRole("button", { name: /导出 Excel/ }),
    ).toBeVisible();
    await expect(page.getByLabel("技能")).toBeVisible();
    await expect(page.getByText("张三")).toBeVisible();
    // 工具栏：列自定义按钮
    await expect(page.getByRole("button", { name: /^列$/ })).toBeVisible();
  });

  test("三分组数字显示", async ({ page }) => {
    await mockApi({
      page,
      candidates: {
        items: [],
        total: 6,
        page: 1,
        page_size: 50,
        group_counts: {
          passed: 3,
          disqualified: 2,
          pending: 1,
        },
      },
    });

    await page.goto(`/jobs/${FAKE_JOB_ID}/candidates`);

    // 全部 tab 显示总数 6
    await expect(page.getByRole("tab", { name: /全部.*6/ })).toBeVisible();
    await expect(page.getByRole("tab", { name: /通过.*3/ })).toBeVisible();
    await expect(page.getByRole("tab", { name: /淘汰.*2/ })).toBeVisible();
    await expect(page.getByRole("tab", { name: /待复核.*1/ })).toBeVisible();
  });

  test("筛选条件写入 URL query（持久化）", async ({ page }) => {
    await mockApi({ page });

    await page.goto(`/jobs/${FAKE_JOB_ID}/candidates`);
    const skillInput = page.getByLabel("技能");
    await skillInput.fill("Python");
    // 等 onChange 写入 URL 后再提交（避免 Enter 提交时拿不到最新 skill）
    await page.waitForFunction(
      () => window.location.search.includes("skill="),
      { timeout: 5_000 },
    );
    await skillInput.press("Enter");

    await page.waitForURL(
      (url) => url.searchParams.get("skill") === "Python",
      { timeout: 5_000 },
    );
  });

  test("分组切换写入 URL", async ({ page }) => {
    await mockApi({ page });

    await page.goto(`/jobs/${FAKE_JOB_ID}/candidates`);
    await page.getByRole("tab", { name: /通过/ }).click();
    await page.waitForURL(
      (url) => url.searchParams.get("group") === "passed",
      { timeout: 5_000 },
    );
    expect(page.url()).toContain("group=passed");
  });

  test("排序：点击列头切换方向（URL 同步）", async ({ page }) => {
    await mockApi({ page });

    await page.goto(`/jobs/${FAKE_JOB_ID}/candidates`);

    // 点击「总分」列头中的排序按钮（sort handler 在 <button> 上）
    const totalCol = page.getByRole("columnheader", { name: /总分/ });
    await totalCol.getByRole("button").click();

    // 第一次点击应改成 asc 或 desc 之一；只要 URL 反映了状态即可
    await page.waitForURL(
      (url) => url.searchParams.get("sort_by") === "total",
      { timeout: 5_000 },
    );
    expect(page.url()).toMatch(/sort_by=total/);
  });

  test("列自定义菜单可打开 + 显示列列表", async ({ page }) => {
    await mockApi({ page });
    await page.goto(`/jobs/${FAKE_JOB_ID}/candidates`);

    await page.getByRole("button", { name: /^列$/ }).click();
    const dialog = page.getByRole("dialog", { name: "列自定义" });
    await expect(dialog).toBeVisible();
    // 至少包含"姓名"和"总分"两个列选项
    await expect(dialog.getByLabel(/显示列 姓名/)).toBeVisible();
    await expect(dialog.getByLabel(/显示列 总分/)).toBeVisible();
  });

  test("保存视图入口可展开 + 输入", async ({ page }) => {
    await mockApi({ page });
    await page.goto(`/jobs/${FAKE_JOB_ID}/candidates`);

    await page.getByRole("button", { name: "保存当前视图" }).click();
    const input = page.getByPlaceholder("视图名称（如：高潜工程师）");
    await expect(input).toBeVisible();
    await input.fill("测试视图");
    await expect(input).toHaveValue("测试视图");
  });

  test("密度切换三个按钮可见", async ({ page }) => {
    await mockApi({ page });
    await page.goto(`/jobs/${FAKE_JOB_ID}/candidates`);

    await expect(page.getByRole("button", { name: "紧凑" })).toBeVisible();
    await expect(page.getByRole("button", { name: "标准" })).toBeVisible();
    await expect(page.getByRole("button", { name: "宽松" })).toBeVisible();
  });

  test("键盘导航提示可见", async ({ page }) => {
    await mockApi({ page });
    await page.goto(`/jobs/${FAKE_JOB_ID}/candidates`);

    await expect(
      page.getByText(/方向键移动.*Enter 打开详情/),
    ).toBeVisible();
  });

  test("SSE 进度提示渲染（done 事件）", async ({ page }) => {
    // 模拟一次 SSE 完成事件
    const doneEvent = [
      'event: done',
      'id: 1',
      'data: {"total":1,"passed":1,"disqualified":0,"failed":0}',
    ].join("\n");

    await mockApi({
      page,
      sseEvents: [doneEvent],
    });

    await page.goto(`/jobs/${FAKE_JOB_ID}/candidates`);

    // 点击触发筛选按钮
    await page.getByRole("button", { name: /触发筛选/ }).click();

    // 应出现进度提示文字（包含 "完成" 或 "已启动"）
    await expect(
      page.locator("text=/完成|已启动|进行中/").first(),
    ).toBeVisible({ timeout: 5_000 });
  });
});
