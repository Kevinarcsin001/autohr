"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchResumeBank } from "@/lib/api/resumeBank";

export function useResumeBank() {
  return useQuery({
    queryKey: ["resume-bank"],
    queryFn: fetchResumeBank,
    staleTime: 10_000,
  });
}
