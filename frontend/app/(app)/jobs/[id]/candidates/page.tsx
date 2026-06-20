"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";

import {
  type CandidateListItem,
} from "@/lib/api/candidates";
import {
  type CandidateGroupLiteral,
  parseCandidatesUrl,
  urlStateToCandidateParams,
} from "@/lib/candidatesUrlSync";
import { useCandidates } from "@/hooks/useCandidates";
import {
  triggerPipelineApi,
  usePipelineSSE,
} from "@/hooks/usePipelineSSE";
import {
  CandidateFilters,
  defaultFilterForm,
  formStateToParams,
  paramsToFormState,
  type FilterFormState,
} from "@/components/CandidateFilters";
import {
  CandidateTable,
  defaultColumnConfigs,
  paramsToSorting,
  sortingToParams,
} from "@/components/CandidateTable";
import { ExportButton } from "@/components/ExportButton";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Tabs } from "@/components/ui/tabs";
import {
  type ColumnConfig,
  type Density,
  type SavedView,
  deleteSavedView,
  listSavedViews,
  snapshotView,
  upsertSavedView,
} from "@/lib/savedViews";
import { useJob } from "@/hooks/useJobs";

// ============================================================================
// 默认值
// ============================================================================

const DEFAULT_DENSITY: Density = "default";

const PAGE_SIZE_OPTIONS_KEY = "autohr:candidates:page_size";
const DENSITY_KEY = "autohr:candidates:density";

// ============================================================================
// 页面外壳（Suspense 边界，因为 useSearchParams 要求）
// ============================================================================

export default function JobCandidatesPage() {
  return (
    <Suspense
      fallback={<div className="p-8 text-sm">加载中...</div>}
    >
      <JobCandidatesContent />
    </Suspense>
  );
}

