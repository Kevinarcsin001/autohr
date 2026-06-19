import { beforeEach, describe, expect, it } from "vitest";

import {
  type ColumnConfig,
  type Density,
  type SavedView,
  deleteSavedView,
  generateViewId,
  isViewCompatible,
  listSavedViews,
  snapshotView,
  upsertSavedView,
} from "@/lib/savedViews";
import { defaultFilterForm } from "@/components/CandidateFilters";

// ============================================================================
// mocks
// ============================================================================

const localStorageStore = new Map<string, string>();

const localStorageMock = {
  getItem: (key: string) => localStorageStore.get(key) ?? null,
  setItem: (key: string, value: string) => {
    localStorageStore.set(key, value);
  },
  removeItem: (key: string) => {
    localStorageStore.delete(key);
  },
  clear: () => localStorageStore.clear(),
};

Object.defineProperty(window, "localStorage", {
  value: localStorageMock,
  configurable: true,
});

// ============================================================================
// fixtures
// ============================================================================

const JOB_ID = "job-123";

function makeColumns(overrides: ColumnConfig[] = []): ColumnConfig[] {
  return [
    { id: "name", visible: true, order: 0 },
    { id: "total", visible: true, order: 1 },
    { id: "skill", visible: false, order: 2 },
    ...overrides,
  ];
}

function makeView(overrides: Partial<SavedView> = {}): SavedView {
  return {
    id: "view-1",
    name: "高潜工程师",
    created_at: "2026-06-01T10:00:00Z",
    filters: defaultFilterForm(),
    group: "passed",
    columns: makeColumns(),
    density: "default" as Density,
    page_size: 50,
    ...overrides,
  };
}

// ============================================================================
// tests
// ============================================================================

describe("savedViews", () => {
  beforeEach(() => {
    localStorageStore.clear();
  });

  describe("listSavedViews", () => {
    it("空 storage 返回空数组", () => {
      expect(listSavedViews(JOB_ID)).toEqual([]);
    });

    it("返回按 created_at 倒序", () => {
      const old = makeView({
        id: "old",
        created_at: "2026-01-01T00:00:00Z",
      });
      const newer = makeView({
        id: "new",
        created_at: "2026-06-01T00:00:00Z",
      });
      upsertSavedView(JOB_ID, old);
      upsertSavedView(JOB_ID, newer);

      const result = listSavedViews(JOB_ID);
      expect(result.map((v) => v.id)).toEqual(["new", "old"]);
    });

    it("不同 jobId 隔离存储", () => {
      upsertSavedView(JOB_ID, makeView({ id: "a" }));
      upsertSavedView("job-other", makeView({ id: "b" }));

      expect(listSavedViews(JOB_ID).map((v) => v.id)).toEqual(["a"]);
      expect(listSavedViews("job-other").map((v) => v.id)).toEqual(["b"]);
    });

    it("storage 内容损坏（非 JSON）→ 返回空数组", () => {
      localStorageStore.set(
        `autohr:views:${JOB_ID}`,
        "not-json{{",
      );
      expect(listSavedViews(JOB_ID)).toEqual([]);
    });

    it("storage 内容缺字段（views 不是数组）→ 返回空数组", () => {
      localStorageStore.set(
        `autohr:views:${JOB_ID}`,
        JSON.stringify({ views: "not-an-array" }),
      );
      expect(listSavedViews(JOB_ID)).toEqual([]);
    });
  });

  describe("upsertSavedView", () => {
    it("新视图 → 追加", () => {
      upsertSavedView(JOB_ID, makeView({ id: "v1" }));
      upsertSavedView(JOB_ID, makeView({ id: "v2" }));
      const ids = listSavedViews(JOB_ID).map((v) => v.id);
      expect(ids).toContain("v1");
      expect(ids).toContain("v2");
    });

    it("已存在 id → 覆盖（不重复）", () => {
      upsertSavedView(JOB_ID, makeView({ id: "v1", name: "old" }));
      upsertSavedView(
        JOB_ID,
        makeView({ id: "v1", name: "new-name" }),
      );
      const views = listSavedViews(JOB_ID);
      expect(views).toHaveLength(1);
      expect(views[0].name).toBe("new-name");
    });
  });

  describe("deleteSavedView", () => {
    it("按 id 删除；不存在时也不报错", () => {
      upsertSavedView(JOB_ID, makeView({ id: "v1" }));
      upsertSavedView(JOB_ID, makeView({ id: "v2" }));
      deleteSavedView(JOB_ID, "v1");
      const ids = listSavedViews(JOB_ID).map((v) => v.id);
      expect(ids).toEqual(["v2"]);

      expect(() => deleteSavedView(JOB_ID, "non-exist")).not.toThrow();
    });
  });

  describe("generateViewId", () => {
    it("每次生成不同的 id", () => {
      const ids = new Set<string>();
      for (let i = 0; i < 100; i++) {
        ids.add(generateViewId());
      }
      expect(ids.size).toBe(100);
    });

    it("id 以 v_ 前缀开头（便于辨识）", () => {
      expect(generateViewId()).toMatch(/^v_/);
    });
  });

  describe("snapshotView", () => {
    it("深拷贝 filters/columns（避免后续修改污染快照）", () => {
      const filters = defaultFilterForm();
      const columns = makeColumns();
      const view = snapshotView("test", {
        filters,
        group: "all",
        columns,
        density: "compact",
        page_size: 20,
      });

      // 修改原对象
      filters.skill = "Python";
      columns[0].visible = false;

      expect(view.filters.skill).toBe("");
      expect(view.columns[0].visible).toBe(true);
    });

    it("携带所有字段", () => {
      const view = snapshotView("my-view", {
        filters: defaultFilterForm(),
        group: "passed",
        columns: makeColumns(),
        density: "comfortable",
        page_size: 100,
      });
      expect(view.name).toBe("my-view");
      expect(view.group).toBe("passed");
      expect(view.density).toBe("comfortable");
      expect(view.page_size).toBe(100);
      expect(view.id).toBeTruthy();
      expect(view.created_at).toBeTruthy();
    });
  });

  describe("isViewCompatible", () => {
    it("合法 SavedView → true", () => {
      expect(isViewCompatible(makeView())).toBe(true);
    });

    it("缺字段 → false", () => {
      expect(isViewCompatible({ id: "x", name: "x" })).toBe(false);
    });

    it("null / 非对象 → false", () => {
      expect(isViewCompatible(null)).toBe(false);
      expect(isViewCompatible("not-object")).toBe(false);
      expect(isViewCompatible(42)).toBe(false);
    });

    it("columns 不是数组 → false", () => {
      expect(
        isViewCompatible({ ...makeView(), columns: "not-array" }),
      ).toBe(false);
    });
  });

  describe("SSR 安全", () => {
    it("window 未定义时返回空数组（不抛错）", () => {
      const originalWindow = globalThis.window;
      // @ts-expect-error 故意移除 window 模拟 SSR
      delete globalThis.window;
      try {
        expect(listSavedViews(JOB_ID)).toEqual([]);
      } finally {
        globalThis.window = originalWindow;
      }
    });
  });
});
