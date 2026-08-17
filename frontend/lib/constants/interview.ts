/**
 * 面试相关常量：维度标签 + 配色 + 状态标签。
 *
 * 题库分类徽章配色复用 DIMENSION_VARIANT 模式（见 components/InterviewQuestions.tsx、
 * app/(app)/interviews/[sessionId]/page.tsx）。
 */
import type { InterviewDimension, InterviewSessionStatus } from "@/lib/api/interview";

type BadgeVariant =
  | "default"
  | "secondary"
  | "success"
  | "warning"
  | "destructive"
  | "outline";

export const DIMENSION_LABEL: Record<InterviewDimension, string> = {
  skill: "技能深挖",
  project: "项目经历",
  weakness: "短板验证",
  culture: "文化匹配",
  communication: "沟通表达",
};

export const DIMENSION_VARIANT: Record<InterviewDimension, BadgeVariant> = {
  skill: "default",
  project: "secondary",
  weakness: "warning",
  culture: "outline",
  communication: "success",
};

export const SESSION_STATUS_LABEL: Record<InterviewSessionStatus, string> = {
  scheduled: "待面试",
  in_progress: "进行中",
  completed: "已完成",
};

export const DIMENSION_OPTIONS = Object.entries(DIMENSION_LABEL).map(
  ([value, label]) => ({ value, label }),
);