function JobCandidatesContent() {
  const params = useParams<{ id: string }>();
  const jobId = params.id;
  const router = useRouter();
  const searchParams = useSearchParams();

  const { data: job, isLoading: jobLoading } = useJob(jobId);

  // ----- 从 URL 读所有筛选 -----
  const urlState = useMemo(
    () => parseCandidatesUrl(searchParams),
    [searchParams],
  );

  // ----- 列配置 + 密度：本地 state，初始化从 URL 或默认 -----
  const [columns, setColumns] = useState<ColumnConfig[]>(() =>
    defaultColumnConfigs(),
  );
  const [density, setDensity] = useState<Density>(DEFAULT_DENSITY);

  // 初始化：从 localStorage 恢复密度 / 页大小
  useEffect(() => {
    if (typeof window === "undefined") return;
    const d = window.localStorage.getItem(DENSITY_KEY) as Density | null;
    if (d === "compact" || d === "default" || d === "comfortable") {
      setDensity(d);
    }
    const ps = window.localStorage.getItem(PAGE_SIZE_OPTIONS_KEY);
    if (ps) {
      const n = Number(ps);
      if ([10, 20, 50, 100].includes(n) && n !== urlState.page_size) {
        // 仅在恢复值与当前 URL 不同时同步一次
        const sp = new URLSearchParams(window.location.search);
        sp.set("page_size", String(n));
        router.replace(`?${sp.toString()}`, { scroll: false });
      }
    }
    // 仅首次挂载执行
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ----- 保存视图列表 -----
  const [savedViews, setSavedViews] = useState<SavedView[]>([]);
  const [savingViewName, setSavingViewName] = useState<string>("");
  const [showSaveRow, setShowSaveRow] = useState<boolean>(false);

  const refreshViews = useCallback(() => {
    setSavedViews(listSavedViews(jobId));
  }, [jobId]);

  useEffect(() => {
    refreshViews();
  }, [refreshViews]);

  // ----- 候选人查询参数（合并 URL + 默认值） -----
  const candidateParams = useMemo(
    () => urlStateToCandidateParams(urlState),
    [urlState],
  );

  const candidatesQuery = useCandidates(jobId, candidateParams);

  // ----- URL 更新 helper（统一 replace，不污染历史） -----
  const updateUrl = useCallback(
    (updates: Record<string, string | number | null | undefined>) => {
      const next = new URLSearchParams(searchParams.toString());
      for (const [k, v] of Object.entries(updates)) {
        if (v === null || v === undefined || v === "") {
          next.delete(k);
        } else {
          next.set(k, String(v));
        }
      }
      router.replace(`?${next.toString()}`, { scroll: false });
    },
    [router, searchParams],
  );

  // ----- 筛选表单：从 URL 派生 -----
  const filterForm = useMemo<FilterFormState>(
    () => paramsToFormState(candidateParams),
    [candidateParams],
  );

  const handleFiltersChange = useCallback(
    (next: FilterFormState) => {
      const params = formStateToParams(next);
      updateUrl({
        ...params,
        // 切换筛选时重置回第一页（避免空白页）
        page: 1,
      });
    },
    [updateUrl],
  );

  const handleFiltersSubmit = useCallback(() => {
    // 筛选已在 onChange 即时写入 URL；submit 仅触发 refetch（数据已自动更新）
    // 此处保留语义：强制从第一页重新拉取
    updateUrl({ page: 1 });
  }, [updateUrl]);

  const handleFiltersReset = useCallback(() => {
    const next = defaultFilterForm();
    updateUrl({
      skill: next.skill,
      education: next.education,
      source: next.source,
      min_score: next.min_score,
      max_score: next.max_score,
      min_years: next.min_years,
      max_years: next.max_years,
      sort_by: next.sort_by,
      sort_order: next.sort_order,
      page: 1,
    });
  }, [updateUrl]);

  // ----- 排序（受控） -----
  const sorting = useMemo(
    () => paramsToSorting(urlState.sort_by, urlState.sort_order),
    [urlState.sort_by, urlState.sort_order],
  );

  const handleSortingChange = useCallback(
    (next: ReturnType<typeof paramsToSorting>) => {
      const { sort_by, sort_order } = sortingToParams(next);
      updateUrl({ sort_by, sort_order });
    },
    [updateUrl],
  );

  // ----- 分组切换 -----
  const handleGroupChange = useCallback(
    (group: string) => {
      updateUrl({ group, page: 1 });
    },
    [updateUrl],
  );

  // ----- 分页 -----
  const handlePageChange = useCallback(
    (page: number) => updateUrl({ page }),
    [updateUrl],
  );

  const handlePageSizeChange = useCallback(
    (page_size: number) => {
      if (typeof window !== "undefined") {
        window.localStorage.setItem(
          PAGE_SIZE_OPTIONS_KEY,
          String(page_size),
        );
      }
      updateUrl({ page_size, page: 1 });
    },
    [updateUrl],
  );

  // ----- 密度（持久化到 localStorage） -----
  const handleDensityChange = useCallback((next: Density) => {
    setDensity(next);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(DENSITY_KEY, next);
    }
  }, []);

  // ----- 打开候选人详情（任务 24 页面占位） -----
  const handleOpenCandidate = useCallback(
    (candidate: CandidateListItem) => {
      router.push(`/jobs/${jobId}/candidates/${candidate.id}`);
    },
    [router, jobId],
  );

  // ----- 保存视图 -----
  const handleSaveView = useCallback(() => {
    if (!savingViewName.trim()) return;
    const view = snapshotView(savingViewName.trim(), {
      filters: filterForm,
      group: urlState.group,
      columns,
      density,
      page_size: urlState.page_size,
    });
    upsertSavedView(jobId, view);
    setSavingViewName("");
    setShowSaveRow(false);
    refreshViews();
  }, [
    savingViewName,
    filterForm,
    urlState.group,
    urlState.page_size,
    columns,
    density,
    jobId,
    refreshViews,
  ]);

  const handleApplyView = useCallback(
    (view: SavedView) => {
      // 应用筛选到 URL
      updateUrl({
        ...formStateToParams(view.filters),
        group: view.group,
        page_size: view.page_size,
        page: 1,
      });
      // 应用列配置 + 密度（本地 state）
      setColumns(view.columns);
      setDensity(view.density);
      if (typeof window !== "undefined") {
        window.localStorage.setItem(DENSITY_KEY, view.density);
      }
    },
    [updateUrl],
  );

  const handleDeleteView = useCallback(
    (view: SavedView) => {
      deleteSavedView(jobId, view.id);
      refreshViews();
    },
    [jobId, refreshViews],
  );

  // ----- SSE 进度订阅 -----
  const [pipelineMessage, setPipelineMessage] = useState<string>("");
  const [pipelineTriggering, setPipelineTriggering] = useState<boolean>(false);

  const handlePipelineProgress = useCallback(() => {
    setPipelineMessage("筛选进行中...");
  }, []);

  const handlePipelineDone = useCallback(
    (summary?: {
      total: number;
      passed: number;
      disqualified: number;
      failed: number;
    }) => {
      if (!summary) {
        setPipelineMessage("筛选完成");
        return;
      }
      setPipelineMessage(
        `筛选完成：通过 ${summary.passed}，淘汰 ${summary.disqualified}` +
          (summary.failed ? `，失败 ${summary.failed}` : ""),
      );
      // 触发列表刷新
      candidatesQuery.refetch();
    },
    [candidatesQuery],
  );

  const sse = usePipelineSSE({
    jobId,
    onProgress: handlePipelineProgress,
    onDone: handlePipelineDone,
  });

  const handleTriggerPipeline = useCallback(async () => {
    setPipelineTriggering(true);
    setPipelineMessage("正在触发筛选...");
    try {
      // 简化：触发当前 job 全量未评分候选人
      // 实际生产中需要根据 candidateParams 取 candidate_ids
      const candidateIds =
        candidatesQuery.data?.items
          ?.filter((c) => c.screening_id === null)
          .map((c) => c.id) ?? [];
      if (candidateIds.length === 0) {
        setPipelineMessage("当前列表无需筛选的候选人");
        return;
      }
      const { run_id, total } = await triggerPipelineApi(
        jobId,
        candidateIds,
      );
      setPipelineMessage(`已启动（${total} 位候选人）`);
      sse.connect(run_id);
    } catch (err) {
      setPipelineMessage(
        err instanceof Error ? err.message : "触发失败",
      );
    } finally {
      setPipelineTriggering(false);
    }
  }, [candidatesQuery.data, jobId, sse]);

  // ----- 渲染 -----
  if (jobLoading) {
    return <div className="p-8 text-sm">加载职位中...</div>;
  }

  if (!job) {
    return (
      <div className="p-8">
        <Alert variant="destructive">
          <AlertTitle>职位不存在</AlertTitle>
          <AlertDescription>该职位可能已被删除或无权访问</AlertDescription>
        </Alert>
        <Link
          href="/jobs"
          className="mt-4 inline-block text-primary underline"
        >
          返回列表
        </Link>
      </div>
    );
  }

  const groupCounts = candidatesQuery.data?.group_counts ?? {
    passed: 0,
    disqualified: 0,
    pending: 0,
  };
  const total = candidatesQuery.data?.total ?? 0;

  return (
    <div className="mx-auto max-w-[1400px] space-y-4 p-6">
      {/* 顶部：返回 + 标题 + 操作按钮 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Link
            href={`/jobs/${jobId}`}
            className="text-sm text-muted-foreground hover:underline"
          >
            ← 返回职位
          </Link>
          <h1 className="mt-1 text-2xl font-bold">
            {job.title} - 候选人
          </h1>
          <p className="text-xs text-muted-foreground">
            v{job.current_version}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            onClick={handleTriggerPipeline}
            disabled={pipelineTriggering || sse.isConnected}
          >
            {pipelineTriggering || sse.isConnected
              ? "筛选进行中..."
              : "触发筛选"}
          </Button>
          <ExportButton jobId={jobId} />
        </div>
      </div>

      {/* SSE 进度提示 */}
      {pipelineMessage && (
        <div className="rounded-md border bg-blue-50 p-2 text-sm text-blue-800 dark:bg-blue-950/40 dark:text-blue-200">
          {pipelineMessage}
          {sse.isConnected && (
            <span className="ml-2 inline-block h-2 w-2 animate-pulse rounded-full bg-blue-500" />
          )}
        </div>
      )}

      {/* 分组切换 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Tabs
          value={urlState.group}
          onChange={handleGroupChange}
          options={[
            { value: "all", label: "全部", count: total },
            {
              value: "passed",
              label: "通过",
              count: groupCounts.passed,
            },
            {
              value: "disqualified",
              label: "淘汰",
              count: groupCounts.disqualified,
            },
            {
              value: "pending",
              label: "待复核",
              count: groupCounts.pending,
            },
          ]}
        />

        {/* 保存视图入口 */}
        <div className="flex flex-wrap items-center gap-2">
          {savedViews.length > 0 && (
            <select
              className="h-8 rounded-md border bg-background px-2 text-xs"
              onChange={(e) => {
                const v = savedViews.find((sv) => sv.id === e.target.value);
                if (v) handleApplyView(v);
              }}
              defaultValue=""
              aria-label="应用保存视图"
            >
              <option value="" disabled>
                应用保存的视图...
              </option>
              {savedViews.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name}
                </option>
              ))}
            </select>
          )}
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setShowSaveRow((s) => !s)}
          >
            保存当前视图
          </Button>
        </div>
      </div>

      {/* 保存视图输入框 */}
      {showSaveRow && (
        <div className="flex items-center gap-2 rounded-md border bg-muted/30 p-2">
          <input
            type="text"
            placeholder="视图名称（如：高潜工程师）"
            value={savingViewName}
            onChange={(e) => setSavingViewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleSaveView();
              if (e.key === "Escape") {
                setShowSaveRow(false);
                setSavingViewName("");
              }
            }}
            className="h-8 flex-1 rounded-md border bg-background px-2 text-sm"
            autoFocus
          />
          <Button
            type="button"
            size="sm"
            onClick={handleSaveView}
            disabled={!savingViewName.trim()}
          >
            保存
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => {
              setShowSaveRow(false);
              setSavingViewName("");
            }}
          >
            取消
          </Button>
        </div>
      )}

      {/* 已保存视图列表（chip） */}
      {savedViews.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <span className="text-muted-foreground">已保存：</span>
          {savedViews.map((v) => (
            <span
              key={v.id}
              className="inline-flex items-center gap-1 rounded-full border bg-card px-2 py-0.5"
            >
              <button
                type="button"
                onClick={() => handleApplyView(v)}
                className="hover:underline"
              >
                {v.name}
              </button>
              <button
                type="button"
                onClick={() => handleDeleteView(v)}
                aria-label={`删除视图 ${v.name}`}
                className="text-muted-foreground hover:text-destructive"
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}

      {/* 筛选栏 */}
      <CandidateFilters
        value={filterForm}
        onChange={handleFiltersChange}
        onSubmit={handleFiltersSubmit}
        onReset={handleFiltersReset}
      />

      {/* 表格 */}
      <CandidateTable
        data={candidatesQuery.data?.items ?? []}
        isLoading={candidatesQuery.isLoading}
        error={candidatesQuery.error ?? null}
        sorting={sorting}
        onSortingChange={handleSortingChange}
        columns={columns}
        onColumnsChange={setColumns}
        density={density}
        onDensityChange={handleDensityChange}
        pagination={{
          page: urlState.page,
          pageSize: urlState.page_size,
          total,
        }}
        onPageChange={handlePageChange}
        onPageSizeChange={handlePageSizeChange}
        onOpenCandidate={handleOpenCandidate}
        emptyHint={
          urlState.group === "pending"
            ? "没有待筛选的候选人"
            : "暂无候选人，请上传简历或调整筛选条件"
        }
      />
    </div>
  );
}
