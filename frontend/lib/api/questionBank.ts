"use client";

import { apiClient } from "./client";
import type { InterviewDimension } from "./interview";

/**
 * 题库 API client：分类 CRUD + 题目 CRUD + 按分类配额凑 100 分组卷。
 */

// ============================================================================
// 分类
// ============================================================================

export interface QuestionCategory {
  id: string;
  team_id: string;
  name: string;
  slug: string;
  target_points: number;
  sort_order: number;
  is_active: boolean;
}

export interface CategoryCreate {
  name: string;
  slug: string;
  target_points: number;
  sort_order?: number;
}

export interface CategoryUpdate {
  name?: string;
  target_points?: number;
  sort_order?: number;
  is_active?: boolean;
}

export async function listCategoriesApi(): Promise<QuestionCategory[]> {
  const { data } = await apiClient.get<QuestionCategory[]>(
    "/api/question-bank/categories",
  );
  return data;
}

export async function createCategoryApi(
  payload: CategoryCreate,
): Promise<QuestionCategory> {
  const { data } = await apiClient.post<QuestionCategory>(
    "/api/question-bank/categories",
    payload,
  );
  return data;
}

export async function updateCategoryApi(
  categoryId: string,
  payload: CategoryUpdate,
): Promise<QuestionCategory> {
  const { data } = await apiClient.patch<QuestionCategory>(
    `/api/question-bank/categories/${categoryId}`,
    payload,
  );
  return data;
}

export async function deleteCategoryApi(categoryId: string): Promise<void> {
  await apiClient.delete(`/api/question-bank/categories/${categoryId}`);
}

// ============================================================================
// 题目
// ============================================================================

export interface QuestionBankItem {
  id: string;
  team_id: string;
  category_id: string;
  dimension: InterviewDimension | null;
  question: string;
  points: number;
  difficulty: number | null;
  tags: string[] | null;
  reference_answer: string | null;
  is_active: boolean;
}

export interface ItemCreate {
  category_id: string;
  dimension?: InterviewDimension | null;
  question: string;
  points: number;
  difficulty?: number | null;
  tags?: string[] | null;
  reference_answer?: string | null;
}

export type ItemUpdate = Partial<ItemCreate> & { is_active?: boolean };

export async function listItemsByCategoryApi(
  categoryId: string,
): Promise<QuestionBankItem[]> {
  const { data } = await apiClient.get<QuestionBankItem[]>(
    `/api/question-bank/categories/${categoryId}/items`,
  );
  return data;
}

export async function createItemApi(payload: ItemCreate): Promise<QuestionBankItem> {
  const { data } = await apiClient.post<QuestionBankItem>(
    "/api/question-bank/items",
    payload,
  );
  return data;
}

export async function updateItemApi(
  itemId: string,
  payload: ItemUpdate,
): Promise<QuestionBankItem> {
  const { data } = await apiClient.patch<QuestionBankItem>(
    `/api/question-bank/items/${itemId}`,
    payload,
  );
  return data;
}

export async function deleteItemApi(itemId: string): Promise<void> {
  await apiClient.delete(`/api/question-bank/items/${itemId}`);
}

// ============================================================================
// 组卷（assemble / compose）
// ============================================================================

export interface AssembleRequest {
  quotas?: Record<string, number> | null;
  tolerance?: number;
  exclude_question_ids?: string[] | null;
  /** 按候选人简历 + JD 动态匹配配额（需 session_id） */
  dynamic?: boolean;
  session_id?: string | null;
}

export interface CategoryDeficit {
  category_id: string;
  category_name: string;
  target: number;
  actual: number;
  gap: number;
}

export interface SignalInfo {
  signal: string;
  weight: number;
}

export interface QuotaPlanItem {
  category_id: string;
  category_name: string;
  base_points: number;
  quota_points: number;
  score: number;
  matched: boolean;
}

export interface AssemblePlan {
  total_target: number;
  signals: SignalInfo[];
  quotas: QuotaPlanItem[];
}

export interface AssembleResponse {
  items: QuestionBankItem[];
  actual_total: number;
  target_total: number;
  deficits: CategoryDeficit[];
  plan?: AssemblePlan | null;
}

/** 预览组卷（不落库）：返回选中题 + 实际总分 + 各分类缺口。 */
export async function assembleApi(payload: AssembleRequest): Promise<AssembleResponse> {
  const { data } = await apiClient.post<AssembleResponse>(
    "/api/question-bank/assemble",
    payload,
  );
  return data;
}

export interface ComposeResponse {
  batch_id: string;
  question_count: number;
  actual_total: number;
  target_total: number;
  deficits: CategoryDeficit[];
  plan?: AssemblePlan | null;
}

/** 组卷并写入指定 session（新 batch_id）。 */
export async function composeFromBankApi(
  sessionId: string,
  payload: AssembleRequest,
): Promise<ComposeResponse> {
  const { data } = await apiClient.post<ComposeResponse>(
    `/api/interview/sessions/${sessionId}/compose`,
    payload,
  );
  return data;
}
