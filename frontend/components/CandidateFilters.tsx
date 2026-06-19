"use client";

import { useCallback, useEffect, useRef } from "react";
import { Filter, RotateCcw, Search } from "lucide-react";

import {
  type CandidateListParams,
  type CandidateSource,
  type EducationLevel,
  type SortBy,
  type SortOrder,
} from "@/lib/api/candidates";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { cn } from "@/lib/utils";

// ============================================================================
// 常量
// ============================================================================

export const EDUCATION_OPTIONS: Array<{
  value: EducationLevel;
  label: string;
}> = [
  { value: "high_school", label: "高中" },
  { value: "bachelor", label: "本科" },
  { value: "master", label: "硕士" },
  { value: "phd", label: "博士" },
  { value: "other", label: "其他" },
];

export const SOURCE_OPTIONS: Array<{
  value: CandidateSource;
  label: string;
}> = [
  { value: "upload", label: "上传" },
  { value: "platform", label: "平台导入" },
  { value: "email", label: "邮件抓取" },
];

export const SORT_BY_OPTIONS: Array<{ value: SortBy; label: string }> = [
  { value: "total", label: "总分" },
  { value: "skill", label: "技能" },
  { value: "experience", label: "经验" },
  { value: "education", label: "学历分" },
  { value: "stability", label: "稳定性" },
  { value: "potential", label: "潜力" },
  { value: "name", label: "姓名" },
];

// ============================================================================
// 类型
// ============================================================================

/**
 * 筛选栏本地态：number 字段用空串占位（便于输入中途状态）。
 * 转换为 CandidateListParams 时空串 → undefined。
 */
export interface FilterFormState {
  skill: string;
  education: EducationLevel | "";
  source: CandidateSource | "";
  min_score: string;
  max_score: string;
  min_years: string;
  max_years: string;
  sort_by: SortBy;
  sort_order: SortOrder;
}

export function paramsToFormState(
  params: CandidateListParams,
): FilterFormState {
  return {
    skill: params.skill ?? "",
    education: params.education ?? "",
    source: params.source ?? "",
    min_score:
      params.min_score !== undefined ? String(params.min_score) : "",
    max_score:
      params.max_score !== undefined ? String(params.max_score) : "",
    min_years:
      params.min_years !== undefined ? String(params.min_years) : "",
    max_years:
      params.max_years !== undefined ? String(params.max_years) : "",
    sort_by: params.sort_by ?? "total",
    sort_order: params.sort_order ?? "desc",
  };
}

export function formStateToParams(
  form: FilterFormState,
): Pick<
  CandidateListParams,
  | "skill"
  | "education"
  | "source"
  | "min_score"
  | "max_score"
  | "min_years"
  | "max_years"
  | "sort_by"
  | "sort_order"
> {
  return {
    skill: form.skill.trim() || undefined,
    education: form.education || undefined,
    source: form.source || undefined,
    min_score: form.min_score ? Number(form.min_score) : undefined,
    max_score: form.max_score ? Number(form.max_score) : undefined,
    min_years: form.min_years ? Number(form.min_years) : undefined,
    max_years: form.max_years ? Number(form.max_years) : undefined,
    sort_by: form.sort_by,
    sort_order: form.sort_order,
  };
}

// ============================================================================
// 组件
// ============================================================================

interface CandidateFiltersProps {
  value: FilterFormState;
  onChange: (next: FilterFormState) => void;
  onReset: () => void;
  /** 提交（按 Enter 或点击应用） */
  onSubmit: () => void;
  className?: string;
  /** 默认折叠状态；展开后显示更多筛选项 */
  defaultCollapsed?: boolean;
}

const DEFAULT_FORM: FilterFormState = {
  skill: "",
  education: "",
  source: "",
  min_score: "",
  max_score: "",
  min_years: "",
  max_years: "",
  sort_by: "total",
  sort_order: "desc",
};

/**
 * 候选人筛选栏（任务 23）。
 *
 * 设计：
 * - 受控组件：父组件持有 FilterFormState，避免 SSR 与 URL 不同步
 * - 三段布局：第一行（搜索 + 学历 + 来源 + 重置）；第二行（评分区间 + 年限 + 排序）
 * - Enter 即提交；点击"应用筛选"提交
 * - 客户端校验：min ≤ max；否则提示
 */
