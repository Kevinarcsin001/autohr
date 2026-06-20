"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import {
  useDeleteJob,
  useJob,
  useJobVersions,
  useUpdateJob,
} from "@/hooks/useJobs";
import { JobForm, diffPayload, type JobFormValues } from "@/components/JobForm";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

const STATUS_LABEL: Record<string, string> = {
  draft: "草稿",
  active: "招聘中",
  closed: "已关闭",
};

const EDUCATION_LABEL: Record<string, string> = {
  high_school: "高中",
  bachelor: "本科",
  master: "硕士",
  phd: "博士",
};

export default function JobDetailPage() {
  const params = useParams<{ id: string }>();
  const jobId = params.id;
  const router = useRouter();
  const { data: job, isLoading, isError, error } = useJob(jobId);
  const { data: versions } = useJobVersions(jobId);
  const updateJob = useUpdateJob(jobId);
  const deleteJob = useDeleteJob();
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const originalValues = useMemo<Partial<JobFormValues> | undefined>(() => {
    if (!job) return undefined;
    return {
      title: job.title,
      jd_text: job.jd_text,
      status: job.status,
      hard_requirements: job.hard_requirements,
    };
  }, [job]);

  if (isLoading) {
    return <div className="p-8">加载中...</div>;
  }
  if (isError || !job) {
    return (
      <div className="p-8">
        <Alert variant="destructive">
          <AlertTitle>无法加载职位</AlertTitle>
          <AlertDescription>
            {error instanceof Error ? error.message : "请稍后重试"}
          </AlertDescription>
        </Alert>
        <Link href="/jobs" className="mt-4 inline-block text-primary underline">
          返回列表
        </Link>
      </div>
    );
  }

  async function handleSubmit(values: JobFormValues) {
    if (!originalValues) return;
    const payload = diffPayload(values, originalValues);
    if (Object.keys(payload).length === 0) {
      setEditing(false);
      return;
    }
    await updateJob.mutateAsync(payload);
    setEditing(false);
  }

  async function handleDelete() {
    await deleteJob.mutateAsync(jobId);
    router.push("/jobs");
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-8">
      <div>
        <Link
          href="/jobs"
          className="text-sm text-muted-foreground hover:underline"
        >
          ← 返回列表
        </Link>
        <div className="mt-2 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold">{job.title}</h1>
            <p className="text-sm text-muted-foreground">
              v{job.current_version} · 创建于{" "}
              {new Date(job.created_at).toLocaleString("zh-CN")}
            </p>
          </div>
          <div className="flex gap-2">
            <Button asChild variant="default">
              <Link href={`/jobs/${jobId}/candidates`}>查看候选人</Link>
            </Button>
            <Button
              variant="outline"
              onClick={() => setEditing((e) => !e)}
            >
              {editing ? "取消编辑" : "编辑"}
            </Button>
            <Button
              variant="destructive"
              onClick={() => setConfirmDelete(true)}
            >
              删除
            </Button>
          </div>
        </div>
      </div>

      {editing ? (
        <Card>
          <CardHeader>
            <CardTitle>编辑职位</CardTitle>
            <CardDescription>
              修改后保存将写入新版本快照（v{job.current_version + 1}）
            </CardDescription>
          </CardHeader>
          <CardContent>
            <JobForm
              initial={originalValues}
              submitLabel="保存修改"
              onSubmit={handleSubmit}
            />
          </CardContent>
        </Card>
      ) : (
        <>
          <Card>
            <CardHeader>
              <CardTitle>基本信息</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <div>
                <span className="font-medium">状态：</span>
                <span className="ml-2 text-muted-foreground">
                  {STATUS_LABEL[job.status] ?? job.status}
                </span>
              </div>
              <div>
                <span className="font-medium">LLM 配置：</span>
                <span className="ml-2 text-muted-foreground">
                  {job.llm_config
                    ? JSON.stringify(job.llm_config)
                    : "默认"}
                </span>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>职位描述（JD）</CardTitle>
            </CardHeader>
            <CardContent>
              <pre className="whitespace-pre-wrap break-words font-mono text-sm">
                {job.jd_text}
              </pre>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>硬性条件</CardTitle>
              <CardDescription>当前版本的结构化筛选条件</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <div>
                  <span className="font-medium">最低学历：</span>
                  <span className="ml-2 text-muted-foreground">
                    {job.hard_requirements.min_education
                      ? EDUCATION_LABEL[
                          job.hard_requirements.min_education
                        ] ?? job.hard_requirements.min_education
                      : "不限"}
                  </span>
                </div>
                <div>
                  <span className="font-medium">最低工作年限：</span>
                  <span className="ml-2 text-muted-foreground">
                    {job.hard_requirements.min_years != null
                      ? `${job.hard_requirements.min_years} 年`
                      : "不限"}
                  </span>
                </div>
              </div>
              <div>
                <div className="font-medium">必备技能：</div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {job.hard_requirements.required_skills?.length ? (
                    job.hard_requirements.required_skills.map((s) => (
                      <span
                        key={s}
                        className="rounded bg-blue-100 px-2 py-0.5 text-xs text-blue-800"
                      >
                        {s}
                      </span>
                    ))
                  ) : (
                    <span className="text-muted-foreground">不限</span>
                  )}
                </div>
              </div>
              <div>
                <div className="font-medium">排除公司：</div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {job.hard_requirements.excluded_companies?.length ? (
                    job.hard_requirements.excluded_companies.map((c) => (
                      <span
                        key={c}
                        className="rounded bg-red-100 px-2 py-0.5 text-xs text-red-800"
                      >
                        {c}
                      </span>
                    ))
                  ) : (
                    <span className="text-muted-foreground">无</span>
                  )}
                </div>
              </div>
            </CardContent>
          </Card>
        </>
      )}

      {/* 版本历史 */}
      <Card>
        <CardHeader>
          <CardTitle>版本历史</CardTitle>
          <CardDescription>
            每次编辑会写入快照（共 {versions?.length ?? 0} 个版本）
          </CardDescription>
        </CardHeader>
        <CardContent>
          {!versions || versions.length === 0 ? (
            <div className="text-sm text-muted-foreground">暂无历史</div>
          ) : (
            <ol className="space-y-3">
              {versions.map((v) => (
                <li
                  key={v.id}
                  className="rounded-md border p-3 text-sm"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-medium">v{v.version}</span>
                    <span className="text-xs text-muted-foreground">
                      {new Date(v.changed_at).toLocaleString("zh-CN")}
                    </span>
                  </div>
                  <div className="mt-1 text-muted-foreground">
                    标题：{v.snapshot.title} · 状态：
                    {STATUS_LABEL[v.snapshot.status] ?? v.snapshot.status}
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    {v.snapshot.hard_requirements.min_education
                      ? `学历≥${
                          EDUCATION_LABEL[
                            v.snapshot.hard_requirements.min_education
                          ]
                        }`
                      : "学历不限"}
                    {v.snapshot.hard_requirements.min_years != null
                      ? ` · 年限≥${v.snapshot.hard_requirements.min_years}`
                      : ""}
                    {v.snapshot.hard_requirements.required_skills?.length
                      ? ` · 必备 ${
                          v.snapshot.hard_requirements.required_skills
                            .length
                        } 项`
                      : ""}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </CardContent>
      </Card>

      {/* 删除二次确认 */}
      {confirmDelete && (
        <div className="fixed inset-0 flex items-center justify-center bg-black/50 p-4">
          <Card className="max-w-md">
            <CardHeader>
              <CardTitle>确认删除？</CardTitle>
              <CardDescription>
                删除「{job.title}」将一并删除所有版本快照、评分结果与候选人关联，操作不可恢复。
              </CardDescription>
            </CardHeader>
            <CardContent className="flex justify-end gap-2">
              <Button
                variant="outline"
                onClick={() => setConfirmDelete(false)}
              >
                取消
              </Button>
              <Button
                variant="destructive"
                onClick={handleDelete}
                disabled={deleteJob.isPending}
              >
                {deleteJob.isPending ? "删除中..." : "确认删除"}
              </Button>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
