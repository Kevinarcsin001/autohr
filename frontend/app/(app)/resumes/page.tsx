"use client";

import Link from "next/link";
import { FileText } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useResumeBank } from "@/hooks/useResumeBank";
import { useAuthStore } from "@/stores/authStore";
import { formatDateTime } from "@/lib/utils";

const PARSE_LABELS: Record<string, { label: string; variant: "success" | "destructive" | "outline" | "warning" }> = {
  pending: { label: "待解析", variant: "outline" },
  success: { label: "已解析", variant: "success" },
  low_text: { label: "低文本", variant: "warning" },
  failed: { label: "失败", variant: "destructive" },
};

export default function ResumesPage() {
  const user = useAuthStore((s) => s.user);
  const { data, isLoading, isError } = useResumeBank();

  if (!user) {
    return (
      <div className="p-8">
        <p>未登录</p>
        <Link href="/login" className="text-primary underline">
          前往登录
        </Link>
      </div>
    );
  }

  const items = data ?? [];

  return (
    <div className="mx-auto max-w-6xl space-y-6 px-4 py-8 sm:px-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">简历库</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            所有已上传简历及处理状态
          </p>
        </div>
        <Link
          href="/uploads"
          className="inline-flex items-center justify-center whitespace-nowrap rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
        >
          上传简历
        </Link>
      </header>

      {isLoading && (
        <Card>
          <CardContent className="py-8 text-sm text-muted-foreground">
            加载中…
          </CardContent>
        </Card>
      )}

      {isError && (
        <Card>
          <CardContent className="py-8 text-sm text-red-600">
            无法加载简历列表。
          </CardContent>
        </Card>
      )}

      {!isLoading && !isError && items.length === 0 && (
        <EmptyState
          icon={FileText}
          title="暂无简历"
          description="上传简历后，所有文件将在此处展示处理状态。"
          action={{ label: "上传简历", href: "/uploads" }}
        />
      )}

      {items.length > 0 && (
        <div className="overflow-hidden rounded-lg border">
          <table className="w-full text-sm">
            <thead className="border-b bg-muted/50 text-left text-xs text-muted-foreground">
              <tr>
                <th className="px-4 py-3">候选人</th>
                <th className="px-4 py-3">文件名</th>
                <th className="px-4 py-3">解析状态</th>
                <th className="px-4 py-3">抽取状态</th>
                <th className="px-4 py-3">评分</th>
                <th className="px-4 py-3">上传时间</th>
                <th className="px-4 py-3">操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.resume_id} className="border-b last:border-0 hover:bg-accent/40">
                  <td className="px-4 py-2.5">
                    <div className="font-medium">{item.candidate_name || "—"}</div>
                    <div className="text-xs text-muted-foreground">{item.candidate_email || ""}</div>
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground">
                    {item.filename}
                  </td>
                  <td className="px-4 py-2.5">
                    <Badge variant={PARSE_LABELS[item.parse_status]?.variant ?? "outline"}>
                      {PARSE_LABELS[item.parse_status]?.label ?? item.parse_status}
                    </Badge>
                  </td>
                  <td className="px-4 py-2.5">
                    {item.extract_status ? (
                      <Badge variant={item.extract_status === "success" || item.extract_status === "extracted" ? "success" : "outline"}>
                        {item.extract_status === "success" || item.extract_status === "extracted" ? "已抽取" : item.extract_status}
                      </Badge>
                    ) : (
                      <span className="text-xs text-muted-foreground">—</span>
                    )}
                  </td>
                  <td className="px-4 py-2.5">
                    {item.score_total != null ? (
                      <span className="inline-block rounded-md bg-emerald-50 px-1.5 py-0.5 text-xs font-mono font-semibold text-emerald-700">
                        {item.score_total}
                      </span>
                    ) : (
                      <span className="text-xs text-muted-foreground">—</span>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-xs text-muted-foreground">
                    {formatDateTime(item.uploaded_at)}
                  </td>
                  <td className="px-4 py-2.5">
                    {item.candidate_id ? (
                      item.job_id ? (
                        <Link
                          href={`/jobs/${item.job_id}/candidates/${item.candidate_id}`}
                          className="rounded-md bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground hover:bg-primary/90"
                        >
                          查看
                        </Link>
                      ) : (
                        <Link
                          href={`/candidates/${item.candidate_id}`}
                          className="rounded-md bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground hover:bg-primary/90"
                        >
                          查看
                        </Link>
                      )
                    ) : (
                      <span className="text-xs text-muted-foreground">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
