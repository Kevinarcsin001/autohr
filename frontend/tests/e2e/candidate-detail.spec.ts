import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * 任务 24：候选人详情页 E2E smoke。
 *
 * 设计：用 page.route() mock 所有 /api/* 调用，让测试独立于后端运行。
 *
 * 覆盖（按 plan 要求）：
 * - 关键 UI 元素渲染（header / tabs / 左侧简历 / 右侧面板）
 * - Tab 切换：structure / score / reasons / interview
 * - 推荐理由「查看依据」模态高亮
 * - 改判弹窗打开 + 提交
 */

const FAKE_TOKEN = "fake-access-token-for-e2e";
const FAKE_JOB_ID = "00000000-0000-0000-0000-000000000001";
const FAKE_CANDIDATE_ID = "00000000-0000-0000-0000-000000000002";
const FAKE_SCREENING_ID = "00000000-0000-0000-0000-000000000003";
const FAKE_SCORE_ID = "00000000-0000-0000-0000-000000000004";

interface MockOptions {
  page: Page;
  detail?: unknown;
  resumeUrl?: unknown;
  activity?: unknown;
  reasons?: unknown;
  interview?: unknown;
  meAuthenticated?: boolean;
}

async function mockApi({
  page,
  detail,
  resumeUrl = {
    url: "https://example.com/resume.pdf?X-Amz-Signature=abc",
    expires_at: new Date(Date.now() + 5 * 60_000).toISOString(),
    mime_type: "application/pdf",
    filename: null,
  },
  activity = { items: [], total: 0, page: 1, page_size: 20 },
  reasons = { items: [], total: 0 },
  interview = { items: [], total: 0 },
  meAuthenticated = true,
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

  const defaultDetail = {
    candidate: {
      id: FAKE_CANDIDATE_ID,
      name: "张三",
      phone: "13800000001",
      email: "zs@example.com",
      source_type: "upload",
      source_id: null,
      created_at: "2026-06-01T10:00:00Z",
    },
    screening_result: {
      id: FAKE_SCREENING_ID,
      job_id: FAKE_JOB_ID,
      candidate_id: FAKE_CANDIDATE_ID,
      disqualified: false,
      reasons: null,
      manually_overridden: false,
    },
    score: {
      id: FAKE_SCORE_ID,
      job_id: FAKE_JOB_ID,
      candidate_id: FAKE_CANDIDATE_ID,
      total: 88,
      skill: 85,
      experience: 80,
      education: 90,
      stability: 78,
      potential: 88,
      model_used: "mock",
      llm_call_id: null,
    },
    parsed_structure: {
      name: "张三",
      name_confidence: 0.95,
      phone: "13800000001",
      phone_confidence: 0.9,
      email: "zs@example.com",
      email_confidence: 0.95,
      education: "master",
      education_confidence: 0.85,
      years_of_experience: 5,
      years_of_experience_confidence: 0.8,
      skills: ["Python", "FastAPI"],
      skills_confidence: 0.7,
      expected_salary: null,
      expected_salary_confidence: 0,
      current_company: "ACME",
      current_company_confidence: 0.6,
      work_history: [],
      work_history_confidence: 0,
    },
    resume: {
      id: "r-1",
      parsed_text: "候选人拥有五年 Python 后端开发经验，熟悉 FastAPI 框架",
      file_storage_key: "team-x/r-1.pdf",
      mime_type: "application/pdf",
      filename: null,
    },
  };

  await page.route(
    `**/api/candidates/${FAKE_CANDIDATE_ID}/detail**`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(detail ?? defaultDetail),
      });
    },
  );

  await page.route(
    `**/api/candidates/${FAKE_CANDIDATE_ID}/resume-url`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(resumeUrl),
      });
    },
  );

  await page.route(
    `**/api/candidates/${FAKE_CANDIDATE_ID}/activity**`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(activity),
      });
    },
  );

  await page.route(`**/api/reasons/by-score/**`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(reasons),
    });
  });

  await page.route(`**/api/interview/questions**`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(interview),
    });
  });
}

