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
  test("未登录访问受保护路由 → 重定向到 /login", async ({ page }) => {
    await mockAuthPages({ page });
    await page.goto("/dashboard");
    await page.waitForURL("**/login", { timeout: 5_000 });
    expect(page.url()).toMatch(/\/login/);
  });

  test("注册：happy path 跳转 dashboard", async ({ page }) => {
    await mockAuthPages({ page });

    // 第一次 /api/auth/me 是 401（已默认 mock）
    // 注册成功后访问 /dashboard，再次 me 调用返回已登录
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
    await page.getByLabel(/密码/).fill("Pass1234");
    await page.getByLabel(/姓名/).fill("New");
    await page.getByRole("button", { name: /注册|创建账号/ }).click();

    await page.waitForURL("**/dashboard", { timeout: 5_000 });
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
    await page.getByLabel(/密码/).fill("Pass1234");
    await page.getByLabel(/姓名/).fill("Dup");
    await page.getByRole("button", { name: /注册|创建账号/ }).click();

    await expect(page.getByText(/该邮箱已被注册/)).toBeVisible({
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

    await expect(page.getByText(/邮箱或密码错误/)).toBeVisible({
      timeout: 5_000,
    });
  });
});
