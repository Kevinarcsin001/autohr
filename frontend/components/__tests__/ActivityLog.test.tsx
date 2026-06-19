import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

import { ActivityLog } from "@/components/ActivityLog";

/**
 * ActivityLog 组件测试（任务 24）。
 *
 * 覆盖：
 * - 加载中 / 空 / 错误 三态
 * - audit + override 混合渲染
 * - 类型徽标（操作 / 改判）
 * - 时间 + actor 显示
 */

vi.mock("@/hooks/useCandidateActivity", () => ({
  useCandidateActivity: vi.fn(),
}));

import { useCandidateActivity } from "@/hooks/useCandidateActivity";

describe("ActivityLog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("加载中 → 显示「加载中」", () => {
    vi.mocked(useCandidateActivity).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      isFetching: false,
      refetch: vi.fn(),
    } as never);

    render(<ActivityLog candidateId="c-1" />);
    expect(screen.getByText("加载中...")).toBeInTheDocument();
  });

  it("空数据 → 显示「暂无活动」", () => {
    vi.mocked(useCandidateActivity).mockReturnValue({
      data: { items: [], total: 0, page: 1, page_size: 20 },
      isLoading: false,
      isError: false,
      isFetching: false,
      refetch: vi.fn(),
    } as never);

    render(<ActivityLog candidateId="c-1" />);
    expect(screen.getByText("暂无活动")).toBeInTheDocument();
  });

  it("错误 → 显示「加载失败」", () => {
    vi.mocked(useCandidateActivity).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      isFetching: false,
      refetch: vi.fn(),
    } as never);

    render(<ActivityLog candidateId="c-1" />);
    expect(screen.getByText("加载失败")).toBeInTheDocument();
  });

  it("渲染 audit + override 混合条目", () => {
    vi.mocked(useCandidateActivity).mockReturnValue({
      data: {
        items: [
          {
            type: "audit_log",
            id: "a-1",
            created_at: "2026-06-01T10:00:00Z",
            actor_id: "u-1",
            action: "candidate.update",
            summary: "候选人信息更新",
            details: { before: { x: 1 } },
          },
          {
            type: "override",
            id: "o-1",
            created_at: "2026-06-01T11:00:00Z",
            actor_id: "u-2",
            action: "screening.override",
            summary: "HR 改判为通过",
            details: { new_value: { disqualified: false } },
          },
        ],
        total: 2,
        page: 1,
        page_size: 20,
      },
      isLoading: false,
      isError: false,
      isFetching: false,
      refetch: vi.fn(),
    } as never);

    render(<ActivityLog candidateId="c-1" />);

    // 类型徽标
    expect(screen.getByText("操作")).toBeInTheDocument();
    expect(screen.getByText("改判")).toBeInTheDocument();

    // summary
    expect(screen.getByText("候选人信息更新")).toBeInTheDocument();
    expect(screen.getByText("HR 改判为通过")).toBeInTheDocument();

    // 总数
    expect(screen.getByText(/共 2 条/)).toBeInTheDocument();
  });

  it("actor_id 截取前 8 字符", () => {
    vi.mocked(useCandidateActivity).mockReturnValue({
      data: {
        items: [
          {
            type: "audit_log",
            id: "a-2",
            created_at: "2026-06-01T10:00:00Z",
            actor_id: "abcdef12-3456-7890-abcd-ef1234567890",
            action: "x",
            summary: "测试",
            details: null,
          },
        ],
        total: 1,
        page: 1,
        page_size: 20,
      },
      isLoading: false,
      isError: false,
      isFetching: false,
      refetch: vi.fn(),
    } as never);

    render(<ActivityLog candidateId="c-1" />);
    expect(screen.getByText(/操作人 abcdef12/)).toBeInTheDocument();
  });
});
