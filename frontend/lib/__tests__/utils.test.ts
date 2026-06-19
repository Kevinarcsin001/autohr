import { describe, expect, it } from "vitest";

import {
  extractNeedleFromReason,
  highlightSubstring,
} from "@/lib/utils";

// ============================================================================
// highlightSubstring
// ============================================================================

describe("highlightSubstring", () => {
  it("命中：返回 prefix/match/suffix", () => {
    const text = "候选人拥有五年Python后端开发经验，熟悉FastAPI框架";
    const result = highlightSubstring(text, "Python");
    expect(result).not.toBeNull();
    expect(result?.match).toBe("Python");
    expect(result?.prefix).toContain("五年");
    expect(result?.suffix).toContain("后端");
  });

  it("未命中：返回 null", () => {
    expect(highlightSubstring("abcdef", "xyz")).toBeNull();
  });

  it("空 needle：返回 null", () => {
    expect(highlightSubstring("text", "")).toBeNull();
    expect(highlightSubstring("text", "   ")).toBeNull();
  });

  it("空 text：返回 null", () => {
    expect(highlightSubstring("", "needle")).toBeNull();
    expect(highlightSubstring(null, "needle")).toBeNull();
    expect(highlightSubstring(undefined, "needle")).toBeNull();
  });

  it("大小写不敏感匹配", () => {
    const result = highlightSubstring("Python FastAPI", "python");
    expect(result).not.toBeNull();
    expect(result?.match).toBe("Python");
  });

  it("多次出现：只取第一个", () => {
    const text = "Python Python Python";
    const result = highlightSubstring(text, "Python");
    expect(result?.start).toBe(0);
    expect(result?.end).toBe(6);
  });

  it("中文匹配：按字符位置切片", () => {
    const text = "候选人熟悉Python、FastAPI、Django等后端框架";
    const result = highlightSubstring(text, "Python");
    expect(result?.prefix).toBe("候选人熟悉");
    expect(result?.match).toBe("Python");
    expect(result?.suffix.startsWith("、")).toBe(true);
  });

  it("上下文窗口为 15 字符（前/后）", () => {
    const longPrefix = "a".repeat(30);
    const longSuffix = "b".repeat(30);
    const text = `${longPrefix}NEEDLE${longSuffix}`;
    const result = highlightSubstring(text, "NEEDLE");
    expect(result?.prefix.length).toBe(15);
    expect(result?.suffix.length).toBe(15);
    expect(result?.prefix).toBe("a".repeat(15));
    expect(result?.suffix).toBe("b".repeat(15));
  });

  it("命中位置在原文开头：prefix 为空", () => {
    const result = highlightSubstring("NEEDLE 在最前", "NEEDLE");
    expect(result?.prefix).toBe("");
    expect(result?.match).toBe("NEEDLE");
  });

  it("命中位置在原文末尾：suffix 为空", () => {
    const result = highlightSubstring("末尾是 NEEDLE", "NEEDLE");
    expect(result?.suffix).toBe("");
    expect(result?.match).toBe("NEEDLE");
  });
});

// ============================================================================
// extractNeedleFromReason
// ============================================================================

describe("extractNeedleFromReason", () => {
  it("短句：原样返回", () => {
    expect(extractNeedleFromReason("Python")).toBe("Python");
  });

  it("含逗号：取第一段", () => {
    expect(extractNeedleFromReason("Python 经验,熟悉 FastAPI")).toBe(
      "Python 经验",
    );
  });

  it("含中文逗号：取第一段", () => {
    expect(extractNeedleFromReason("Python 经验，熟悉 FastAPI")).toBe(
      "Python 经验",
    );
  });

  it("含句号：取第一段", () => {
    expect(extractNeedleFromReason("五年经验。熟悉框架")).toBe("五年经验");
  });

  it("长句无标点：截断到 maxLen", () => {
    const reason = "候选人拥有五年Python后端开发经验熟悉FastAPI";
    const result = extractNeedleFromReason(reason, 14);
    expect(result.length).toBe(14);
    expect(result).toBe("候选人拥有五年Python后");
  });

  it("空 / null 输入：返回空字符串", () => {
    expect(extractNeedleFromReason("")).toBe("");
    expect(extractNeedleFromReason(null)).toBe("");
    expect(extractNeedleFromReason(undefined)).toBe("");
  });

  it("首尾空白：trim", () => {
    expect(extractNeedleFromReason("  Python 经验  ")).toBe("Python 经验");
  });
});
