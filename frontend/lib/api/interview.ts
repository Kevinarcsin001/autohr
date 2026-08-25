"use client";

import { apiClient } from "./client";

/**
 * 面试 API client：问题 + 反馈 + 会话管理 + 录用建议。
 */

// ============================================================================
// 面试问题
// ============================================================================

export type InterviewDimension =
  | "skill"
  | "project"
  | "weakness"
  | "culture"
  | "communication";

export interface InterviewQuestionOut {
  id: string;
  session_id?: string | null;
  candidate_id: string;
  job_id: string;
  batch_id: string;
  dimension: InterviewDimension;
  question: string;
  sort_order: number;
  generated_by: string | null;
  bank_question_id?: string | null;
  /** 非空 → 来自题库；空 → AI 现场生成。 */
  feedback_id?: string | null;
  feedback?: string | null;
  rating?: number | null;
}

export interface InterviewQuestionListResponse {
  items: InterviewQuestionOut[];
  total: number;
}

export interface FeedbackRequest {
  feedback?: string | null;
  rating?: number | null;
}

export interface FeedbackOut {
  id: string;
  question_id: string;
  reviewer_id: string;
  feedback: string | null;
  rating: number | null;
}

export interface FeedbackResponse {
  feedback: FeedbackOut;
  question: InterviewQuestionOut;
}

export async function listInterviewQuestionsApi(
  candidateId: string,
  jobId: string,
): Promise<InterviewQuestionListResponse> {
  const { data } = await apiClient.get<InterviewQuestionListResponse>(
    "/api/interview/questions",
    { params: { candidate_id: candidateId, job_id: jobId } },
  );
  return data;
}

export async function submitFeedbackApi(
  questionId: string,
  payload: FeedbackRequest,
): Promise<FeedbackResponse> {
  const { data } = await apiClient.post<FeedbackResponse>(
    `/api/interview/questions/${questionId}/feedback`,
    payload,
  );
  return data;
}

// ============================================================================
// 面试会话
// ============================================================================

export type InterviewSessionStatus = "scheduled" | "in_progress" | "completed";

