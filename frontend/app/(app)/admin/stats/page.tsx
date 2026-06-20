"use client";

import { useState } from "react";
import Link from "next/link";

import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { useAdminStats } from "@/hooks/useAdmin";
import { useAuthStore } from "@/stores/authStore";
import type {
  StatsByDimension,
  StatsRange,
  StatsResponse,
  StatsSummary,
  StatsTimeSeries,
} from "@/lib/api/admin";

const RANGES: StatsRange[] = ["7d", "30d"];

export default function StatsPage() {
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === "admin";
  const [range, setRange] = useState<StatsRange>("7d");

  if (!isAdmin) {
    return (
      <div className="p-8">
        <Alert variant="destructive">
          <AlertTitle>权限不足</AlertTitle>
          <AlertDescription>
            仅团队管理员可访问 LLM 调用统计页面。
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-8">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">LLM 调用统计</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            按时间 / scope / adapter 聚合；每分钟自动刷新。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-xs text-muted-foreground" htmlFor="range">
            范围
          </label>
          <Select
            id="range"
            value={range}
            onChange={(e) => setRange(e.target.value as StatsRange)}
            className="w-32"
          >
            {RANGES.map((r) => (
              <option key={r} value={r}>
                {r === "7d" ? "最近 7 天" : "最近 30 天"}
              </option>
            ))}
          </Select>
        </div>
      </header>

      <StatsContent range={range} />

      <div>
        <Link href="/admin" className="text-sm text-primary hover:underline">
          ← 返回管理首页
        </Link>
      </div>
    </div>
  );
}

function StatsContent({ range }: { range: StatsRange }) {
  const { data, isLoading, isError } = useAdminStats(range);

  if (isLoading) {
    return (
      <Card>
        <CardContent className="py-8 text-sm text-muted-foreground">
          加载中…
        </CardContent>
      </Card>
    );
  }
  if (isError || !data) {
    return (
      <Card>
        <CardContent className="py-8 text-sm text-red-600">
          无法加载统计数据。
        </CardContent>
      </Card>
    );
  }

  return (
    <>
      <SummaryGrid summary={data.summary} />
      <TimeSeriesCard series={data.time_series} />
      <div className="grid gap-6 md:grid-cols-2">
        <DimensionCard title="按用途" dim={data.by_scope} />
        <DimensionCard title="按适配器" dim={data.by_adapter} />
      </div>
    </>
  );
}

// ============================================================================
// 概要
// ============================================================================

function SummaryGrid({ summary }: { summary: StatsSummary }) {
  const items = [
    {
      label: "总调用",
      value: summary.total_calls.toLocaleString(),
      hint: `成功 ${summary.success_count} / 失败 ${summary.failed_count}`,
    },
    {
      label: "成功率",
      value: `${(summary.success_rate * 100).toFixed(1)}%`,
      hint: summary.total_calls > 0 ? null : "暂无数据",
    },
    {
      label: "Token 入",
      value: summary.total_tokens_in.toLocaleString(),
      hint: null,
    },
    {
      label: "Token 出",
      value: summary.total_tokens_out.toLocaleString(),
      hint: null,
    },
    {
      label: "总成本 (¥)",
      value: summary.total_cost_cny.toFixed(4),
      hint: null,
    },
    {
      label: "P50 延迟",
      value: summary.p50_latency_ms != null ? `${summary.p50_latency_ms} ms` : "—",
      hint: null,
    },
    {
      label: "P95 延迟",
      value: summary.p95_latency_ms != null ? `${summary.p95_latency_ms} ms` : "—",
      hint: null,
    },
    {
      label: "P99 延迟",
      value: summary.p99_latency_ms != null ? `${summary.p99_latency_ms} ms` : "—",
      hint: null,
    },
  ];

  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      {items.map((it) => (
        <Card key={it.label}>
          <CardContent className="py-4">
            <div className="text-xs text-muted-foreground">{it.label}</div>
            <div className="mt-1 text-xl font-semibold tabular-nums">
              {it.value}
            </div>
            {it.hint && (
              <div className="mt-1 text-[11px] text-muted-foreground">
                {it.hint}
              </div>
            )}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

// ============================================================================
// 时间序列
// ============================================================================

function TimeSeriesCard({ series }: { series: StatsTimeSeries }) {
  const points = series.points.map((p) => ({
    ...p,
    label: new Date(p.timestamp).toLocaleDateString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
    }),
  }));

  return (
    <Card>
      <CardHeader>
        <CardTitle>时间序列</CardTitle>
        <CardDescription>
          按天聚合 · {series.granularity === "day" ? "日" : "时"}粒度 ·{" "}
          {points.length} 个数据点
        </CardDescription>
      </CardHeader>
      <CardContent>
        {points.length === 0 ? (
          <div className="py-8 text-sm text-muted-foreground">暂无数据</div>
        ) : (
          <ResponsiveContainer width="100%" height={260}>
            <AreaChart data={points} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="colorTotal" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="colorFail" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#ef4444" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#ef4444" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
              <XAxis dataKey="label" tick={{ fontSize: 12 }} />
              <YAxis tick={{ fontSize: 12 }} allowDecimals={false} />
              <Tooltip
                contentStyle={{ fontSize: 12, borderRadius: 6 }}
                formatter={(value: number, name: string) => [
                  value,
                  name === "total_calls" ? "总数" : name === "success_count" ? "成功" : "失败",
                ]}
              />
              <Area
                type="monotone"
                dataKey="total_calls"
                stroke="#3b82f6"
                fill="url(#colorTotal)"
              />
              <Area
                type="monotone"
                dataKey="failed_count"
                stroke="#ef4444"
                fill="url(#colorFail)"
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}

// ============================================================================
// 维度卡片
// ============================================================================

function DimensionCard({ title, dim }: { title: string; dim: StatsByDimension }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>共 {dim.items.length} 项</CardDescription>
      </CardHeader>
      <CardContent>
        {dim.items.length === 0 ? (
          <div className="py-6 text-sm text-muted-foreground">暂无数据</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b text-left text-xs text-muted-foreground">
                <tr>
                  <th className="py-2 pr-3">Key</th>
                  <th className="py-2 pr-3 text-right">调用</th>
                  <th className="py-2 pr-3 text-right">成功率</th>
                  <th className="py-2 pr-3 text-right">Token 入</th>
                  <th className="py-2 pr-3 text-right">成本 ¥</th>
                </tr>
              </thead>
              <tbody>
                {dim.items.map((it) => {
                  const rate =
                    it.total_calls > 0 ? it.success_count / it.total_calls : 0;
                  return (
                    <tr key={it.key} className="border-b last:border-0">
                      <td className="py-2 pr-3 font-mono">{it.key}</td>
                      <td className="py-2 pr-3 text-right tabular-nums">
                        {it.total_calls}
                      </td>
                      <td className="py-2 pr-3 text-right tabular-nums">
                        {(rate * 100).toFixed(1)}%
                      </td>
                      <td className="py-2 pr-3 text-right tabular-nums">
                        {it.total_tokens_in.toLocaleString()}
                      </td>
                      <td className="py-2 pr-3 text-right tabular-nums">
                        {it.total_cost_cny.toFixed(4)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