export function CandidateFilters({
  value,
  onChange,
  onReset,
  onSubmit,
  className,
}: CandidateFiltersProps) {
  const formRef = useRef<HTMLFormElement>(null);

  // 局部错误（min/max 倒置等）
  const rangeError = validateRanges(value);

  const update = useCallback(
    <K extends keyof FilterFormState>(key: K, v: FilterFormState[K]) => {
      onChange({ ...value, [key]: v });
    },
    [onChange, value],
  );

  // Cmd/Ctrl + Enter 提交（高级用户）
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        if (!rangeError) onSubmit();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onSubmit, rangeError]);

  return (
    <form
      ref={formRef}
      className={cn(
        "rounded-lg border bg-card p-4 text-card-foreground shadow-sm",
        className,
      )}
      onSubmit={(e) => {
        e.preventDefault();
        if (!rangeError) onSubmit();
      }}
    >
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Filter className="h-4 w-4" />
          筛选条件
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => onReset()}
          className="text-muted-foreground"
        >
          <RotateCcw className="mr-1 h-3 w-3" />
          重置
        </Button>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
        {/* 技能搜索 */}
        <div className="space-y-1.5">
          <Label htmlFor="filter-skill">技能</Label>
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              id="filter-skill"
              placeholder="如 Python、K8s"
              value={value.skill}
              onChange={(e) => update("skill", e.target.value)}
              className="pl-9"
            />
          </div>
        </div>

        {/* 学历 */}
        <div className="space-y-1.5">
          <Label htmlFor="filter-education">学历</Label>
          <Select
            id="filter-education"
            value={value.education}
            onChange={(e) =>
              update("education", e.target.value as EducationLevel | "")
            }
          >
            <option value="">不限</option>
            {EDUCATION_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </div>

        {/* 来源 */}
        <div className="space-y-1.5">
          <Label htmlFor="filter-source">来源</Label>
          <Select
            id="filter-source"
            value={value.source}
            onChange={(e) =>
              update("source", e.target.value as CandidateSource | "")
            }
          >
            <option value="">不限</option>
            {SOURCE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </div>

        {/* 评分区间 */}
        <div className="space-y-1.5">
          <Label>评分区间</Label>
          <div className="flex items-center gap-2">
            <Input
              type="number"
              inputMode="numeric"
              min={0}
              max={100}
              placeholder="0"
              value={value.min_score}
              onChange={(e) => update("min_score", e.target.value)}
              className="w-full"
              aria-label="最低分"
            />
            <span className="text-muted-foreground">—</span>
            <Input
              type="number"
              inputMode="numeric"
              min={0}
              max={100}
              placeholder="100"
              value={value.max_score}
              onChange={(e) => update("max_score", e.target.value)}
              className="w-full"
              aria-label="最高分"
            />
          </div>
        </div>

        {/* 工作年限 */}
        <div className="space-y-1.5">
          <Label>工作年限</Label>
          <div className="flex items-center gap-2">
            <Input
              type="number"
              inputMode="numeric"
              min={0}
              max={50}
              placeholder="0"
              value={value.min_years}
              onChange={(e) => update("min_years", e.target.value)}
              className="w-full"
              aria-label="最低年限"
            />
            <span className="text-muted-foreground">—</span>
            <Input
              type="number"
              inputMode="numeric"
              min={0}
              max={50}
              placeholder="50"
              value={value.max_years}
              onChange={(e) => update("max_years", e.target.value)}
              className="w-full"
              aria-label="最高年限"
            />
          </div>
        </div>

        {/* 排序 */}
        <div className="space-y-1.5">
          <Label htmlFor="filter-sort-by">排序</Label>
          <div className="flex gap-2">
            <Select
              id="filter-sort-by"
              value={value.sort_by}
              onChange={(e) => update("sort_by", e.target.value as SortBy)}
              className="flex-1"
            >
              {SORT_BY_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
            <Select
              aria-label="排序方向"
              value={value.sort_order}
              onChange={(e) =>
                update("sort_order", e.target.value as SortOrder)
              }
              className="w-24"
            >
              <option value="desc">降序</option>
              <option value="asc">升序</option>
            </Select>
          </div>
        </div>
      </div>

      {rangeError && (
        <p
          role="alert"
          className="mt-3 text-xs text-destructive"
        >
          {rangeError}
        </p>
      )}

      <div className="mt-3 flex justify-end">
        <Button
          type="submit"
          size="sm"
          disabled={Boolean(rangeError)}
        >
          应用筛选
          <span className="ml-1 text-xs text-muted-foreground">
            （⌘↵）
          </span>
        </Button>
      </div>
    </form>
  );
}

/**
 * 校验 min ≤ max（评分 / 年限）。
 * 返回错误信息；无错返回 null。
 */
function validateRanges(form: FilterFormState): string | null {
  const { min_score, max_score, min_years, max_years } = form;
  if (
    min_score &&
    max_score &&
    Number(min_score) > Number(max_score)
  ) {
    return "最低分不能大于最高分";
  }
  if (
    min_years &&
    max_years &&
    Number(min_years) > Number(max_years)
  ) {
    return "最低年限不能大于最高年限";
  }
  return null;
}

/**
 * 默认筛选态（用于重置）。
 */
export function defaultFilterForm(): FilterFormState {
  return { ...DEFAULT_FORM };
}
