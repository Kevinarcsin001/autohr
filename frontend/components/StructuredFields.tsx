"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { CandidateStructure } from "@/lib/api/candidateDetail";
import { cn } from "@/lib/utils";

/**
 * 结构化字段展示（任务 24）。
 *
 * 渲染：
 * - 基础：name / phone / email / education / years / current_company
 * - 技能：skills[] 标签
 * - 工作经历：work_history[] 列表
 * - confidence 徽标（绿/黄/红三档）
 */

interface StructuredFieldsProps {
  structure: CandidateStructure | null;
  className?: string;
}

export function StructuredFields({
  structure,
  className,
}: StructuredFieldsProps) {
  if (!structure) {
    return (
      <Card className={className}>
        <CardHeader>
          <CardTitle className="text-base">结构化字段</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            暂无结构化抽取数据
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="text-base">结构化字段</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <FieldRow label="姓名">
          <ValueWithConfidence
            value={structure.name}
            confidence={structure.name_confidence}
          />
        </FieldRow>
        <FieldRow label="电话">
          <ValueWithConfidence
            value={structure.phone}
            confidence={structure.phone_confidence}
          />
        </FieldRow>
        <FieldRow label="邮箱">
          <ValueWithConfidence
            value={structure.email}
            confidence={structure.email_confidence}
          />
        </FieldRow>
        <FieldRow label="学历">
          <ValueWithConfidence
            value={educationLabel(structure.education)}
            confidence={structure.education_confidence}
          />
        </FieldRow>
        <FieldRow label="工作年限">
          <ValueWithConfidence
            value={
              structure.years_of_experience != null
                ? `${structure.years_of_experience} 年`
                : null
            }
            confidence={structure.years_of_experience_confidence}
          />
        </FieldRow>
        <FieldRow label="当前公司">
          <ValueWithConfidence
            value={structure.current_company}
            confidence={structure.current_company_confidence}
          />
        </FieldRow>
        <FieldRow label="期望薪资">
          <ValueWithConfidence
            value={structure.expected_salary}
            confidence={structure.expected_salary_confidence}
          />
        </FieldRow>

        <div>
          <div className="mb-2 flex items-center justify-between">
            <span className="text-muted-foreground">技能</span>
            <ConfidenceBadge
              confidence={structure.skills_confidence}
              small
            />
          </div>
          {structure.skills.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {structure.skills.map((s, i) => (
                <Badge key={`${s}-${i}`} variant="secondary">
                  {s}
                </Badge>
              ))}
            </div>
          ) : (
            <span className="text-muted-foreground">—</span>
          )}
        </div>

        <div>
          <div className="mb-2 flex items-center justify-between">
            <span className="text-muted-foreground">工作经历</span>
            <ConfidenceBadge
              confidence={structure.work_history_confidence}
              small
            />
          </div>
          {structure.work_history.length > 0 ? (
            <ul className="space-y-2">
              {structure.work_history.map((wh, i) => (
                <li
                  key={i}
                  className="border-l-2 border-muted pl-3 text-sm"
                >
                  <div className="font-medium">
                    {wh.company || "—"}
                    {wh.title ? ` · ${wh.title}` : ""}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {wh.start_date || "?"}
                    {" — "}
                    {wh.end_date || "present"}
                  </div>
                  {wh.description && (
                    <p className="mt-1 text-xs text-muted-foreground">
                      {wh.description}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <span className="text-muted-foreground">—</span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// ============================================================================
// 内部
// ============================================================================

function FieldRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <span className="text-right font-medium">{children}</span>
    </div>
  );
}

function ValueWithConfidence({
  value,
  confidence,
}: {
  value: string | null;
  confidence: number;
}) {
  if (!value) {
    return <span className="text-muted-foreground">—</span>;
  }
  return (
    <span className="inline-flex items-center gap-2">
      {value}
      <ConfidenceBadge confidence={confidence} small />
    </span>
  );
}

function ConfidenceBadge({
  confidence,
  small,
  className,
}: {
  confidence: number;
  small?: boolean;
  className?: string;
}) {
  const pct = Math.round(confidence * 100);
  const variant =
    confidence >= 0.8 ? "success" : confidence >= 0.5 ? "warning" : "destructive";
  const label = pct >= 80 ? "高" : pct >= 50 ? "中" : "低";
  return (
    <Badge
      variant={variant}
      className={cn(small && "px-1 py-0 text-[10px]", className)}
      title={`置信度 ${pct}%`}
    >
      {label} {pct}%
    </Badge>
  );
}

function educationLabel(
  edu: CandidateStructure["education"],
): string | null {
  if (!edu) return null;
  const map: Record<string, string> = {
    high_school: "高中",
    bachelor: "本科",
    master: "硕士",
    phd: "博士",
    other: "其他",
  };
  return map[edu] ?? edu;
}
