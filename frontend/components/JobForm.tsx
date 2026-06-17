"use client";

import { useState, type FormEvent } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  type EducationLevel,
  type HardRequirements,
  type JobStatus,
  type JobUpdatePayload,
} from "@/lib/api/jobs";

// ============================================================================
// 常量
// ============================================================================

const STATUS_OPTIONS: JobStatus[] = ["draft", "active", "closed"];
const STATUS_LABEL: Record<JobStatus, string> = {
  draft: "草稿",
  active: "招聘中",
  closed: "已关闭",
};

const EDUCATION_OPTIONS: EducationLevel[] = [
  "high_school",
  "bachelor",
  "master",
  "phd",
];
const EDUCATION_LABEL: Record<EducationLevel, string> = {
  high_school: "高中",
  bachelor: "本科",
  master: "硕士",
  phd: "博士",
};

// ============================================================================
// 表单状态
// ============================================================================

export interface JobFormValues {
  title: string;
  jd_text: string;
  status: JobStatus;
  hard_requirements: HardRequirements;
}

export interface JobFormProps {
  initial?: Partial<JobFormValues>;
  submitLabel?: string;
  onSubmit: (values: JobFormValues) => Promise<void>;
  onError?: (message: string) => void;
}

// ============================================================================
// 组件
// ============================================================================

/**
 * 职位表单（创建 / 编辑共用）。
 *
 * - JD 文本：textarea（markdown 源码），右侧显示字符数。
 * - 硬性条件：子表单（最低学历 / 最低年限 / 必备技能 / 排除公司）。
 * - 必备技能 / 排除公司：每行一项的文本框（粘贴时用换行分隔）。
 */
export function JobForm({
  initial,
  submitLabel = "保存",
  onSubmit,
  onError,
}: JobFormProps) {
  const [title, setTitle] = useState(initial?.title ?? "");
  const [jdText, setJdText] = useState(initial?.jd_text ?? "");
  const [status, setStatus] = useState<JobStatus>(initial?.status ?? "draft");
  const [minEducation, setMinEducation] = useState<EducationLevel | "">(
    (initial?.hard_requirements?.min_education as EducationLevel) ?? "",
  );
  const [minYears, setMinYears] = useState<string>(
    initial?.hard_requirements?.min_years?.toString() ?? "",
  );
  const [requiredSkillsText, setRequiredSkillsText] = useState<string>(
    (initial?.hard_requirements?.required_skills ?? []).join("\n"),
  );
  const [excludedCompaniesText, setExcludedCompaniesText] = useState<string>(
    (initial?.hard_requirements?.excluded_companies ?? []).join("\n"),
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function linesToList(text: string): string[] {
    return text
      .split(/\r?\n/)
      .map((s) => s.trim())
      .filter((s) => s.length > 0);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!title.trim()) {
      setError("请填写职位标题");
      return;
    }
    if (!jdText.trim()) {
      setError("请填写职位描述（JD）");
      return;
    }
    const minYearsNum = minYears.trim()
      ? Number(minYears.trim())
      : null;
    if (minYearsNum !== null) {
      if (Number.isNaN(minYearsNum) || minYearsNum < 0 || minYearsNum > 50) {
        setError("最低工作年限必须是 0-50 之间的整数");
        return;
      }
    }

    const values: JobFormValues = {
      title: title.trim(),
      jd_text: jdText,
      status,
      hard_requirements: {
        min_education: minEducation || null,
        min_years: minYearsNum,
        required_skills: linesToList(requiredSkillsText),
        excluded_companies: linesToList(excludedCompaniesText),
      },
    };
    setSubmitting(true);
    try {
      await onSubmit(values);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "提交失败";
      setError(msg);
      onError?.(msg);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      {error && (
        <Alert variant="destructive">
          <AlertTitle>提交失败</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="space-y-2">
        <Label htmlFor="title">职位标题 *</Label>
        <Input
          id="title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          maxLength={200}
          required
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="status">状态</Label>
        <select
          id="status"
          value={status}
          onChange={(e) => setStatus(e.target.value as JobStatus)}
          className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
        >
          {STATUS_OPTIONS.map((s) => (
            <option key={s} value={s}>
              {STATUS_LABEL[s]}
            </option>
          ))}
        </select>
      </div>

      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label htmlFor="jd_text">职位描述（Markdown）*</Label>
          <span className="text-xs text-muted-foreground">
            {jdText.length} 字符
          </span>
        </div>
        <textarea
          id="jd_text"
          value={jdText}
          onChange={(e) => setJdText(e.target.value)}
          rows={16}
          required
          className="flex w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          placeholder={
            "示例：\n## 岗位职责\n- 负责...\n\n## 任职要求\n- 5 年以上后端开发经验..."
          }
        />
      </div>

      <fieldset className="space-y-4 rounded-md border p-4">
        <legend className="px-1 text-sm font-medium">硬性条件（可选）</legend>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="min_education">最低学历</Label>
            <select
              id="min_education"
              value={minEducation}
              onChange={(e) =>
                setMinEducation(e.target.value as EducationLevel | "")
              }
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            >
              <option value="">不限</option>
              {EDUCATION_OPTIONS.map((e) => (
                <option key={e} value={e}>
                  {EDUCATION_LABEL[e]}
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="min_years">最低工作年限</Label>
            <Input
              id="min_years"
              type="number"
              min={0}
              max={50}
              value={minYears}
              onChange={(e) => setMinYears(e.target.value)}
              placeholder="不限"
            />
          </div>
        </div>

        <div className="space-y-2">
          <Label htmlFor="required_skills">必备技能（每行一项）</Label>
          <textarea
            id="required_skills"
            value={requiredSkillsText}
            onChange={(e) => setRequiredSkillsText(e.target.value)}
            rows={5}
            className="flex w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            placeholder={"Python\nFastAPI\nPostgreSQL"}
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="excluded_companies">排除公司（每行一项）</Label>
          <textarea
            id="excluded_companies"
            value={excludedCompaniesText}
            onChange={(e) => setExcludedCompaniesText(e.target.value)}
            rows={3}
            className="flex w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            placeholder={"竞品 A\n竞品 B"}
          />
        </div>
      </fieldset>

      <Button type="submit" disabled={submitting}>
        {submitting ? "保存中..." : submitLabel}
      </Button>
    </form>
  );
}

/** 将表单值转换为 PATCH 后端期望的部分载荷（仅包含已变更字段）。 */
export function diffPayload(
  values: JobFormValues,
  original?: Partial<JobFormValues>,
): JobUpdatePayload {
  const payload: JobUpdatePayload = {};
  if (original?.title !== values.title) payload.title = values.title;
  if (original?.jd_text !== values.jd_text) payload.jd_text = values.jd_text;
  if (original?.status !== values.status) payload.status = values.status;
  // hard_requirements 整体替换（后端语义：未传 = 保持原值，传 = 整体替换）
  const origHard = original?.hard_requirements ?? {};
  if (
    origHard.min_education !== values.hard_requirements.min_education ||
    origHard.min_years !== values.hard_requirements.min_years ||
    origHard.required_skills?.join("|") !==
      values.hard_requirements.required_skills?.join("|") ||
    origHard.excluded_companies?.join("|") !==
      values.hard_requirements.excluded_companies?.join("|")
  ) {
    payload.hard_requirements = values.hard_requirements;
  }
  return payload;
}