test.describe("候选人详情页（任务 24）", () => {
  test("页面加载 + 关键 UI 元素可见", async ({ page }) => {
    await mockApi({ page });

    await page.goto(
      `/jobs/${FAKE_JOB_ID}/candidates/${FAKE_CANDIDATE_ID}`,
    );

    // header
    await expect(page.locator("h1")).toContainText("张三");
    await expect(
      page.getByText("zs@example.com", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText(/通过/).first()).toBeVisible();
    await expect(
      page.getByRole("button", { name: /HR 改判/ }),
    ).toBeVisible();

    // 默认 tab = structure
    await expect(page.getByText("结构化字段")).toBeVisible();
    await expect(page.getByText("硕士")).toBeVisible();

    // tab 切换
    await page.getByRole("tab", { name: "评分" }).click();
    await expect(page.getByText("评分细项")).toBeVisible();
    await expect(page.getByText("综合", { exact: true })).toBeVisible();

    await page.getByRole("tab", { name: "理由" }).click();
    await expect(page.getByText("推荐理由")).toBeVisible();

    await page.getByRole("tab", { name: "面试" }).click();
    await expect(page.getByText("面试问题")).toBeVisible();
  });

  test("推荐理由 + 查看依据高亮", async ({ page }) => {
    await mockApi({
      page,
      reasons: {
        items: [
          {
            id: "r-1",
            score_id: FAKE_SCORE_ID,
            type: "recommend",
            bullet_points: ["五年 Python 后端开发经验"],
            validated: true,
          },
        ],
        total: 1,
      },
    });

    await page.goto(
      `/jobs/${FAKE_JOB_ID}/candidates/${FAKE_CANDIDATE_ID}`,
    );

    // 切到理由 tab
    await page.getByRole("tab", { name: "理由" }).click();
    await expect(page.getByText("五年 Python 后端开发经验")).toBeVisible();

    // 点击查看依据
    await page.getByRole("button", { name: "查看依据" }).click();

    // 模态出现 + 高亮
    const dialog = page.getByRole("dialog", { name: "理由依据" });
    await expect(dialog).toBeVisible();
    await expect(dialog.locator("mark")).toContainText(/Python/);
  });

  test("改判弹窗可打开 + 输入 + 提交", async ({ page }) => {
    let overrideCalled = false;
    await page.route(
      `**/api/screening/results/${FAKE_SCREENING_ID}/override`,
      async (route) => {
        overrideCalled = true;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            screening_result: {
              id: FAKE_SCREENING_ID,
              job_id: FAKE_JOB_ID,
              candidate_id: FAKE_CANDIDATE_ID,
              disqualified: true,
              reasons: ["HR 复核不通过"],
              manually_overridden: true,
            },
            override_id: "ov-1",
          }),
        });
      },
    );

    await mockApi({ page });

    await page.goto(
      `/jobs/${FAKE_JOB_ID}/candidates/${FAKE_CANDIDATE_ID}`,
    );

    // 点击 HR 改判
    await page.getByRole("button", { name: /HR 改判/ }).click();

    const dialog = page.getByRole("dialog", { name: "HR 改判候选人" });
    await expect(dialog).toBeVisible();

    // 选择「淘汰」
    await dialog.getByRole("button", { name: "淘汰" }).click();

    // 填改判说明
    await dialog
      .getByPlaceholder(/详细说明改判原因/)
      .fill("面试反馈差，技能不匹配");

    // 提交
    await dialog.getByRole("button", { name: "确认改判" }).click();

    // 等待 override 调用
    await page.waitForTimeout(500);
    expect(overrideCalled).toBe(true);
  });

  test("活动日志渲染 + 类型徽标", async ({ page }) => {
    await mockApi({
      page,
      activity: {
        items: [
          {
            type: "audit_log",
            id: "a-1",
            created_at: "2026-06-01T10:00:00Z",
            actor_id: "u-1",
            action: "candidate.update",
            summary: "候选人信息更新",
            details: null,
          },
          {
            type: "override",
            id: "o-1",
            created_at: "2026-06-01T11:00:00Z",
            actor_id: "u-2",
            action: "screening.override",
            summary: "HR 改判为通过",
            details: null,
          },
        ],
        total: 2,
        page: 1,
        page_size: 20,
      },
    });

    await page.goto(
      `/jobs/${FAKE_JOB_ID}/candidates/${FAKE_CANDIDATE_ID}`,
    );

    await expect(page.getByText("活动日志")).toBeVisible();
    await expect(page.getByText(/共 2 条/)).toBeVisible();
    await expect(page.getByText("候选人信息更新")).toBeVisible();
    await expect(page.getByText("HR 改判为通过")).toBeVisible();
  });
});
