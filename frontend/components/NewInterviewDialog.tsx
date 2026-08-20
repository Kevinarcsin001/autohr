"use client";

/**
 * 「新建面试」对话框：选职位 → 选该职位下候选人 → 创建会话 → 直达渐进式面试。
 *
 * 数据流：职位(active) → 候选人(团队池,可搜索) → POST /interview/sessions
 * → 跳转 /interviews/{id}/adaptive(自动 start)。
 */
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { listJobsApi } from "@/lib/api/jobs";
import { listTeamCandidatesApi } from "@/lib/api/candidates";
import { useCreateInterviewSession } from "@/hooks/useInterviewSessions";
import { useQuery } from "@tanstack/react-query";

interface Props {
  open: boolean;
  onClose: () => void;
}

export function NewInterviewDialog({ open, onClose }: Props) {
  const router = useRouter();
  const [jobId, setJobId] = useState("");
  const [candidateId, setCandidateId] = useState("");
  const [search, setSearch] = useState("");
  const create = useCreateInterviewSession();

  const { data: jobsData, isLoading: jobsLoading } = useQuery({
    queryKey: ["jobs", "for-interview"],
    queryFn: () => listJobsApi({ status: "active", page_size: 50 }),
    enabled: open,
    staleTime: 30_000,
  });

  const { data: candsData, isLoading: candsLoading } = useQuery({
    queryKey: ["candidates", "for-interview", search],
    queryFn: () =>
      listTeamCandidatesApi({ page_size: 50, ...(search ? { search } : {}) }),
    enabled: open,
    staleTime: 15_000,
  });

  // 重置
  useEffect(() => {
    if (open) {
      setJobId("");
      setCandidateId("");
      setSearch("");
    }
  }, [open]);

  const jobs = jobsData?.items ?? [];
  const candidates = useMemo(() => {
    const all = candsData?.items ?? [];
    return all; // 后端已按 search 过滤;此处直接用
  }, [candsData]);

  const selectedJob = jobs.find((j) => j.id === jobId);
  const selectedCandidate = candidates.find((c) => c.id === candidateId);

  const onSubmit = () => {
    if (!jobId || !candidateId) return;
    create.mutate(
      { candidate_id: candidateId, job_id: jobId },
      {
        onSuccess: (session) => {
          onClose();
          // 直达渐进式面试页(页面会引导 start)
          router.push(`/interviews/${session.id}/adaptive`);
        },
      },
    );
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="新建面试"
      description="选择职位与候选人，创建会话后直接进入渐进式自适应面试"
      maxWidth="max-w-2xl"
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={create.isPending}>
            取消
          </Button>
          <Button
            onClick={onSubmit}
            disabled={!jobId || !candidateId || create.isPending}
          >
            {create.isPending ? "创建中…" : "创建并进入面试"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {/* 第 1 步:职位 */}
        <div className="space-y-1.5">
          <p className="text-sm font-medium">① 选择职位</p>
          {jobsLoading ? (
            <p className="text-xs text-muted-foreground">加载职位中…</p>
          ) : jobs.length === 0 ? (
            <Alert variant="destructive">
              <AlertDescription>
                没有招聘中的职位，请先到「职位管理」创建并启用职位。
              </AlertDescription>
            </Alert>
          ) : (
            <div className="grid max-h-40 gap-1 overflow-y-auto rounded-md border p-2">
              {jobs.map((j) => (
                <button
                  key={j.id}
                  className={`rounded px-2 py-1.5 text-left text-sm hover:bg-accent ${
                    jobId === j.id ? "bg-accent font-medium" : ""
                  }`}
                  onClick={() => setJobId(j.id)}
                >
                  {j.title}
                  {j.status !== "active" && (
                    <span className="ml-2 text-xs text-muted-foreground">
                      ({j.status})
                    </span>
                  )}
                </button>
              ))}
            </div>
          )}
          {selectedJob && (
            <p className="text-xs text-muted-foreground">
              已选:{selectedJob.title}
            </p>
          )}
        </div>

        {/* 第 2 步:候选人 */}
        <div className="space-y-1.5">
          <p className="text-sm font-medium">② 选择候选人</p>
          <Input
            placeholder="搜索姓名 / 邮箱 / 技能…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="text-sm"
          />
          {candsLoading ? (
            <p className="text-xs text-muted-foreground">加载候选人中…</p>
          ) : candidates.length === 0 ? (
            <Alert>
              <AlertDescription>
                团队候选人池为空——先上传简历（上传页可关联到该职位）。
              </AlertDescription>
            </Alert>
          ) : (
            <div className="grid max-h-48 gap-1 overflow-y-auto rounded-md border p-2">
              {candidates.map((c) => (
                <button
                  key={c.id}
                  className={`rounded px-2 py-1.5 text-left text-sm hover:bg-accent ${
                    candidateId === c.id ? "bg-accent font-medium" : ""
                  }`}
                  onClick={() => setCandidateId(c.id)}
                >
                  <span className="font-medium">{c.name}</span>
                  {c.years_of_experience !== null && (
                    <span className="ml-2 text-xs text-muted-foreground">
                      {c.years_of_experience} 年
                    </span>
                  )}
                  {c.skills.slice(0, 5).map((s) => (
                    <span
                      key={s}
                      className="ml-1.5 rounded bg-muted px-1 text-[10px] text-muted-foreground"
                    >
                      {s}
                    </span>
                  ))}
                </button>
              ))}
            </div>
          )}
          {selectedCandidate && (
            <p className="text-xs text-muted-foreground">
              已选:{selectedCandidate.name}
            </p>
          )}
        </div>

        {create.error && (
          <Alert variant="destructive">
            <AlertDescription>
              {(create.error as { response?: { data?: { error?: { message?: string } } } })
                ?.response?.data?.error?.message ?? "创建失败"}
            </AlertDescription>
          </Alert>
        )}
      </div>
    </Dialog>
  );
}
