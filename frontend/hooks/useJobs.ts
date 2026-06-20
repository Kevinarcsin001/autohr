"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createJobApi,
  deleteJobApi,
  getJobApi,
  listJobVersionsApi,
  listJobsApi,
  updateJobApi,
  type JobCreatePayload,
  type JobUpdatePayload,
} from "@/lib/api/jobs";

// ============================================================================
// Query Keys
// ============================================================================

export const JOBS_KEY = (params: {
  status?: string;
  page: number;
  page_size: number;
}) => ["jobs", "list", params] as const;

export const JOB_KEY = (jobId: string) => ["jobs", "detail", jobId] as const;
export const JOB_VERSIONS_KEY = (jobId: string) =>
  ["jobs", "versions", jobId] as const;

// ============================================================================
// Hooks
// ============================================================================

export function useJobs(params: {
  status?: string;
  page?: number;
  page_size?: number;
} = {}) {
  const query = {
    status: (params?.status ?? undefined) as
      | "draft"
      | "active"
      | "closed"
      | undefined,
    page: params?.page ?? 1,
    page_size: params?.page_size ?? 20,
  };
  return useQuery({
    queryKey: JOBS_KEY(query),
    queryFn: () => listJobsApi(query),
    staleTime: 15_000,
  });
}

export function useJob(jobId: string | undefined) {
  return useQuery({
    queryKey: JOB_KEY(jobId ?? ""),
    queryFn: () => getJobApi(jobId!),
    enabled: !!jobId,
    staleTime: 15_000,
  });
}

export function useJobVersions(jobId: string | undefined) {
  return useQuery({
    queryKey: JOB_VERSIONS_KEY(jobId ?? ""),
    queryFn: () => listJobVersionsApi(jobId!),
    enabled: !!jobId,
    staleTime: 15_000,
  });
}

function invalidateJobQueries(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: ["jobs"] });
}

export function useCreateJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: JobCreatePayload) => createJobApi(payload),
    onSuccess: () => invalidateJobQueries(qc),
  });
}

export function useUpdateJob(jobId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: JobUpdatePayload) =>
      updateJobApi(jobId, payload),
    onSuccess: () => {
      invalidateJobQueries(qc);
    },
  });
}

export function useDeleteJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => deleteJobApi(jobId),
    onSuccess: () => invalidateJobQueries(qc),
  });
}
