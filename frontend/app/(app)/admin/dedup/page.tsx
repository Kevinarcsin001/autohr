"use client";

import Link from "next/link";
import { useState } from "react";
import { GitMerge, ShieldAlert } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useDedupMatches } from "@/hooks/useDedupMatches";
import { useAuthStore } from "@/stores/authStore";

export default function DedupPage() {
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === "admin";
  const { data, isLoading, isError, refetch } = useDedupMatches();
  const [deciding, setDeciding] = useState<string | null>(null);

  if (!isAdmin) {
    return (
      <div className="p-8">
        <Alert variant="destructive">
          <AlertTitle>权限不足</AlertTitle>
          <AlertDescription>仅团队管理员可管理去重。</AlertDescription>
        </Alert>
      </div>
    );
  }

  const handleDecide = async (id: string, decision: string) => {
    try {
      setDeciding(id);
      const { default: axios } = await import("axios");
      await axios.patch(
        `${process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000"}/api/candidates/dedup-matches/${id}`,
        { decision },
        { withCredentials: true },
      );
      refetch();
    } finally {
      setDeciding(null);
    }
  };

  const items = data?.items ?? [];

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-4 py-8 sm:px-6">
      <header>
        <h1 className="text-2xl font-bold">去重管理</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          审核疑似重复候选人，手动合并或驳回
        </p>
      </header>

      {isLoading && (
        <Card>
          <CardContent className="py-8 text-sm text-muted-foreground">
            加载中…
          </CardContent>
        </Card>
      )}

      {isError && (
        <Alert variant="destructive">
          <AlertTitle>加载失败</AlertTitle>
          <AlertDescription>无法加载去重列表。</AlertDescription>
        </Alert>
      )}

      {!isLoading && !isError && items.length === 0 && (
        <EmptyState
          icon={GitMerge}
          title="无重复候选人"
          description="系统会自动检测疑似重复的候选人，需要审核时在此展示。"
        />
      )}

      {items.length > 0 && (
        <div className="space-y-3">
          {items.map((match) => (
            <Card key={match.id}>
              <CardContent className="flex items-center justify-between py-4">
                <div className="flex-1 space-y-1">
                  <div className="flex items-center gap-3">
                    <span className="font-medium">{match.name_a || "候选人 A"}</span>
                    <GitMerge className="h-4 w-4 text-muted-foreground" />
                    <span className="font-medium">{match.name_b || "候选人 B"}</span>
                    <Badge
                      variant={
                        match.status === "pending"
                          ? "outline"
                          : match.status === "merged"
                            ? "success"
                            : "destructive"
                      }
                    >
                      {match.status === "pending"
                        ? "待审核"
                        : match.status === "merged"
                          ? "已合并"
                          : "已驳回"}
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    相似度: {JSON.stringify(match.similarity)}
                  </p>
                </div>
                {match.status === "pending" && (
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="default"
                      disabled={deciding === match.id}
                      onClick={() => handleDecide(match.id, "merged")}
                    >
                      合并
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={deciding === match.id}
                      onClick={() => handleDecide(match.id, "rejected")}
                    >
                      驳回
                    </Button>
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <div>
        <Link href="/admin" className="text-sm text-primary hover:underline">
          ← 返回管理首页
        </Link>
      </div>
    </div>
  );
}
