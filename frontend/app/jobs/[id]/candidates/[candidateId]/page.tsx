"use client";

import { useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";

import { ActivityLog } from "@/components/ActivityLog";
import { InterviewQuestions } from "@/components/InterviewQuestions";
import { OverrideDialog } from "@/components/OverrideDialog";
import { ReasonsList } from "@/components/ReasonsList";
import { ResumePreview } from "@/components/ResumePreview";
import { ScoreBreakdown } from "@/components/ScoreBreakdown";
import { StructuredFields } from "@/components/StructuredFields";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs } from "@/components/ui/tabs";
import { useCandidateDetail } from "@/hooks/useCandidateDetail";
import { useOverrideCandidate } from "@/hooks/useOverrideCandidate";
import { formatDateTime } from "@/lib/utils";

/**
 * 候选人详情页（任务 24）。
 *
 * 布局：
 * - 顶部：返回列表 + 候选人基础信息 + 改判按钮
 * - 主区：左 1/2 简历预览（PDF/图），右 1/2 Tabs（结构化 / 评分 / 理由 / 面试）
 * - 底部：活动日志
 */

const TAB_OPTIONS = [
  { value: "structure", label: "结构化" },
  { value: "score", label: "评分" },
  { value: "reasons", label: "理由" },
  { value: "interview", label: "面试" },
];

export default function CandidateDetailPage() {
  const params = useParams<{ id: string; candidateId: string }>();
  const jobId = params.id;
  const candidateId = params.candidateId;
  const router = useRouter();

  const [activeTab, setActiveTab] = useState("structure");
  const [overrideOpen, setOverrideOpen] = useState(false);

  const {
    data: detail,
    isLoading,
    isError,
    error,
  } = useCandidateDetail(candidateId, jobId);

  const override = useOverrideCandidate(candidateId, jobId, {
    onSuccess: () => setOverrideOpen(false),
  });

  if (isLoading) {
    return (
      <div className="container mx-auto max-w-7xl p-6">
        <div className="text-sm text-muted-foreground">加载中...</div>
      </div>
    );
  }

  if (isError || !detail) {
    return (
      <div className="container mx-auto max-w-7xl p-6">
        <Alert variant="destructive">
          <AlertTitle>无法加载候选人详情</AlertTitle>
          <AlertDescription>
            {(error as Error)?.message ?? "请稍后重试"}
          </AlertDescription>
        </Alert>
        <div className="mt-4">
          <Button
            variant="outline"
            onClick={() => router.push(`/jobs/${jobId}/candidates`)}
          >
            返回列表
          </Button>
        </div>
      </div>
    );
  }

  const { candidate, screening_result, score, parsed_structure, resume } =
    detail;
  const scoreId = score?.id ?? null;
  const parsedText = resume?.parsed_text ?? null;
  const isDisqualified = screening_result?.disqualified ?? false;

  return (
    <div className="container mx-auto max-w-7xl space-y-4 p-6">
      {/* 顶部导航 + 候选人信息 */}
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-1">
          <Link
            href={`/jobs/${jobId}/candidates`}
            className="text-sm text-muted-foreground hover:text-foreground"
          >
            ← 返回候选人列表
          </Link>
          <h1 className="text-2xl font-semibold">{candidate.name}</h1>
          <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            {candidate.email && <span>{candidate.email}</span>}
            {candidate.phone && (
              <>
                <span>·</span>
                <span>{candidate.phone}</span>
              </>
            )}
            <span>·</span>
            <span>来源：{sourceLabel(candidate.source_type)}</span>
            <span>·</span>
            <span>入库：{formatDateTime(candidate.created_at)}</span>
          </div>
          <div className="flex items-center gap-2 pt-1">
            {screening_result ? (
              <Badge variant={isDisqualified ? "destructive" : "success"}>
                {isDisqualified ? "已淘汰" : "通过"}
              </Badge>
            ) : (
              <Badge variant="outline">待筛选</Badge>
            )}
            {screening_result?.manually_overridden && (
              <Badge variant="warning">HR 改判</Badge>
            )}
            {score && (
              <Badge variant="outline">综合分 {score.total}</Badge>
            )}
          </div>
        </div>

        <div className="flex gap-2">
          <Button
            variant="outline"
            onClick={() => router.push(`/jobs/${jobId}/candidates`)}
          >
            关闭
          </Button>
          {screening_result && (
            <Button onClick={() => setOverrideOpen(true)}>
              HR 改判
            </Button>
          )}
        </div>
      </header>

      {/* 主区：左右两栏 */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* 左：简历预览 */}
        <section className="space-y-2">
          <h2 className="text-sm font-medium text-muted-foreground">
            简历预览
          </h2>
          {resume ? (
            <ResumePreview
              candidateId={candidate.id}
              mimeType={resume.mime_type}
            />
          ) : (
            <Alert>
              <AlertDescription>
                候选人无简历文件
              </AlertDescription>
            </Alert>
          )}
        </section>

        {/* 右：Tabs */}
        <section className="space-y-3">
          <Tabs
            value={activeTab}
            onChange={setActiveTab}
            options={TAB_OPTIONS}
          />
          {activeTab === "structure" && (
            <StructuredFields structure={parsed_structure} />
          )}
          {activeTab === "score" && <ScoreBreakdown score={score} />}
          {activeTab === "reasons" && (
            <ReasonsList scoreId={scoreId} parsedText={parsedText} />
          )}
          {activeTab === "interview" && (
            <InterviewQuestions
              candidateId={candidate.id}
              jobId={jobId}
            />
          )}
        </section>
      </div>

      {/* 底部：活动日志 */}
      <ActivityLog candidateId={candidate.id} />

      {/* 改判弹窗 */}
      <OverrideDialog
        open={overrideOpen}
        defaultDisqualified={isDisqualified}
        onClose={() => setOverrideOpen(false)}
        onSubmit={(payload) => {
          if (!screening_result) return;
          override.mutate({
            screeningResultId: screening_result.id,
            payload,
          });
        }}
        submitting={override.isPending}
        error={
          override.isError
            ? (override.error as Error)?.message ?? "改判失败"
            : null
        }
      />
    </div>
  );
}

function sourceLabel(sourceType: string | null): string {
  if (!sourceType) return "—";
  const map: Record<string, string> = {
    upload: "上传",
    platform: "平台导入",
    email: "邮件",
  };
  return map[sourceType] ?? sourceType;
}
