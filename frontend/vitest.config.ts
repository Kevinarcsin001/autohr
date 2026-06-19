import { defineConfig } from "vitest/config";
import path from "node:path";

/**
 * Vitest 配置（任务 23）。
 *
 * - environment: jsdom（DOM API 可用）
 * - esbuild 自动处理 .tsx（automatic jsx runtime）
 * - alias @ → frontend root（与 tsconfig paths 一致）
 *
 * 不依赖 @vitejs/plugin-react：vitest 的 esbuild 已支持 React 17+ 的
 * automatic jsx runtime（tsconfig.json 已设 jsx: "preserve"，运行时由
 * react/jsx-runtime 接管）。
 */
export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./"),
    },
  },
  esbuild: {
    jsx: "automatic",
    jsxImportSource: "react",
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["**/*.{test,spec}.{ts,tsx}"],
    exclude: ["node_modules", ".next", "tests/e2e/**"],
    coverage: {
      reporter: ["text", "html"],
      include: ["lib/**", "hooks/**", "components/**"],
      exclude: ["**/*.test.*", "**/*.spec.*"],
    },
  },
});
