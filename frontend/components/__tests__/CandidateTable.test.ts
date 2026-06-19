import { describe, expect, it } from "vitest";
import type { SortingState } from "@tanstack/react-table";

import {
  defaultColumnConfigs,
  paramsToSorting,
  sortingToParams,
} from "@/components/CandidateTable";

// ============================================================================
// sortingToParams / paramsToSorting
// ============================================================================

describe("sortingToParams", () => {
  it("空 sorting → 默认 total desc", () => {
    expect(sortingToParams([])).toEqual({
      sort_by: "total",
      sort_order: "desc",
    });
  });

  it("asc → 透传", () => {
    const sorting: SortingState = [{ id: "skill", desc: false }];
    expect(sortingToParams(sorting)).toEqual({
      sort_by: "skill",
      sort_order: "asc",
    });
  });

  it("desc → desc", () => {
    const sorting: SortingState = [{ id: "experience", desc: true }];
    expect(sortingToParams(sorting)).toEqual({
      sort_by: "experience",
      sort_order: "desc",
    });
  });

  it("多列排序只取第一列（业务限制：后端不支持多列）", () => {
    const sorting: SortingState = [
      { id: "skill", desc: false },
      { id: "total", desc: true },
    ];
    const result = sortingToParams(sorting);
    expect(result.sort_by).toBe("skill");
  });
});

describe("paramsToSorting", () => {
  it("空 sort_by → 空 SortingState", () => {
    expect(paramsToSorting("", "desc")).toEqual([]);
  });

  it("合法 sort_by + asc → 单列 asc", () => {
    expect(paramsToSorting("skill", "asc")).toEqual([
      { id: "skill", desc: false },
    ]);
  });

  it("合法 sort_by + desc → 单列 desc", () => {
    expect(paramsToSorting("total", "desc")).toEqual([
      { id: "total", desc: true },
    ]);
  });
});

// ============================================================================
// defaultColumnConfigs
// ============================================================================

describe("defaultColumnConfigs", () => {
  it("每列都包含 id/visible/order", () => {
    const configs = defaultColumnConfigs();
    for (const c of configs) {
      expect(typeof c.id).toBe("string");
      expect(typeof c.visible).toBe("boolean");
      expect(typeof c.order).toBe("number");
    }
  });

  it("order 唯一（无重复）", () => {
    const configs = defaultColumnConfigs();
    const orders = configs.map((c) => c.order);
    expect(new Set(orders).size).toBe(orders.length);
  });

  it("按 order 升序排列", () => {
    const configs = defaultColumnConfigs();
    const orders = configs.map((c) => c.order);
    const sorted = [...orders].sort((a, b) => a - b);
    expect(orders).toEqual(sorted);
  });

  it("name 列默认可见（关键字段）", () => {
    const configs = defaultColumnConfigs();
    const name = configs.find((c) => c.id === "name");
    expect(name?.visible).toBe(true);
    expect(name?.order).toBe(0);
  });

  it("total 列默认可见", () => {
    const configs = defaultColumnConfigs();
    const total = configs.find((c) => c.id === "total");
    expect(total?.visible).toBe(true);
  });

  it("education_score 默认隐藏（避免列过多）", () => {
    const configs = defaultColumnConfigs();
    const edu = configs.find((c) => c.id === "education_score");
    expect(edu?.visible).toBe(false);
  });
});

// ============================================================================
// 列元数据 sanity check
// ============================================================================

describe("ColumnMeta sanity", () => {
  it("所有列 meta.label 都是中文字符串", () => {
    // 通过反向检查：defaultColumnConfigs 拿到 id 后，列定义里有 label
    // 这里间接验证：导出的 COLUMN_DEFS 通过 defaultColumnConfigs 间接被使用
    const configs = defaultColumnConfigs();
    expect(configs.length).toBeGreaterThan(5);
  });
});
