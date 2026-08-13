"use client";

import { useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { TagInput } from "@/components/TagInput";
import { DIMENSION_LABEL, DIMENSION_OPTIONS } from "@/lib/constants/interview";
import {
  useCreateQuestionBankItem,
  useDeleteQuestionBankItem,
  useQuestionBankItems,
  useQuestionCategories,
  useUpdateQuestionBankItem,
} from "@/hooks/useQuestionBank";
import type { InterviewDimension } from "@/lib/api/interview";

/**
 * 分类详情：题目 CRUD。
 *
 * 列出该分类下所有题目，Dialog 表单新建/编辑题目：
 * 维度（可选）/ 分值 / 难度 / 题干 / 参考答案 / 标签。
 */

interface ItemFormState {
  dimension: "" | InterviewDimension;
  points: string;
  difficulty: string;
  question: string;
  reference_answer: string;
  tags: string[];
}

const DEFAULT_ITEM_FORM: ItemFormState = {
  dimension: "",
  points: "10",
  difficulty: "",
  question: "",
  reference_answer: "",
  tags: [],
};

export default function CategoryItemsPage() {
  const params = useParams<{ categoryId: string }>();
  const categoryId = params.categoryId;

  const { data: categories } = useQuestionCategories();
  const category = categories?.find((c) => c.id === categoryId);

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-8">
      <header className="space-y-1">
        <div className="flex items-center gap-2">
          <Link
            href="/admin/question-bank"
            className="text-sm text-primary hover:underline"
          >
            ← 题库
          </Link>
        </div>
        <h1 className="text-2xl font-bold">
          {category ? `${category.name} 题目` : "分类题目"}
        </h1>
        <p className="text-sm text-muted-foreground">
          管理该分类下的题目。建议分值为整数（如 10/15/20/30），便于凑 100 分。
        </p>
      </header>

      <ItemsCard categoryId={categoryId} />
    </div>
  );
}

// ============================================================================
// 题目列表 + 新增/编辑
// ============================================================================

