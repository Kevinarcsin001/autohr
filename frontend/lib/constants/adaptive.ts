/**
 * 自适应面试常量：分支状态与决策动作的标签/配色。
 */
import type { BadgeVariant } from "@/lib/constants/interview";

export const ADAPTIVE_STATUS_LABEL: Record<string, string> = {
  pending: "待问",
  active: "进行中",
  done: "已完成",
  weak: "薄弱",
  exhausted: "已用尽",
};

export const ADAPTIVE_STATUS_VARIANT: Record<string, BadgeVariant> = {
  pending: "outline",
  active: "default",
  done: "secondary",
  weak: "destructive",
  exhausted: "outline",
};

export const DECISION_LABEL: Record<string, string> = {
  deepen: "深度·同分支深挖",
  followup: "深度·内容追问",
  retry: "换考点再验证",
  switch: "广度·切换分支",
  complete: "面试完成",
};
