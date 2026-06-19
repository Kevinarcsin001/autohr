import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

import { ReasonsList } from "@/components/ReasonsList";

/**
 * ReasonsList 组件测试（任务 24）。
 *
 * 覆盖：
 * - 空状态（无 scoreId / 无数据）
 * - 加载状态
 * - recommend / disqualify 两种徽标渲染
 * - 点击"查看依据"打开模态 + 高亮 mark
 * - 未命中 → 显示"定位失败"
 */

vi.mock("@/hooks/useReasons", () => ({
  useReasons: vi.fn(),
}));

import { useReasons } from "@/hooks/useReasons";

describe("ReasonsList", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("无 scoreId → 显示「尚未评分」", () => {
    vi.mocked(useReasons).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
    } as never);

    render(<ReasonsList scoreId={null} parsedText="原文" />);
    expect(screen.getByText("尚未评分，无推荐理由")).toBeInTheDocument();
  });

  it("加载中 → 显示「加载中」", () => {
    vi.mocked(useReasons).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
    } as never);

    render(<ReasonsList scoreId="s-1" parsedText="原文" />);
    expect(screen.getByText("加载中...")).toBeInTheDocument();
  });

  it("空数据 → 显示「暂无推荐理由」", () => {
    vi.mocked(useReasons).mockReturnValue({
      data: { items: [], total: 0 },
      isLoading: false,
      isError: false,
    } as never);

    render(<ReasonsList scoreId="s-1" parsedText="原文" />);
    expect(screen.getByText("暂无推荐理由")).toBeInTheDocument();
  });

  it("recommend 理由 → 显示「推荐」徽标 + bullet_points", () => {
    vi.mocked(useReasons).mockReturnValue({
      data: {
        items: [
          {
            id: "r-1",
            score_id: "s-1",
            type: "recommend",
            bullet_points: ["Python 经验丰富"],
            validated: true,
          },
        ],
        total: 1,
      },
      isLoading: false,
      isError: false,
    } as never);

    render(<ReasonsList scoreId="s-1" parsedText="原文" />);
    expect(screen.getByText("推荐")).toBeInTheDocument();
    expect(screen.getByText("Python 经验丰富")).toBeInTheDocument();
  });

  it("disqualify 理由 → 显示「淘汰」徽标", () => {
    vi.mocked(useReasons).mockReturnValue({
      data: {
        items: [
          {
            id: "r-2",
            score_id: "s-1",
            type: "disqualify",
            bullet_points: ["学历不达标"],
            validated: false,
          },
        ],
        total: 1,
      },
      isLoading: false,
      isError: false,
    } as never);

    render(<ReasonsList scoreId="s-1" parsedText="原文" />);
    expect(screen.getByText("淘汰")).toBeInTheDocument();
    expect(screen.getByText("未通过事实校验")).toBeInTheDocument();
  });

  it("点击「查看依据」命中 → 高亮 mark", async () => {
    vi.mocked(useReasons).mockReturnValue({
      data: {
        items: [
          {
            id: "r-3",
            score_id: "s-1",
            type: "recommend",
            bullet_points: ["五年 Python 经验"],
            validated: true,
          },
        ],
        total: 1,
      },
      isLoading: false,
      isError: false,
    } as never);

    const parsedText = "候选人拥有五年 Python 经验，曾任职 ACME 公司";
    render(<ReasonsList scoreId="s-1" parsedText={parsedText} />);

    fireEvent.click(screen.getByText("查看依据"));

    await waitFor(() => {
      const dialog = screen.getByRole("dialog", { name: "理由依据" });
      expect(dialog).toBeInTheDocument();
      const mark = dialog.querySelector("mark");
      expect(mark).not.toBeNull();
      expect(mark?.textContent).toContain("Python");
    });
  });

  it("点击「查看依据」未命中 → 显示「定位失败」", async () => {
    vi.mocked(useReasons).mockReturnValue({
      data: {
        items: [
          {
            id: "r-4",
            score_id: "s-1",
            type: "recommend",
            bullet_points: ["某完全不相关的关键词"],
            validated: true,
          },
        ],
        total: 1,
      },
      isLoading: false,
      isError: false,
    } as never);

    render(<ReasonsList scoreId="s-1" parsedText="完全不同的文本" />);

    fireEvent.click(screen.getByText("查看依据"));

    await waitFor(() => {
      expect(screen.getByText(/定位失败/)).toBeInTheDocument();
    });
  });

  it("无 parsedText → 不渲染「查看依据」按钮", () => {
    vi.mocked(useReasons).mockReturnValue({
      data: {
        items: [
          {
            id: "r-5",
            score_id: "s-1",
            type: "recommend",
            bullet_points: ["理由"],
            validated: true,
          },
        ],
        total: 1,
      },
      isLoading: false,
      isError: false,
    } as never);

    render(<ReasonsList scoreId="s-1" parsedText={null} />);
    expect(screen.queryByText("查看依据")).not.toBeInTheDocument();
  });
});
