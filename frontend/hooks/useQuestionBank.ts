"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  assembleApi,
  composeFromBankApi,
  createCategoryApi,
  createItemApi,
  deleteCategoryApi,
  deleteItemApi,
  listCategoriesApi,
  listItemsByCategoryApi,
  updateCategoryApi,
  updateItemApi,
  type AssembleRequest,
  type CategoryCreate,
  type CategoryUpdate,
  type ItemCreate,
  type ItemUpdate,
} from "@/lib/api/questionBank";

const CATEGORIES_KEY = ["question-bank", "categories"] as const;
const itemsKey = (categoryId: string) =>
  ["question-bank", "items", categoryId] as const;

// ============================================================================
// 分类
// ============================================================================

export function useQuestionCategories() {
  return useQuery({
    queryKey: CATEGORIES_KEY,
    queryFn: () => listCategoriesApi(),
    staleTime: 30_000,
  });
}

export function useCreateQuestionCategory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: CategoryCreate) => createCategoryApi(payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: CATEGORIES_KEY }),
  });
}

export function useUpdateQuestionCategory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      categoryId,
      payload,
    }: {
      categoryId: string;
      payload: CategoryUpdate;
    }) => updateCategoryApi(categoryId, payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: CATEGORIES_KEY }),
  });
}

export function useDeleteQuestionCategory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (categoryId: string) => deleteCategoryApi(categoryId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: CATEGORIES_KEY });
      qc.invalidateQueries({ queryKey: ["question-bank", "items"] });
    },
  });
}

// ============================================================================
// 题目
// ============================================================================

export function useQuestionBankItems(categoryId: string | null) {
  return useQuery({
    queryKey: itemsKey(categoryId ?? ""),
    queryFn: () => listItemsByCategoryApi(categoryId!),
    enabled: !!categoryId,
    staleTime: 15_000,
  });
}

export function useCreateQuestionBankItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: ItemCreate) => createItemApi(payload),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: itemsKey(data.category_id) });
    },
  });
}

export function useUpdateQuestionBankItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      itemId,
      payload,
    }: {
      itemId: string;
      payload: ItemUpdate;
    }) => updateItemApi(itemId, payload),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: itemsKey(data.category_id) });
    },
  });
}

export function useDeleteQuestionBankItem(categoryId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (itemId: string) => deleteItemApi(itemId),
    onSuccess: () => qc.invalidateQueries({ queryKey: itemsKey(categoryId) }),
  });
}

// ============================================================================
// 组卷
// ============================================================================

/** 预览组卷（不落库）。 */
export function useAssembleQuestions() {
  return useMutation({
    mutationFn: (payload: AssembleRequest) => assembleApi(payload),
  });
}

/** 组卷并写入 session（invalidate session detail）。 */
export function useComposeFromBank(sessionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: AssembleRequest) =>
      composeFromBankApi(sessionId, payload),
    onSuccess: () => {
      qc.invalidateQueries({
        queryKey: ["interview-session", sessionId],
      });
      qc.invalidateQueries({
        queryKey: ["interview-sessions"],
      });
    },
  });
}
