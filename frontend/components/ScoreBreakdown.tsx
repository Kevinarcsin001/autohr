"use client";

import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
} from "recharts";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ScoreOut } from "@/lib/api/candidateDetail";

/**
 * 评分细项（任务 24）：6 维度雷达图 + 数值列表。
 *
 * 数据来源：ScoreOut.skill/experience/education/stability/potential/total
 *
 * 设计（plan）：
 * - 任一维度为 null → 渲染为 0 + 显示"未评"灰标
 * - 雷达图 6 个轴：skill / experience / education / stability / potential / total
 */

interface ScoreBreakdownProps {
  score: ScoreOut | null;
  className?: string;
}

interface AxisDatum {
  axis: string;
  value: number;
  raw: number | null;
  label: string;
}

const AXES: Array<{ key: keyof ScoreOut; label: string }> = [
  { key: "skill", label: "技能" },
  { key: "experience", label: "经验" },
  { key: "education", label: "学历" },
  { key: "stability", label: "稳定性" },
  { key: "potential", label: "潜力" },
  { key: "total", label: "综合" },
];

export function ScoreBreakdown({ score, className }: ScoreBreakdownProps) {
  if (!score) {
    return (
      <Card className={className}>
        <CardHeader>
          <CardTitle className="text-base">评分细项</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            尚未评分
          </p>
        </CardContent>
      </Card>
    );
  }

  const data: AxisDatum[] = AXES.map(({ key, label }) => {
    const raw = score[key] as number | null;
    return {
      axis: label,
      label,
      raw,
      value: raw ?? 0,
    };
  });

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="text-base">
          评分细项
          <span className="ml-2 text-sm font-normal text-muted-foreground">
            模型：{score.model_used ?? "—"}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <RadarChart data={data} outerRadius={100}>
                <PolarGrid />
                <PolarAngleAxis dataKey="axis" />
                <PolarRadiusAxis
                  domain={[0, 100]}
                  tickCount={6}
                  tick={{ fontSize: 10 }}
                />
                <Radar
                  name="评分"
                  dataKey="value"
                  stroke="hsl(var(--primary))"
                  fill="hsl(var(--primary))"
                  fillOpacity={0.4}
                />
              </RadarChart>
            </ResponsiveContainer>
          </div>

          <div className="space-y-2">
            {data.map((d) => (
              <div
                key={d.axis}
                className="flex items-center justify-between rounded-md border px-3 py-2"
              >
                <span className="text-sm">{d.label}</span>
                <div className="flex items-center gap-2">
                  {d.raw === null ? (
                    <Badge variant="outline">未评</Badge>
                  ) : (
                    <Badge
                      variant={
                        d.raw >= 80
                          ? "success"
                          : d.raw >= 60
                            ? "warning"
                            : "destructive"
                      }
                    >
                      {d.raw}
                    </Badge>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