function ItemsCard({ categoryId }: { categoryId: string }) {
  const { data, isLoading, isError } = useQuestionBankItems(categoryId);
  const del = useDeleteQuestionBankItem(categoryId);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
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
          无法加载题目列表。
        </CardContent>
      </Card>
    );
  }

  const items = data ?? [];

  const onDelete = async (id: string) => {
    if (!confirm("确认删除该题目？")) return;
    setPendingId(id);
    try {
      await del.mutateAsync(id);
    } finally {
      setPendingId(null);
    }
  };

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <div>
          <CardTitle>题目列表</CardTitle>
          <CardDescription>共 {items.length} 题</CardDescription>
        </div>
        <Button
          onClick={() => {
            setEditingId(null);
            setDialogOpen(true);
          }}
        >
          新增题目
        </Button>
      </CardHeader>
      <CardContent>
        {items.length === 0 ? (
          <p className="py-4 text-sm text-muted-foreground">暂无题目，点「新增题目」开始。</p>
        ) : (
          <div className="space-y-3">
            {items.map((it) => (
              <div
                key={it.id}
                className="rounded-md border p-3"
              >
                <div className="mb-1 flex items-center gap-2">
                  <Badge variant="success">{it.points} 分</Badge>
                  {it.dimension && (
                    <Badge variant="secondary">{DIMENSION_LABEL[it.dimension]}</Badge>
                  )}
                  {it.difficulty && (
                    <Badge variant="outline">难度 {it.difficulty}</Badge>
                  )}
                  {!it.is_active && <Badge variant="destructive">停用</Badge>}
                  <div className="ml-auto flex gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        setEditingId(it.id);
                        setDialogOpen(true);
                      }}
                    >
                      编辑
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => onDelete(it.id)}
                      disabled={pendingId === it.id}
                    >
                      {pendingId === it.id ? "删除中…" : "删除"}
                    </Button>
                  </div>
                </div>
                <p className="text-sm">{it.question}</p>
                {it.tags && it.tags.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {it.tags.map((t) => (
                      <Badge key={t} variant="outline" className="text-xs">
                        {t}
                      </Badge>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </CardContent>

      <ItemDialog
        open={dialogOpen}
        categoryId={categoryId}
        editingId={editingId}
        onClose={() => setDialogOpen(false)}
      />
    </Card>
  );
}

// ============================================================================
// 题目新增/编辑 Dialog
// ============================================================================

function ItemDialog({
  open,
  categoryId,
  editingId,
  onClose,
}: {
  open: boolean;
  categoryId: string;
  editingId: string | null;
  onClose: () => void;
}) {
  const create = useCreateQuestionBankItem();
  const update = useUpdateQuestionBankItem();
  const { data: items } = useQuestionBankItems(categoryId);
  const editing = items?.find((i) => i.id === editingId);

  const [form, setForm] = useState<ItemFormState>(DEFAULT_ITEM_FORM);
  const [error, setError] = useState<string | null>(null);
  const [hydrated, setHydrated] = useState(false);

  // 打开时 hydrate 表单
  if (open && !hydrated) {
    setForm(
      editing
        ? {
            dimension: (editing.dimension ?? "") as ItemFormState["dimension"],
            points: String(editing.points),
            difficulty: editing.difficulty ? String(editing.difficulty) : "",
            question: editing.question,
            reference_answer: editing.reference_answer ?? "",
            tags: editing.tags ?? [],
          }
        : DEFAULT_ITEM_FORM,
    );
    setError(null);
    setHydrated(true);
  }
  if (!open && hydrated) {
    setHydrated(false);
  }

  const setField = <K extends keyof ItemFormState>(k: K, v: ItemFormState[K]) =>
    setForm((prev) => ({ ...prev, [k]: v }));

  const submit = async () => {
    setError(null);
    if (!form.question.trim()) {
      setError("题干不能为空");
      return;
    }
    const payload = {
      category_id: categoryId,
      dimension: form.dimension || null,
      question: form.question.trim(),
      points: Number(form.points) || 1,
      difficulty: form.difficulty ? Number(form.difficulty) : null,
      reference_answer: form.reference_answer.trim() || null,
      tags: form.tags.length > 0 ? form.tags : null,
    };
    try {
      if (editingId) {
        await update.mutateAsync({ itemId: editingId, payload });
      } else {
        await create.mutateAsync(payload);
      }
      onClose();
    } catch (err: unknown) {
      setError(extractErrorMessage(err) ?? "保存失败");
    }
  };

  const submitting = create.isPending || update.isPending;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={editingId ? "编辑题目" : "新增题目"}
      maxWidth="max-w-2xl"
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={submitting}>
            取消
          </Button>
          <Button onClick={submit} disabled={submitting}>
            {submitting ? "保存中…" : "保存"}
          </Button>
        </>
      }
    >
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <div className="grid gap-4 sm:grid-cols-3">
        <div className="space-y-2">
          <Label htmlFor="points">分值</Label>
          <Input
            id="points"
            type="number"
            min={1}
            max={100}
            value={form.points}
            onChange={(e) => setField("points", e.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="dimension">维度（可选）</Label>
          <Select
            id="dimension"
            value={form.dimension}
            onChange={(e) =>
              setField("dimension", e.target.value as ItemFormState["dimension"])
            }
          >
            <option value="">不指定</option>
            {DIMENSION_OPTIONS.map((d) => (
              <option key={d.value} value={d.value}>
                {d.label}
              </option>
            ))}
          </Select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="difficulty">难度（1-5，可选）</Label>
          <Input
            id="difficulty"
            type="number"
            min={1}
            max={5}
            value={form.difficulty}
            onChange={(e) => setField("difficulty", e.target.value)}
          />
        </div>
      </div>
      <div className="space-y-2">
        <Label htmlFor="question">题干</Label>
        <textarea
          id="question"
          value={form.question}
          onChange={(e) => setField("question", e.target.value)}
          rows={3}
          maxLength={1000}
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="ref">参考答案（可选）</Label>
        <textarea
          id="ref"
          value={form.reference_answer}
          onChange={(e) => setField("reference_answer", e.target.value)}
          rows={2}
          maxLength={2000}
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      </div>
      <div className="space-y-2">
        <Label>标签（可选）</Label>
        <TagInput value={form.tags} onChange={(v) => setField("tags", v)} />
      </div>
    </Dialog>
  );
}

function extractErrorMessage(err: unknown): string | null {
  const msg = (
    err as { response?: { data?: { error?: { message?: string } } } }
  )?.response?.data?.error?.message;
  return msg ?? null;
}
