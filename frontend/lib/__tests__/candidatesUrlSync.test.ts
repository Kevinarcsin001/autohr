import { describe, expect, it } from "vitest";

import {
  candidatesUrlStateToSearchParams,
  parseCandidatesUrl,
  urlStateToCandidateParams,
  ALLOWED_GROUPS,
  ALLOWED_PAGE_SIZES,
  DEFAULT_GROUP,
  DEFAULT_PAGE,
  DEFAULT_PAGE_SIZE,
  DEFAULT_SORT_BY,
  DEFAULT_SORT_ORDER,
} from "@/lib/candidatesUrlSync";

// ============================================================================
// parseCandidatesUrl
// ============================================================================

describe("parseCandidatesUrl", () => {
  it("空 URL → 全部默认值", () => {
    const sp = new URLSearchParams("");
    const state = parseCandidatesUrl(sp);
    expect(state.group).toBe(DEFAULT_GROUP);
    expect(state.page).toBe(DEFAULT_PAGE);
    expect(state.page_size).toBe(DEFAULT_PAGE_SIZE);
    expect(state.sort_by).toBe(DEFAULT_SORT_BY);
    expect(state.sort_order).toBe(DEFAULT_SORT_ORDER);
    expect(state.skill).toBe("");
    expect(state.education).toBe("");
    expect(state.min_score).toBeUndefined();
  });

  it("合法参数全部解析", () => {
    const sp = new URLSearchParams(
      "?group=passed&page=3&page_size=20&sort_by=skill&sort_order=asc" +
        "&skill=Python&education=master&source=email" +
        "&min_score=70&max_score=90&min_years=3&max_years=10",
    );
    const state = parseCandidatesUrl(sp);
    expect(state).toEqual({
      group: "passed",
      page: 3,
      page_size: 20,
      sort_by: "skill",
      sort_order: "asc",
      skill: "Python",
      education: "master",
      source: "email",
      min_score: 70,
      max_score: 90,
      min_years: 3,
      max_years: 10,
    });
  });

  it("非法 group → 默认 all", () => {
    const sp = new URLSearchParams("?group=invalid");
    expect(parseCandidatesUrl(sp).group).toBe(DEFAULT_GROUP);
  });

  it("page = 0 → clamp 到 1", () => {
    const sp = new URLSearchParams("?page=0");
    expect(parseCandidatesUrl(sp).page).toBe(1);
  });

  it("page = -5 → clamp 到 1", () => {
    const sp = new URLSearchParams("?page=-5");
    expect(parseCandidatesUrl(sp).page).toBe(1);
  });

  it("page 非数字 → 默认 1", () => {
    const sp = new URLSearchParams("?page=abc");
    expect(parseCandidatesUrl(sp).page).toBe(DEFAULT_PAGE);
  });

  it.each([
    "5", // 不在允许列表
    "15",
    "25",
    "abc",
  ])("page_size = %s → 默认 %s", (val) => {
    const sp = new URLSearchParams(`?page_size=${val}`);
    expect(parseCandidatesUrl(sp).page_size).toBe(DEFAULT_PAGE_SIZE);
  });

  it.each(ALLOWED_PAGE_SIZES)("page_size = %s → 接受", (val) => {
    const sp = new URLSearchParams(`?page_size=${val}`);
    expect(parseCandidatesUrl(sp).page_size).toBe(val);
  });

  it("sort_order 不是 asc/desc → 默认 desc", () => {
    const sp = new URLSearchParams("?sort_order=invalid");
    expect(parseCandidatesUrl(sp).sort_order).toBe("desc");
  });

  it("sort_order = asc → 接受", () => {
    const sp = new URLSearchParams("?sort_order=asc");
    expect(parseCandidatesUrl(sp).sort_order).toBe("asc");
  });

  it("sort_by 缺失 → 默认 total", () => {
    const sp = new URLSearchParams("");
    expect(parseCandidatesUrl(sp).sort_by).toBe(DEFAULT_SORT_BY);
  });

  it("min_score 非数字 → undefined", () => {
    const sp = new URLSearchParams("?min_score=abc");
    expect(parseCandidatesUrl(sp).min_score).toBeUndefined();
  });

  it("空字符串值 → 转空串（不 undefined）", () => {
    const sp = new URLSearchParams("?skill=&education=");
    const state = parseCandidatesUrl(sp);
    expect(state.skill).toBe("");
    expect(state.education).toBe("");
  });
});

// ============================================================================
// urlStateToCandidateParams
// ============================================================================

describe("urlStateToCandidateParams", () => {
  it("空筛选 → skill/education/source 都是 undefined（axios 不发送）", () => {
    const sp = new URLSearchParams("");
    const state = parseCandidatesUrl(sp);
    const params = urlStateToCandidateParams(state);
    expect(params.skill).toBeUndefined();
    expect(params.education).toBeUndefined();
    expect(params.source).toBeUndefined();
  });

  it("数字字段保留 number 类型（不转 string）", () => {
    const sp = new URLSearchParams("?min_score=70&max_score=90");
    const params = urlStateToCandidateParams(parseCandidatesUrl(sp));
    expect(params.min_score).toBe(70);
    expect(params.max_score).toBe(90);
    expect(typeof params.min_score).toBe("number");
  });

  it("group 转发 union 类型（不丢失）", () => {
    for (const g of ALLOWED_GROUPS) {
      const sp = new URLSearchParams(`?group=${g}`);
      const params = urlStateToCandidateParams(parseCandidatesUrl(sp));
      expect(params.group).toBe(g);
    }
  });
});

// ============================================================================
// candidatesUrlStateToSearchParams
// ============================================================================

describe("candidatesUrlStateToSearchParams", () => {
  it("空字段被剔除", () => {
    const sp = candidatesUrlStateToSearchParams({
      skill: "",
      education: "",
      source: "",
      group: "all",
    });
    expect(sp.toString()).not.toContain("skill");
    expect(sp.toString()).not.toContain("education");
    expect(sp.toString()).not.toContain("source");
    expect(sp.get("group")).toBe("all");
  });

  it("undefined/null 被剔除", () => {
    const sp = candidatesUrlStateToSearchParams({
      min_score: undefined,
      max_score: undefined,
      skill: "Python",
    });
    expect(sp.toString()).not.toContain("min_score");
    expect(sp.toString()).not.toContain("max_score");
    expect(sp.get("skill")).toBe("Python");
  });

  it("数字字段转字符串", () => {
    const sp = candidatesUrlStateToSearchParams({
      min_score: 70,
      page: 3,
    });
    expect(sp.get("min_score")).toBe("70");
    expect(sp.get("page")).toBe("3");
  });

  it("布尔字段不存在（业务无布尔字段）", () => {
    // 仅做 smoke：未来若新增 boolean 字段需测试 true → "true"
    const sp = candidatesUrlStateToSearchParams({ group: "passed" });
    expect(sp.get("group")).toBe("passed");
  });
});
