"use client";

import { useState } from "react";
import Link from "next/link";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  useCreateQuestionCategory,
  useDeleteQuestionCategory,
  useQuestionCategories,
} from "@/hooks/useQuestionBank";
import { useAuthStore } from "@/stores/authStore";

/**
 * 题库分类管理页。
 *
 * 套用 admin/llm/page.tsx 的 UpsertCard + ListCard 模式：
 * - 上方表单新建分类（基础/RAG/agent/微调等，含 target_points 默认配额）
 * - 下方列表展示分类，点击进入该分类的题目管理
 */

interface FormState {
  name: string;
  slug: string;
  targetPoints: string;
  sortOrder: string;
}

const DEFAULT_FORM: FormState = {
  name: "",
  slug: "",
  targetPoints: "0",
  sortOrder: "0",
};

export default function QuestionBankPage() {
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === "admin";

  if (!isAdmin) {
    return (
      <div className="p-8">
        <Alert variant="destructive">
          <AlertTitle>权限不足</AlertTitle>
          <AlertDescription>仅团队管理员可访问题库管理页面。</AlertDescription>
        </Alert>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-8">
      <header>
        <h1 className="text-2xl font-bold">题库管理</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          管理面试题分类与预设题目。各分类的「目标分」相加建议 = 100，用于按分类配额凑分组卷。
        </p>
      </header>

      <UpsertCategoryCard />
      <CategoryListCard />

      <div>
        <Link href="/admin" className="text-sm text-primary hover:underline">
          ← 返回管理首页
        </Link>
      </div>
    </div>
  );
}

// ============================================================================
// 新建分类
// ============================================================================

function UpsertCategoryCard() {
  const create = useCreateQuestionCategory();
  const [form, setForm] = useState<FormState>(DEFAULT_FORM);
  const [error, setError] = useState<string | null>(null);

  const setField = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((prev) => ({ ...prev, [k]: v }));

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!form.name.trim() || !form.slug.trim()) {
      setError("分类名称与标识不能为空");
      return;
    }
    try {
      await create.mutateAsync({
        name: form.name.trim(),
        slug: form.slug.trim(),
        target_points: Number(form.targetPoints) || 0,
        sort_order: Number(form.sortOrder) || 0,
      });
      setForm(DEFAULT_FORM);
    } catch (err: unknown) {
      setError(extractErrorMessage(err) ?? "保存失败");
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>新增分类</CardTitle>
        <CardDescription>
          如：基础 / RAG / agent / 模型微调。标识（slug）只能含小写字母、数字、中划线、下划线。
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2">
          {error && (
            <div className="sm:col-span-2">
              <Alert variant="destructive">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            </div>
          )}
          <div className="space-y-2">
            <Label htmlFor="name">分类名称</Label>
            <Input
              id="name"
              value={form.name}
              onChange={(e) => setField("name", e.target.value)}
              placeholder="如：RAG"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="slug">标识（slug）</Label>
            <Input
              id="slug"
              value={form.slug}
              onChange={(e) => setField("slug", e.target.value)}
              placeholder="如：rag"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="tp">目标分（凑分配额）</Label>
            <Input
              id="tp"
              type="number"
              min={0}
              max={100}
              value={form.targetPoints}
              onChange={(e) => setField("targetPoints", e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="sort">排序</Label>
            <Input
              id="sort"
              type="number"
              min={0}
              value={form.sortOrder}
              onChange={(e) => setField("sortOrder", e.target.value)}
            />
          </div>
          <div className="flex gap-2 sm:col-span-2">
            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? "保存中…" : "新增分类"}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                setForm(DEFAULT_FORM);
                setError(null);
              }}
            >
              重置
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

// ============================================================================
// 分类列表
// ============================================================================

function CategoryListCard() {
  const { data, isLoading, isError } = useQuestionCategories();
  const del = useDeleteQuestionCategory();
  const [pendingId, setPendingId] = useState<string | null>(null);

  if (isLoading) {
    return (
      <Card>
        <CardContent className="py-6 text-sm text-muted-foreground">
          加载中…
        </CardContent>
      </Card>
    );
  }
  if (isError) {
    return (
      <Card>
        <CardContent className="py-6 text-sm text-red-600">
          无法加载分类列表。
        </CardContent>
      </Card>
    );
  }

  const items = data ?? [];
  if (items.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>分类列表</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          暂无分类。使用上方表单创建。
        </CardContent>
      </Card>
    );
  }

  const totalTarget = items.reduce((sum, c) => sum + c.target_points, 0);

  const onDelete = async (id: string) => {
    if (!confirm("删除分类将级联删除其下所有题目，确认？")) return;
    setPendingId(id);
    try {
      await del.mutateAsync(id);
    } finally {
      setPendingId(null);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>分类列表</CardTitle>
        <CardDescription>
          目标分合计 <Badge variant={totalTarget === 100 ? "success" : "warning"}>{totalTarget} / 100</Badge>
          {totalTarget !== 100 && "（建议合计 100）"}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b text-left text-xs text-muted-foreground">
              <tr>
                <th className="py-2 pr-3">名称</th>
                <th className="py-2 pr-3">标识</th>
                <th className="py-2 pr-3">目标分</th>
                <th className="py-2 pr-3">排序</th>
                <th className="py-2 pr-3">状态</th>
                <th className="py-2"></th>
              </tr>
            </thead>
            <tbody>
              {items.map((c) => (
                <tr key={c.id} className="border-b last:border-0">
                  <td className="py-2 pr-3">
                    <Link
                      href={`/admin/question-bank/${c.id}`}
                      className="font-medium text-primary hover:underline"
                    >
                      {c.name}
                    </Link>
                  </td>
                  <td className="py-2 pr-3 font-mono text-xs text-muted-foreground">
                    {c.slug}
                  </td>
                  <td className="py-2 pr-3">{c.target_points}</td>
                  <td className="py-2 pr-3">{c.sort_order}</td>
                  <td className="py-2 pr-3">
                    {c.is_active ? (
                      <Badge variant="success">启用</Badge>
                    ) : (
                      <Badge variant="secondary">停用</Badge>
                    )}
                  </td>
                  <td className="py-2 text-right">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => onDelete(c.id)}
                      disabled={pendingId === c.id}
                    >
                      {pendingId === c.id ? "删除中…" : "删除"}
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

function extractErrorMessage(err: unknown): string | null {
  const msg = (
    err as { response?: { data?: { error?: { message?: string } } } }
  )?.response?.data?.error?.message;
  return msg ?? null;
}