export interface InterviewSessionOut {
  id: string;
  candidate_id: string;
  job_id: string;
  status: InterviewSessionStatus;
  interviewer_id: string | null;
  overall_notes: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface InterviewSessionListItem {
  id: string;
  candidate_id: string;
  candidate_name: string | null;
  job_id: string;
  job_title: string | null;
  status: InterviewSessionStatus;
  interviewer_id: string | null;
  interviewer_name: string | null;
  question_count: number;
  created_at: string | null;
}

export interface InterviewSessionListResponse {
  items: InterviewSessionListItem[];
  total: number;
}

export interface SessionDetailResponse {
  session: InterviewSessionOut;
  candidate_name: string | null;
  candidate_email: string | null;
  job_title: string | null;
  job_jd_summary: string | null;
  questions: InterviewQuestionOut[];
  recommendation: HiringRecommendationOut | null;
}

export interface CreateSessionRequest {
  candidate_id: string;
  job_id: string;
}

export interface UpdateSessionRequest {
  status?: InterviewSessionStatus;
  interviewer_id?: string;
  overall_notes?: string;
}

export interface BatchFeedbackItem {
  question_id: string;
  feedback?: string | null;
  rating?: number | null;
}

export interface BatchFeedbackRequest {
  feedbacks: BatchFeedbackItem[];
}

export interface BatchFeedbackResponse {
  saved: number;
  errors: { question_id: string; error: string }[];
}

export async function createInterviewSessionApi(
  payload: CreateSessionRequest,
): Promise<InterviewSessionOut> {
  const { data } = await apiClient.post<InterviewSessionOut>(
    "/api/interview/sessions",
    payload,
  );
  return data;
}

export async function listInterviewSessionsApi(
  params: {
    status?: string;
    job_id?: string;
    page?: number;
    page_size?: number;
  },
): Promise<InterviewSessionListResponse> {
  const { data } = await apiClient.get<InterviewSessionListResponse>(
    "/api/interview/sessions",
    { params },
  );
  return data;
}

export async function getInterviewSessionApi(
  sessionId: string,
): Promise<SessionDetailResponse> {
  const { data } = await apiClient.get<SessionDetailResponse>(
    `/api/interview/sessions/${sessionId}`,
  );
  return data;
}

export async function updateInterviewSessionApi(
  sessionId: string,
  payload: UpdateSessionRequest,
): Promise<InterviewSessionOut> {
  const { data } = await apiClient.patch<InterviewSessionOut>(
    `/api/interview/sessions/${sessionId}`,
    payload,
  );
  return data;
}

export async function batchSaveFeedbackApi(
  sessionId: string,
  payload: BatchFeedbackRequest,
): Promise<BatchFeedbackResponse> {
  const { data } = await apiClient.post<BatchFeedbackResponse>(
    `/api/interview/sessions/${sessionId}/feedback`,
    payload,
  );
  return data;
}

// ============================================================================
// 录用建议
// ============================================================================

export type HiringRecommendation = "hire" | "reserve" | "reject";
export interface HiringRecommendationOut {
  id: string;
  session_id: string;
  recommendation: HiringRecommendation;
  reasons: string[] | null;
  risks: string[] | null;
  probation_focus: string[] | null;
  generated_by: string | null;
  created_at: string | null;
}

export interface GenerateRecommendationResponse {
  recommendation: HiringRecommendationOut;
}

export async function generateRecommendationApi(
  sessionId: string,
): Promise<GenerateRecommendationResponse> {
  const { data } = await apiClient.post<GenerateRecommendationResponse>(
    `/api/interview/sessions/${sessionId}/recommend`,
    {},
  );
  return data;
}

export async function getRecommendationApi(
  sessionId: string,
): Promise<GenerateRecommendationResponse> {
  const { data } = await apiClient.get<GenerateRecommendationResponse>(
    `/api/interview/sessions/${sessionId}/recommend`,
  );
  return data;
}

// ============================================================================
// 渐进式自适应面试（M1）
// ============================================================================

export interface AdaptiveSignal {
  signal: string;
  weight: number;
}

export interface AdaptiveTurn {
  id: string;
  seq: number;
  question_item_id: string | null;
  question_text: string;
  dimension: InterviewDimension | null;
  category_id: string | null;
  category_name: string | null;
  answer_text: string | null;
  answered_at: string | null;
  audio_storage_key: string | null;
  transcription_status: "pending" | "processing" | "done" | "failed" | null;
  rating: number | null;
  rating_evidence: {
    key_points_hit?: string[];
    key_points_missed?: string[];
    strengths?: string[];
    flaws?: string[];
    follow_up_suggestion?: string;
    is_followup?: boolean;
    anchor_quote?: string;
    skipped?: boolean;
  } | null;
  rating_model: string | null;
  next_decision: {
    action?: "deepen" | "retry" | "switch" | "complete" | "followup";
    reason?: string;
    difficulty?: number;
    weak?: boolean;
  } | null;
}

export interface AdaptiveBranch {
  category_id: string;
  category_name: string;
  score: number;
  status: "pending" | "active" | "done" | "weak" | "exhausted";
  turns_count: number;
  avg_rating: number | null;
}

export interface AdaptiveStartResponse {
  session_id: string;
  mode: string;
  signals: AdaptiveSignal[];
  branches: AdaptiveBranch[];
  first_turn: AdaptiveTurn;
}

export interface AdaptiveStateResponse {
  session_id: string;
  mode: string;
  status: InterviewSessionStatus;
  total_turns: number;
  answered_turns: number;
  plan_signals: AdaptiveSignal[];
  branches: AdaptiveBranch[];
  turns: AdaptiveTurn[];
  ability: Record<string, number>;
  coverage: string;
  followup_turns: number;
  done: boolean;
  done_reason: string | null;
}

export interface AdaptiveAnswerResponse {
  turn: AdaptiveTurn;
  rating_error: string | null;
}

export interface AdaptiveNextResponse {
  turn: AdaptiveTurn | null;
  done: boolean;
  done_reason: string | null;
  decision: AdaptiveTurn["next_decision"];
}

/** 启动自适应面试（幂等：已有 turns 返回当前状态）。 */
export async function adaptiveStartApi(sessionId: string): Promise<AdaptiveStartResponse> {
  const { data } = await apiClient.post<AdaptiveStartResponse>(
    `/api/interview/sessions/${sessionId}/adaptive/start`,
  );
  return data;
}

/** 自适应面试大屏状态。 */
export async function adaptiveStateApi(sessionId: string): Promise<AdaptiveStateResponse> {
  const { data } = await apiClient.get<AdaptiveStateResponse>(
    `/api/interview/sessions/${sessionId}/adaptive/state`,
  );
  return data;
}

/** 提交回答（M1 手输文本）。 */
export async function adaptiveAnswerApi(
  sessionId: string,
  payload: { turn_id: string; answer_text: string },
): Promise<AdaptiveAnswerResponse> {
  const { data } = await apiClient.post<AdaptiveAnswerResponse>(
    `/api/interview/sessions/${sessionId}/adaptive/answer`,
    payload,
  );
  return data;
}

/** 获取下一题（幂等；全部完成 done=true）。 */
export async function adaptiveNextApi(
  sessionId: string,
  opts?: { forceCategoryId?: string; skipCurrent?: boolean },
): Promise<AdaptiveNextResponse> {
  const params = new URLSearchParams();
  if (opts?.forceCategoryId) params.set("force_category_id", opts.forceCategoryId);
  if (opts?.skipCurrent) params.set("skip_current", "true");
  const qs = params.toString();
  const { data } = await apiClient.get<AdaptiveNextResponse>(
    `/api/interview/sessions/${sessionId}/adaptive/next${qs ? `?${qs}` : ""}`,
  );
  return data;
}

/** 上传本题音频（M2a）：存 MinIO + 入队 Celery 转写 → 转写完成自动评分。 */
export async function adaptiveAudioApi(
  sessionId: string,
  payload: { turn_id: string; audio: Blob; filename?: string },
): Promise<{ turn_id: string; transcription_status: string; async_job_id: string | null; storage_key: string }> {
  const form = new FormData();
  form.append("turn_id", payload.turn_id);
  form.append(
    "audio",
    payload.audio,
    payload.filename ?? `turn-${payload.turn_id}.webm`,
  );
  const { data } = await apiClient.post(
    `/api/interview/sessions/${sessionId}/adaptive/audio`,
    form,
    { headers: { "Content-Type": "multipart/form-data" } },
  );
  return data;
}

/** 打点：标注本题在整场录制中的起始时间（毫秒或 mm:ss）。 */
export async function adaptiveMarkOffsetApi(
  sessionId: string,
  turnId: string,
  audioStartMs: string | number,
): Promise<void> {
  await apiClient.patch(
    `/api/interview/sessions/${sessionId}/adaptive/turns/${turnId}/offset`,
    { audio_start_ms: audioStartMs },
  );
}

/** 上传整场会议录制（钉钉/腾讯会议云录制下载或本地录制）。 */
export async function adaptiveUploadRecordingApi(
  sessionId: string,
  file: File,
): Promise<{ session_id: string; recording_status: string; storage_key: string }> {
  const form = new FormData();
  form.append("audio", file);
  const { data } = await apiClient.post(
    `/api/interview/sessions/${sessionId}/adaptive/recording`,
    form,
    { headers: { "Content-Type": "multipart/form-data" }, timeout: 300_000 },
  );
  return data;
}

/** 触发会后回捞处理。 */
export async function adaptiveProcessRecordingApi(
  sessionId: string,
): Promise<{ status: string; async_job_id: string | null }> {
  const { data } = await apiClient.post(
    `/api/interview/sessions/${sessionId}/adaptive/recording/process`,
  );
  return data;
}

/** 手动创建面试会话（HR 发起面试）。 */
export async function createSessionApi(
  payload: CreateSessionRequest,
): Promise<InterviewSessionOut> {
  const { data } = await apiClient.post<InterviewSessionOut>(
    "/api/interview/sessions",
    payload,
  );
  return data;
}

/** 面试官自然语言指挥：「问问他 RAG」「来道简单的」→ 解析并直接出题。 */
export async function adaptiveDirectApi(
  sessionId: string,
  text: string,
): Promise<{
  parsed: { category_id?: string; category_name?: string; difficulty?: number; matched_signal?: number };
  result: AdaptiveNextResponse;
}> {
  const { data } = await apiClient.post(
    `/api/interview/sessions/${sessionId}/adaptive/direct`,
    { text },
  );
  return data;
}

export interface PreviewItem {
  id: string;
  category_id: string;
  category_name: string;
  question: string;
  difficulty: number | null;
  points: number;
  relevance: number;
  tags: string[];
}

/** 候选题预览（当前难度+信号相关度排序的备选）。 */
export async function adaptivePreviewApi(
  sessionId: string,
  categoryId?: string,
): Promise<PreviewItem[]> {
  const { data } = await apiClient.get(
    `/api/interview/sessions/${sessionId}/adaptive/preview`,
    { params: categoryId ? { category_id: categoryId } : {} },
  );
  return data;
}
