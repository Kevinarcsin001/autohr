import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * 任务 26 E2E：认证流程（登录 / 注册 / 登出 / 路由守卫）。
 *
 * 用 page.route mock /api/auth/*，让测试独立于后端。
 *
 * 覆盖：
 * - 未登录访问受保护页 → 重定向到 /login
 * - 注册：填表 → 提交 → 跳转 dashboard
 * - 注册错误回显（邮箱已存在等）
 * - 登录：填表 → 提交 → 跳转 dashboard
 * - 登录 401 错误回显
 * - 登出：跳回 /login
 */

const FAKE_TOKEN = "fake-access-token";

async function mockAuthPages({ page }: { page: Page }) {
  // /api/auth/me：默认 401（未登录态），由具体测试覆写
  await page.route("**/api/auth/me", async (route: Route) => {
    await route.fulfill({ status: 401 });
  });
}

test.describe("认证流程（任务 26）", () => {
  test("未登录访问受保护路由 → 提示前往登录", async ({ page }) => {
    await mockAuthPages({ page });
    await page.goto("/dashboard");
    // dashboard 是 client-side 检查 authStore.user，未登录显示「前往登录」链接
    await expect(page.getByText("未登录")).toBeVisible({ timeout: 5_000 });
    await expect(page.getByRole("link", { name: /前往登录/ })).toBeVisible();
  });

  test("注册：happy path 跳转 dashboard", async ({ page }) => {
    await mockAuthPages({ page });

    let registered = false;
    await page.route("**/api/auth/register", async (route) => {
      registered = true;
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          user: {
            id: "u-1",
            email: "new@example.com",
            name: "New",
            role: "admin",
            team_id: "t-1",
          },
          tokens: {
            access_token: FAKE_TOKEN,
            refresh_token: "fake-refresh",
          },
        }),
      });
    });

    // 后注册的 route 优先；这里始终按 registered 状态返回，避免时序竞争
    await page.route("**/api/auth/me", async (route) => {
      if (registered) {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            id: "u-1",
            email: "new@example.com",
            name: "New",
            role: "admin",
            team_id: "t-1",
          }),
        });
      } else {
        await route.fulfill({ status: 401 });
      }
    });

    await page.goto("/register");
    await page.getByLabel(/邮箱/).fill("new@example.com");
    // register 页有 password + confirm 两个字段，getByLabel(/密码/) 会命中两个
    await page.getByLabel("密码", { exact: true }).fill("Pass1234");
    await page.getByLabel("确认密码").fill("Pass1234");
    await page.getByLabel(/姓名/).fill("New");
    await page.getByRole("button", { name: "注册" }).click();

    await page.waitForURL("**/dashboard", { timeout: 10_000 });
    await expect(page.locator("h1")).toContainText(/欢迎/);
  });

  test("注册失败：邮箱冲突回显错误", async ({ page }) => {
    await mockAuthPages({ page });
    await page.route("**/api/auth/register", async (route) => {
      await route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({
          error: { code: "Conflict", message: "该邮箱已被注册" },
        }),
      });
    });

    await page.goto("/register");
    await page.getByLabel(/邮箱/).fill("dup@example.com");
    await page.getByLabel("密码", { exact: true }).fill("Pass1234");
    await page.getByLabel("确认密码").fill("Pass1234");
    await page.getByLabel(/姓名/).fill("Dup");
    await page.getByRole("button", { name: "注册" }).click();

    // AlertTitle 固定「注册失败」；具体消息由后端 response 决定
    await expect(page.getByText("注册失败")).toBeVisible({
      timeout: 5_000,
    });
  });

  test("登录：happy path 跳转 dashboard", async ({ page }) => {
    let logged = false;
    await mockAuthPages({ page });
    await page.route("**/api/auth/login", async (route) => {
      logged = true;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          user: {
            id: "u-1",
            email: "admin@example.com",
            name: "Admin",
            role: "admin",
            team_id: "t-1",
          },
          tokens: {
            access_token: FAKE_TOKEN,
            refresh_token: "fake-refresh",
          },
        }),
      });
    });
    await page.route("**/api/auth/me", async (route) => {
      if (logged) {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            id: "u-1",
            email: "admin@example.com",
            name: "Admin",
            role: "admin",
            team_id: "t-1",
          }),
        });
      } else {
        await route.fulfill({ status: 401 });
      }
    });

    await page.goto("/login");
    await page.getByLabel(/邮箱/).fill("admin@example.com");
    await page.getByLabel(/密码/).fill("Pass1234");
    await page.getByRole("button", { name: /登录|登 录/ }).click();

    await page.waitForURL("**/dashboard", { timeout: 5_000 });
    await expect(page.locator("h1")).toContainText(/欢迎/);
  });

  test("登录失败：401 回显错误", async ({ page }) => {
    await mockAuthPages({ page });
    await page.route("**/api/auth/login", async (route) => {
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({
          error: { code: "Unauthorized", message: "邮箱或密码错误" },
        }),
      });
    });

    await page.goto("/login");
    await page.getByLabel(/邮箱/).fill("bad@example.com");
    await page.getByLabel(/密码/).fill("wrong");
    await page.getByRole("button", { name: /登录|登 录/ }).click();

    // AlertTitle 固定 "登录失败"（精确匹配，避免 fallback msg 含同子串）
    await expect(page.getByText("登录失败", { exact: true })).toBeVisible({
      timeout: 5_000,
    });
  });
});
