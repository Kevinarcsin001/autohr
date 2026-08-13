"use client";

import { Suspense, useCallback } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useQuery } from "@tanstack/react-query";
import {
  listTeamCandidatesApi,
  type TeamCandidateListParams,
  type TeamCandidateItem,
} from "@/lib/api/candidates";
import { formatDateTime } from "@/lib/utils";

const SOURCE_LABEL: Record<string, string> = {
  upload: "上传",
  platform: "平台",
  email: "邮件",
};

const EDUCATION_LABEL: Record<string, string> = {
  high_school: "高中",
  bachelor: "本科",
  master: "硕士",
  phd: "博士",
};

export default function CandidatesPage() {
  return (
    <Suspense fallback={<div className="p-8 text-sm">加载中...</div>}>
      <CandidatesContent />
    </Suspense>
  );
}

function CandidatesContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const source = searchParams.get("source") || "";
  const education = searchParams.get("education") || "";
  const skill = searchParams.get("skill") || "";
  const search = searchParams.get("search") || "";
  const sortBy = searchParams.get("sort_by") || "created_at";
  const sortOrder = searchParams.get("sort_order") || "desc";
  const page = Number(searchParams.get("page") || "1");
  const pageSize = Number(searchParams.get("page_size") || "50");

  const params: TeamCandidateListParams = {
    source: source || undefined,
    education: education || undefined,
    skill: skill || undefined,
    search: search || undefined,
    sort_by: sortBy,
    sort_order: sortOrder,
    page,
    page_size: pageSize,
  };

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["team-candidates", params],
    queryFn: () => listTeamCandidatesApi(params),
    staleTime: 10_000,
    placeholderData: (prev) => prev,
  });

  const updateUrl = useCallback(
    (updates: Record<string, string | number | null>) => {
      const next = new URLSearchParams(searchParams.toString());
      for (const [k, v] of Object.entries(updates)) {
        if (v === null || v === undefined || v === "") {
          next.delete(k);
        } else {
          next.set(k, String(v));
        }
      }
      router.replace(`/candidates?${next.toString()}`, { scroll: false });
    },
    [router, searchParams],
  );

  const items = data?.items ?? [];
  const total = data?.total ?? 0;

  const renderSortIcon = (field: string) => {
    if (sortBy !== field)
      return <span className="ml-1 text-xs text-muted-foreground">↕</span>;
    return (
      <span className="ml-1 text-xs">
        {sortOrder === "asc" ? "↑" : "↓"}
      </span>
    );
  };

  const handleSort = (field: string) => {
    if (sortBy === field) {
      updateUrl({ sort_order: sortOrder === "asc" ? "desc" : "asc" });
    } else {
      updateUrl({ sort_by: field, sort_order: "asc" });
    }
  };

  return (
    <div className="mx-auto max-w-[1400px] space-y-4 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">候选人</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            团队内所有候选人（跨职位聚合）
          </p>
        </div>
        <Link href="/uploads">
          <Button variant="outline" size="sm">
            上传简历
          </Button>
        </Link>
      </div>

      {/* 筛选栏 */}
      <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-muted/30 p-3">
        <input
          type="text"
          placeholder="搜索姓名..."
          value={search}
          onChange={(e) =>
            updateUrl({ search: e.target.value || null, page: 1 })
          }
          className="h-8 w-40 rounded-md border px-2 text-xs"
        />
        <select
          value={source}
          onChange={(e) =>
            updateUrl({ source: e.target.value || null, page: 1 })
          }
          className="h-8 rounded-md border bg-background px-2 text-xs"
          aria-label="筛选来源"
        >
          <option value="">全部来源</option>
          <option value="upload">上传</option>
          <option value="platform">平台</option>
          <option value="email">邮件</option>
        </select>
        <select
          value={education}
          onChange={(e) =>
            updateUrl({ education: e.target.value || null, page: 1 })
          }
          className="h-8 rounded-md border bg-background px-2 text-xs"
          aria-label="筛选学历"
        >
          <option value="">全部学历</option>
          <option value="high_school">高中</option>
          <option value="bachelor">本科</option>
          <option value="master">硕士</option>
          <option value="phd">博士</option>
        </select>
        <input
          type="text"
          placeholder="技能关键词..."
          value={skill}
          onChange={(e) =>
            updateUrl({ skill: e.target.value || null, page: 1 })
          }
          className="h-8 w-32 rounded-md border px-2 text-xs"
        />
        {(source || education || skill || search) && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() =>
              updateUrl({
                source: null,
                education: null,
                skill: null,
                search: null,
                page: 1,
              })
            }
          >
            清除筛选
          </Button>
        )}
      </div>

      {/* 错误 */}
      {isError && (
        <Alert variant="destructive">
          <AlertTitle>加载失败</AlertTitle>
          <AlertDescription>
            {(error as Error)?.message ?? "请稍后重试"}
          </AlertDescription>
        </Alert>
      )}

      {/* 加载 */}
      {isLoading && (
        <div className="py-8 text-sm text-muted-foreground">加载中...</div>
      )}

      {/* 空 */}
      {!isLoading && !isError && items.length === 0 && (
        <div className="py-16 text-center text-sm text-muted-foreground">
          暂无候选人
          <p className="mt-1 text-xs">请先上传简历</p>
        </div>
      )}

      {/* 表格 */}
      {!isLoading && items.length > 0 && (
        <div className="overflow-hidden rounded-lg border">
          <table className="w-full text-sm">
            <thead className="border-b bg-muted/50 text-left text-xs text-muted-foreground">
              <tr>
                <th
                  className="cursor-pointer px-4 py-2.5 font-medium hover:text-foreground"
                  onClick={() => handleSort("name")}
                >
                  姓名{renderSortIcon("name")}
                </th>
                <th className="px-4 py-2.5 font-medium">联系方式</th>
                <th
                  className="cursor-pointer px-4 py-2.5 font-medium hover:text-foreground"
                  onClick={() => handleSort("source_type")}
                >
                  来源{renderSortIcon("source_type")}
                </th>
                <th className="px-4 py-2.5 font-medium">学历</th>
                <th className="px-4 py-2.5 font-medium">技能</th>
                <th className="px-4 py-2.5 font-medium">年限</th>
                <th
                  className="cursor-pointer px-4 py-2.5 font-medium hover:text-foreground"
                  onClick={() => handleSort("created_at")}
                >
                  入库时间{renderSortIcon("created_at")}
                </th>
                <th className="px-4 py-2.5 font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item: TeamCandidateItem) => (
                <tr
                  key={item.id}
                  className="cursor-pointer border-b transition-colors hover:bg-muted/30"
                  onClick={() => router.push(`/candidates/${item.id}`)}
                >
                  <td className="px-4 py-2.5 font-medium">{item.name}</td>
                  <td className="px-4 py-2.5 text-xs text-muted-foreground">
                    {item.email || item.phone || "—"}
                  </td>
                  <td className="px-4 py-2.5">
                    {item.source_type ? (
                      <Badge variant="secondary">
                        {SOURCE_LABEL[item.source_type] ?? item.source_type}
                      </Badge>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-muted-foreground">
                    {item.education
                      ? EDUCATION_LABEL[item.education] ?? item.education
                      : "—"}
                  </td>
                  <td className="px-4 py-2.5">
                    {item.skills.length > 0 ? (
                      <div className="flex flex-wrap gap-1">
                        {item.skills.map((s, i) => (
                          <span
                            key={i}
                            className="rounded bg-muted px-1.5 py-0.5 text-xs"
                          >
                            {s}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-muted-foreground">
                    {item.years_of_experience != null
                      ? `${item.years_of_experience} 年`
                      : "—"}
                  </td>
                  <td className="px-4 py-2.5 text-xs text-muted-foreground">
                    {item.created_at ? formatDateTime(item.created_at) : "—"}
                  </td>
                  <td className="px-4 py-2.5">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={(e) => {
                        e.stopPropagation();
                        router.push(`/candidates/${item.id}`);
                      }}
                    >
                      查看
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 分页 */}
      {total > pageSize && (
        <div className="flex items-center justify-center gap-2 text-sm">
          <Button
            variant="outline"
            size="sm"
            disabled={page <= 1}
            onClick={() => updateUrl({ page: page - 1 })}
          >
            上一页
          </Button>
          <span className="text-muted-foreground">
            第 {page} 页 / 共 {Math.ceil(total / pageSize)} 页（{total} 条）
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={page * pageSize >= total}
            onClick={() => updateUrl({ page: page + 1 })}
          >
            下一页
          </Button>
        </div>
      )}
    </div>
  );
}
