"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCreateJob } from "@/hooks/useJobs";
import { JobForm, type JobFormValues } from "@/components/JobForm";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export default function NewJobPage() {
  const router = useRouter();
  const createJob = useCreateJob();

  async function handleSubmit(values: JobFormValues) {
    const job = await createJob.mutateAsync(values);
    router.push(`/jobs/${job.id}`);
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-8">
      <div>
        <Link
          href="/jobs"
          className="text-sm text-muted-foreground hover:underline"
        >
          ← 返回列表
        </Link>
        <h1 className="mt-2 text-2xl font-bold">新建职位</h1>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>职位信息</CardTitle>
          <CardDescription>
            填写职位标题、JD 与硬性条件。提交后会自动写入 v1 版本快照。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <JobForm
            submitLabel="创建职位"
            onSubmit={handleSubmit}
          />
        </CardContent>
      </Card>
    </div>
  );
}
