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
import { Select } from "@/components/ui/select";
import {
  useDeleteLLMConfig,
  useLLMConfigs,
  useUpsertLLMConfig,
} from "@/hooks/useAdmin";
import { useAuthStore } from "@/stores/authStore";
import type { LLMScope } from "@/lib/api/admin";

const SCOPES: { value: LLMScope; label: string; hint: string }[] = [
  { value: "extractor", label: "结构化抽取", hint: "简历字段抽取" },
  { value: "scorer", label: "评分", hint: "评分维度计算" },
  { value: "reasoning", label: "推荐理由", hint: "评分依据生成" },
  { value: "interview", label: "面试问题", hint: "面试题生成" },
];

const ADAPTERS = ["zhipu", "qwen", "mock"];

const SCOPE_LABEL: Record<LLMScope, string> = {
  extractor: "结构化抽取",
  scorer: "评分",
  reasoning: "推荐理由",
  interview: "面试问题",
};

export default function LLMConfigPage() {
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === "admin";

  if (!isAdmin) {
    return (
      <div className="p-8">
        <Alert variant="destructive">
          <AlertTitle>权限不足</AlertTitle>
          <AlertDescription>
            仅团队管理员可访问 LLM 路由配置页面。
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-8">
      <header>
        <h1 className="text-2xl font-bold">LLM 路由配置</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          按 scope 配置主/备 LLM 适配器；同 scope 重复保存即更新。作用范围留空表示全局默认。
        </p>
      </header>

      <UpsertCard />
      <ConfigListCard />

      <div>
        <Link href="/admin" className="text-sm text-primary hover:underline">
          ← 返回管理首页
        </Link>
      </div>
    </div>
  );
}

// ============================================================================
// 新建 / 编辑
// ============================================================================

interface FormState {
  scope: LLMScope;
  primary: string;
  fallback: string;
  teamScope: "global" | "team";
  timeoutSeconds: string;
  circuitBreakerFailures: string;
}

const DEFAULT_FORM: FormState = {
  scope: "extractor",
  primary: "zhipu",
  fallback: "qwen",
  teamScope: "team",
  timeoutSeconds: "",
  circuitBreakerFailures: "",
};

function UpsertCard() {
  const upsert = useUpsertLLMConfig();
  const [form, setForm] = useState<FormState>(DEFAULT_FORM);
  const [error, setError] = useState<string | null>(null);

  const setField = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((prev) => ({ ...prev, [k]: v }));

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await upsert.mutateAsync({
        scope: form.scope,
        primary: form.primary,
        fallback: form.fallback || null,
        team_id: form.teamScope === "global" ? null : undefined,
        timeout_seconds: form.timeoutSeconds
          ? Number(form.timeoutSeconds)
          : null,
        circuit_breaker_failures: form.circuitBreakerFailures
          ? Number(form.circuitBreakerFailures)
          : null,
      });
      setForm(DEFAULT_FORM);
    } catch (err: unknown) {
      setError(extractErrorMessage(err) ?? "保存失败");
    }
  };

  const onReset = () => {
    setForm(DEFAULT_FORM);
    setError(null);
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>新增 / 更新配置</CardTitle>
        <CardDescription>
          同 (team_id, scope) 已存在则更新；否则插入。
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2">
          {error && (
            <div className="sm:col-span-2">
              <Alert variant="destructive">
                <AlertTitle>操作失败</AlertTitle>
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="scope">用途</Label>
            <Select
              id="scope"
              value={form.scope}
              onChange={(e) => setField("scope", e.target.value as LLMScope)}
            >
              {SCOPES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label} — {s.hint}
                </option>
              ))}
            </Select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="teamScope">作用范围</Label>
            <Select
              id="teamScope"
              value={form.teamScope}
              onChange={(e) =>
                setField("teamScope", e.target.value as "global" | "team")
              }
            >
              <option value="team">当前团队</option>
              <option value="global">全局默认（admin 写入）</option>
            </Select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="primary">主模型适配器</Label>
            <Select
              id="primary"
              value={form.primary}
              onChange={(e) => setField("primary", e.target.value)}
            >
              {ADAPTERS.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </Select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="fallback">备用适配器（可选）</Label>
            <Select
              id="fallback"
              value={form.fallback}
              onChange={(e) => setField("fallback", e.target.value)}
            >
              <option value="">无</option>
              {ADAPTERS.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </Select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="timeout">超时秒数（可选）</Label>
            <Input
              id="timeout"
              type="number"
              min={1}
              max={600}
              placeholder="留空 = 默认"
              value={form.timeoutSeconds}
              onChange={(e) => setField("timeoutSeconds", e.target.value)}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="breaker">熔断阈值（可选）</Label>
            <Input
              id="breaker"
              type="number"
              min={1}
              max={20}
              placeholder="留空 = 默认"
              value={form.circuitBreakerFailures}
              onChange={(e) =>
                setField("circuitBreakerFailures", e.target.value)
              }
            />
          </div>

          <div className="flex gap-2 sm:col-span-2">
            <Button type="submit" disabled={upsert.isPending}>
              {upsert.isPending ? "保存中…" : "保存"}
            </Button>
            <Button type="button" variant="ghost" onClick={onReset}>
              重置
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

// ============================================================================
// 列表
// ============================================================================

function ConfigListCard() {
  const { data, isLoading, isError } = useLLMConfigs();
  const del = useDeleteLLMConfig();
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
          无法加载配置列表。
        </CardContent>
      </Card>
    );
  }

  const items = data?.items ?? [];

  if (items.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>当前配置</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          暂无配置。使用上方表单创建。
        </CardContent>
      </Card>
    );
  }

  const onDelete = async (id: string) => {
    if (!confirm("确认删除该 LLM 配置？")) return;
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
        <CardTitle>当前配置</CardTitle>
        <CardDescription>
          包含全局默认 + 当前 team 自有；按 scope 排序。
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b text-left text-xs text-muted-foreground">
              <tr>
                <th className="py-2 pr-3">用途</th>
                <th className="py-2 pr-3">范围</th>
                <th className="py-2 pr-3">主</th>
                <th className="py-2 pr-3">备</th>
                <th className="py-2 pr-3">超时</th>
                <th className="py-2 pr-3">熔断</th>
                <th className="py-2 pr-3">更新</th>
                <th className="py-2"></th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.id} className="border-b last:border-0">
                  <td className="py-2 pr-3">
                    <Badge variant="outline">{SCOPE_LABEL[it.scope]}</Badge>
                  </td>
                  <td className="py-2 pr-3">
                    {it.team_id ? (
                      <span className="text-muted-foreground">本团队</span>
                    ) : (
                      <Badge>全局</Badge>
                    )}
                  </td>
                  <td className="py-2 pr-3 font-mono">{it.primary}</td>
                  <td className="py-2 pr-3 font-mono text-muted-foreground">
                    {it.fallback ?? "—"}
                  </td>
                  <td className="py-2 pr-3">
                    {it.timeout_seconds ?? "默认"}
                  </td>
                  <td className="py-2 pr-3">
                    {it.circuit_breaker_failures ?? "默认"}
                  </td>
                  <td className="py-2 pr-3 text-xs text-muted-foreground">
                    {new Date(it.updated_at).toLocaleString("zh-CN")}
                  </td>
                  <td className="py-2 text-right">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => onDelete(it.id)}
                      disabled={pendingId === it.id}
                    >
                      {pendingId === it.id ? "删除中…" : "删除"}
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
