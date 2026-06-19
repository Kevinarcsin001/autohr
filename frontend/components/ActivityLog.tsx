"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useCandidateActivity } from "@/hooks/useCandidateActivity";
import type { CandidateActivityItem } from "@/lib/api/candidateDetail";
import { cn, formatDateTime } from "@/lib/utils";

/**
 * 活动时间线（任务 24）：audit_logs + manual_overrides UNION 倒序。
 *
 * 渲染：
 * - 时间线左竖线 + 类型徽标（audit 蓝 / override 黄）
 * - summary（中文友好）+ 时间 + actor
 * - 加载更多（page_size=20 → 分页）
 */

interface ActivityLogProps {
  candidateId: string;
  className?: string;
  pageSize?: number;
}

export function ActivityLog({
  candidateId,
  className,
  pageSize = 20,
}: ActivityLogProps) {
  // 简化：始终取前 20 条；如果业务需要分页可改成 useState
  const { data, isLoading, isError, refetch, isFetching } =
    useCandidateActivity(candidateId, 1, pageSize);

  return (
    <Card className={className}>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-base">
          活动日志
          {data && (
            <span className="ml-2 text-sm font-normal text-muted-foreground">
              共 {data.total} 条
            </span>
          )}
        </CardTitle>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => refetch()}
          disabled={isFetching}
        >
          {isFetching ? "刷新中..." : "刷新"}
        </Button>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <p className="text-sm text-muted-foreground">加载中...</p>
        ) : isError ? (
          <p className="text-sm text-destructive">加载失败</p>
        ) : !data || data.items.length === 0 ? (
          <p className="text-sm text-muted-foreground">暂无活动</p>
        ) : (
          <ol className="relative space-y-3 border-l border-muted pl-4">
            {data.items.map((item) => (
              <ActivityEntry key={`${item.type}-${item.id}`} item={item} />
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}

function ActivityEntry({ item }: { item: CandidateActivityItem }) {
  const isOverride = item.type === "override";
  return (
    <li className="relative">
      {/* 时间线圆点 */}
      <span
        className={cn(
          "absolute -left-[21px] top-1.5 size-2.5 rounded-full",
          isOverride ? "bg-amber-500" : "bg-blue-500",
        )}
      />
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={isOverride ? "warning" : "secondary"}>
          {isOverride ? "改判" : "操作"}
        </Badge>
        <span className="text-sm font-medium">{item.summary}</span>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span>{formatDateTime(item.created_at)}</span>
        {item.actor_id && (
          <>
            <span>·</span>
            <span>操作人 {item.actor_id.slice(0, 8)}</span>
          </>
        )}
      </div>
      {item.details && (
        <details className="mt-1 text-xs text-muted-foreground">
          <summary className="cursor-pointer select-none">详情</summary>
          <pre className="mt-1 overflow-x-auto rounded bg-muted p-2">
            {JSON.stringify(item.details, null, 2)}
          </pre>
        </details>
      )}
    </li>
  );
}
