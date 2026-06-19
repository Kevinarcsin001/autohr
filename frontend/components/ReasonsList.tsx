"use client";

import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useReasons } from "@/hooks/useReasons";
import {
  cn,
  extractNeedleFromReason,
  highlightSubstring,
} from "@/lib/utils";

/**
 * 推荐理由列表（任务 24）。
 *
 * 行为（plan）：
 * - 列出 recommend / disqualify 理由
 * - 每条理由附"查看依据"按钮 → 模态高亮 parsed_text 中的对应片段
 * - 未命中 → 优雅降级"定位失败，仅显示理由文本"
 */

interface ReasonsListProps {
  scoreId: string | null | undefined;
  parsedText: string | null;
  className?: string;
}

export function ReasonsList({
  scoreId,
  parsedText,
  className,
}: ReasonsListProps) {
  const { data, isLoading, isError } = useReasons(scoreId);
  const [highlight, setHighlight] = useState<{
    reason: string;
    match: ReturnType<typeof highlightSubstring>;
  } | null>(null);

  if (!scoreId) {
    return (
      <Card className={className}>
        <CardHeader>
          <CardTitle className="text-base">推荐理由</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            尚未评分，无推荐理由
          </p>
        </CardContent>
      </Card>
    );
  }

  if (isLoading) {
    return (
      <Card className={className}>
        <CardHeader>
          <CardTitle className="text-base">推荐理由</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">加载中...</p>
        </CardContent>
      </Card>
    );
  }

  if (isError || !data || data.items.length === 0) {
    return (
      <Card className={className}>
        <CardHeader>
          <CardTitle className="text-base">推荐理由</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            暂无推荐理由
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="text-base">推荐理由</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {data.items.map((reason) => {
          const isRecommend = reason.type === "recommend";
          return (
            <div
              key={reason.id}
              className={cn(
                "rounded-md border p-3",
                isRecommend
                  ? "border-emerald-200 bg-emerald-50/50"
                  : "border-red-200 bg-red-50/50",
              )}
            >
              <div className="mb-2 flex items-center gap-2">
                <Badge variant={isRecommend ? "success" : "destructive"}>
                  {isRecommend ? "推荐" : "淘汰"}
                </Badge>
                {reason.validated === false && (
                  <Badge variant="warning">未通过事实校验</Badge>
                )}
              </div>
              <ul className="space-y-1.5 text-sm">
                {reason.bullet_points?.map((bp, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-current opacity-50" />
                    <span className="flex-1">{bp}</span>
                    {parsedText && (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          const needle = extractNeedleFromReason(bp);
                          const match = highlightSubstring(
                            parsedText,
                            needle,
                          );
                          setHighlight({ reason: bp, match });
                        }}
                      >
                        查看依据
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </CardContent>

      {highlight && (
        <HighlightModal
          reason={highlight.reason}
          match={highlight.match}
          onClose={() => setHighlight(null)}
        />
      )}
    </Card>
  );
}

// ============================================================================
// 高亮模态（fixed overlay 模式，复用任务 7 改判弹窗风格）
// ============================================================================

function HighlightModal({
  reason,
  match,
  onClose,
}: {
  reason: string;
  match: ReturnType<typeof highlightSubstring>;
  onClose: () => void;
}) {
  return (
    <div
      role="dialog"
      aria-label="理由依据"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
    >
      <div
        className="max-h-[80vh] w-full max-w-2xl overflow-hidden rounded-lg bg-background shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="border-b p-4">
          <h3 className="text-base font-medium">理由依据</h3>
          <p className="mt-1 text-sm text-muted-foreground">{reason}</p>
        </div>
        <div className="max-h-[60vh] overflow-y-auto p-4">
          {match ? (
            <p className="text-sm leading-relaxed">
              {match.prefix && (
                <span className="text-muted-foreground">
                  ...{match.prefix}
                </span>
              )}
              <mark className="rounded bg-yellow-200 px-1 py-0.5 text-foreground">
                {match.match}
              </mark>
              {match.suffix && (
                <span className="text-muted-foreground">
                  {match.suffix}...
                </span>
              )}
            </p>
          ) : (
            <Alert>
              <AlertDescription>
                定位失败，仅显示理由文本。原文中未找到对应片段。
              </AlertDescription>
            </Alert>
          )}
        </div>
        <div className="flex justify-end border-t p-3">
          <Button variant="outline" onClick={onClose}>
            关闭
          </Button>
        </div>
      </div>
    </div>
  );
}
