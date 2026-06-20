import { apiClient } from "./client";

export interface ResumeBankItem {
  resume_id: string;
  candidate_id: string | null;
  candidate_name: string | null;
  candidate_email: string | null;
  filename: string;
  parse_status: string;
  extract_status: string | null;
  score_total: number | null;
  job_id: string | null;
  uploaded_at: string;
}

export async function fetchResumeBank(): Promise<ResumeBankItem[]> {
  const resp = await apiClient.get<{ items: ResumeBankItem[] }>(
    "/api/resumes/",
  );
  return resp.data.items;
}
