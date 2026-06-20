"use client";

import { useMemo, useRef, useState, useCallback, useEffect } from "react";
import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  type OnChangeFn,
  type SortingState,
  type Updater,
  useReactTable,
  type VisibilityState,
} from "@tanstack/react-table";
import {
  ArrowDown,
  ArrowUp,
  ChevronLeft,
  ChevronRight,
  Columns3,
  Loader2,
} from "lucide-react";

import type { CandidateListItem } from "@/lib/api/candidates";
import {
  type ColumnConfig,
  type Density,
} from "@/lib/savedViews";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// ============================================================================
// 列元数据
// ============================================================================

export interface ColumnMeta {
  /** 中文标题 */
  label: string;
  /** 默认是否可见 */
  defaultVisible: boolean;
  /** 默认顺序（从 0 开始） */
  defaultOrder: number;
  /** 是否可由用户隐藏（关键字段如姓名不可隐藏） */
  toggleable: boolean;
  /** 列宽（px 或 className） */
  widthClass?: string;
}

// ============================================================================
// 列定义
// ============================================================================

/**
 * 列 id 与表格 column.id 对应；用于列自定义、视图保存。
 *
 * 设计：
 * - 字段全部从 CandidateListItem 派生（无任何前端计算/推断）
 * - 评分列做"颜色分级"展示（≥80 绿，60-79 黄，<60 红，null 灰）
 * - skills 数组只展示前 3 个，超出 +N tooltip
 */
export const COLUMN_DEFS: ColumnDef<CandidateListItem>[] = [
  {
    id: "name",
    accessorKey: "name",
    meta: {
      label: "姓名",
      defaultVisible: true,
      defaultOrder: 0,
      toggleable: false,
      widthClass: "w-[160px]",
    } satisfies ColumnMeta,
    cell: ({ row }) => (
      <div className="flex flex-col">
        <span className="font-medium">{row.original.name}</span>
        {row.original.email && (
          <span className="text-xs text-muted-foreground">
            {row.original.email}
          </span>
        )}
        {row.original.phone && !row.original.email && (
          <span className="text-xs text-muted-foreground">
            {row.original.phone}
          </span>
        )}
      </div>
    ),
  },
  {
    id: "source_type",
    accessorKey: "source_type",
    meta: {
      label: "来源",
      defaultVisible: true,
      defaultOrder: 1,
      toggleable: true,
      widthClass: "w-[100px]",
    } satisfies ColumnMeta,
    cell: ({ row }) => {
      const v = row.original.source_type;
      if (!v) return <span className="text-muted-foreground">—</span>;
      const labelMap: Record<string, string> = {
        upload: "上传",
        platform: "平台",
        email: "邮件",
      };
      return <Badge variant="secondary">{labelMap[v] ?? v}</Badge>;
    },
  },
  {
    id: "total",
    accessorKey: "total",
    meta: {
      label: "总分",
      defaultVisible: true,
      defaultOrder: 2,
      toggleable: true,
      widthClass: "w-[80px]",
    } satisfies ColumnMeta,
    cell: ({ row }) => (
      <ScoreBadge value={row.original.total} />
    ),
  },
  {
    id: "skill",
    accessorKey: "skill",
    meta: {
      label: "技能",
      defaultVisible: true,
      defaultOrder: 3,
      toggleable: true,
      widthClass: "w-[70px]",
    } satisfies ColumnMeta,
    cell: ({ row }) => <ScoreBadge value={row.original.skill} />,
  },
  {
    id: "experience",
    accessorKey: "experience",
    meta: {
      label: "经验",
      defaultVisible: true,
      defaultOrder: 4,
      toggleable: true,
      widthClass: "w-[70px]",
    } satisfies ColumnMeta,
    cell: ({ row }) => <ScoreBadge value={row.original.experience} />,
  },
  {
    id: "education_score",
    accessorKey: "education_score",
    meta: {
      label: "学历分",
      defaultVisible: false,
      defaultOrder: 5,
      toggleable: true,
      widthClass: "w-[80px]",
    } satisfies ColumnMeta,
    cell: ({ row }) => (
      <ScoreBadge value={row.original.education_score} />
    ),
  },
  {
    id: "stability",
    accessorKey: "stability",
    meta: {
      label: "稳定性",
      defaultVisible: false,
      defaultOrder: 6,
      toggleable: true,
      widthClass: "w-[80px]",
    } satisfies ColumnMeta,
    cell: ({ row }) => <ScoreBadge value={row.original.stability} />,
  },
  {
    id: "potential",
    accessorKey: "potential",
    meta: {
      label: "潜力",
      defaultVisible: false,
      defaultOrder: 7,
      toggleable: true,
      widthClass: "w-[70px]",
    } satisfies ColumnMeta,
    cell: ({ row }) => <ScoreBadge value={row.original.potential} />,
  },
  {
    id: "education",
    accessorKey: "education",
    meta: {
      label: "学历",
      defaultVisible: true,
      defaultOrder: 8,
      toggleable: true,
      widthClass: "w-[80px]",
    } satisfies ColumnMeta,
    cell: ({ row }) => {
      const v = row.original.education;
      if (!v) return <span className="text-muted-foreground">—</span>;
      const labelMap: Record<string, string> = {
        high_school: "高中",
        bachelor: "本科",
        master: "硕士",
        phd: "博士",
        other: "其他",
      };
      return (
        <span>{labelMap[v] ?? String(v)}</span>
      );
    },
  },
  {
    id: "years_of_experience",
    accessorKey: "years_of_experience",
    meta: {
      label: "年限",
      defaultVisible: true,
      defaultOrder: 9,
      toggleable: true,
      widthClass: "w-[70px]",
    } satisfies ColumnMeta,
    cell: ({ row }) => {
      const v = row.original.years_of_experience;
      if (v === null || v === undefined) {
        return <span className="text-muted-foreground">—</span>;
      }
      return <span>{v} 年</span>;
    },
  },
  {
    id: "current_company",
    accessorKey: "current_company",
    meta: {
      label: "当前公司",
      defaultVisible: false,
      defaultOrder: 10,
      toggleable: true,
      widthClass: "w-[160px]",
    } satisfies ColumnMeta,
    cell: ({ row }) => {
      const v = row.original.current_company;
      if (!v) return <span className="text-muted-foreground">—</span>;
      return <span className="truncate">{v}</span>;
    },
  },
  {
    id: "skills",
    accessorKey: "skills",
    meta: {
      label: "技能标签",
      defaultVisible: true,
      defaultOrder: 11,
      toggleable: true,
      widthClass: "w-[200px]",
    } satisfies ColumnMeta,
    cell: ({ row }) => {
      const skills = row.original.skills ?? [];
      if (skills.length === 0) {
        return <span className="text-muted-foreground">—</span>;
      }
      const visible = skills.slice(0, 3);
      const overflow = skills.length - visible.length;
      return (
        <div className="flex flex-wrap gap-1">
          {visible.map((s) => (
            <span
              key={s}
              className="rounded bg-blue-50 px-1.5 py-0.5 text-xs text-blue-700"
            >
              {s}
            </span>
          ))}
          {overflow > 0 && (
            <span
              className="text-xs text-muted-foreground"
              title={skills.slice(3).join("、")}
            >
              +{overflow}
            </span>
          )}
        </div>
      );
    },
  },
  {
    id: "status",
    accessorKey: "disqualified",
    enableSorting: false,
    meta: {
      label: "状态",
      defaultVisible: true,
      defaultOrder: 12,
      toggleable: true,
      widthClass: "w-[100px]",
    } satisfies ColumnMeta,
    cell: ({ row }) => {
      if (row.original.manually_overridden) {
        return <Badge variant="warning">人工覆盖</Badge>;
      }
      if (row.original.disqualified === true) {
        return <Badge variant="destructive">淘汰</Badge>;
      }
      if (row.original.disqualified === false) {
        return <Badge variant="success">通过</Badge>;
      }
      return <Badge variant="outline">待处理</Badge>;
    },
  },
  {
    id: "created_at",
    accessorKey: "created_at",
    meta: {
      label: "入库时间",
      defaultVisible: false,
      defaultOrder: 13,
      toggleable: true,
      widthClass: "w-[140px]",
    } satisfies ColumnMeta,
    cell: ({ row }) => {
      const v = row.original.created_at;
      if (!v) return <span className="text-muted-foreground">—</span>;
      const d = new Date(v);
      const pad = (n: number) => String(n).padStart(2, "0");
      return (
        <span className="text-xs">
          {d.getFullYear()}-{pad(d.getMonth() + 1)}-{pad(d.getDate())}{" "}
          {pad(d.getHours())}:{pad(d.getMinutes())}
        </span>
      );
    },
  },
];

// ============================================================================
// 默认列配置
// ============================================================================

export function defaultColumnConfigs(): ColumnConfig[] {
  return COLUMN_DEFS.map((c) => {
    const meta = c.meta as ColumnMeta | undefined;
    return {
      id: c.id!,
      visible: meta?.defaultVisible ?? true,
      order: meta?.defaultOrder ?? 99,
    };
  }).sort((a, b) => a.order - b.order);
}

// ============================================================================
// 子组件：评分徽章（颜色分级）
// ============================================================================

function ScoreBadge({ value }: { value: number | null | undefined }) {
  if (value === null || value === undefined) {
    return <span className="rounded-md bg-muted/50 px-1.5 py-0.5 text-xs font-mono text-muted-foreground">—</span>;
  }
  const tone =
    value >= 80
      ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300"
      : value >= 60
        ? "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300"
        : "bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300";
  return (
    <span className={cn("inline-block rounded-md px-1.5 py-0.5 text-xs font-mono font-semibold tabular-nums", tone)}>
      {value}
    </span>
  );
}

// ============================================================================
// 主组件
// ============================================================================

interface CandidateTableProps {
  data: CandidateListItem[];
  isLoading?: boolean;
  error?: Error | null;

  /** 当前排序（受控，由父组件同步 URL） */
  sorting: SortingState;
  onSortingChange: (next: SortingState) => void;

  /** 列配置（受控） */
  columns: ColumnConfig[];
  onColumnsChange: (next: ColumnConfig[]) => void;

  /** 密度（受控） */
  density: Density;
  onDensityChange: (next: Density) => void;

  /** 分页（受控） */
  pagination: {
    page: number;
    pageSize: number;
    total: number;
  };
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;

  /** 行点击（Enter 或 click）打开详情 */
  onOpenCandidate?: (candidate: CandidateListItem) => void;

  /** 可选空状态 */
  emptyHint?: string;
}

const DENSITY_CLASS: Record<Density, string> = {
  compact: "text-xs [&_td]:py-1 [&_th]:py-1",
  default: "text-sm [&_td]:py-2 [&_th]:py-2",
  comfortable: "text-base [&_td]:py-3.5 [&_th]:py-3",
};

const PAGE_SIZE_OPTIONS = [10, 20, 50, 100];

export function CandidateTable({
  data,
  isLoading,
  error,
  sorting,
  onSortingChange,
  columns,
  onColumnsChange,
  density,
  onDensityChange,
  pagination,
  onPageChange,
  onPageSizeChange,
  onOpenCandidate,
  emptyHint = "暂无候选人",
}: CandidateTableProps) {
  const [focusedRowIndex, setFocusedRowIndex] = useState<number>(-1);
  const [columnMenuOpen, setColumnMenuOpen] = useState(false);
  const columnMenuRef = useRef<HTMLDivElement>(null);

  // ----- 列可见性 → TanStack -----
  const visibility = useMemo<VisibilityState>(() => {
    const map: VisibilityState = {};
    for (const c of columns) {
      map[c.id] = c.visible;
    }
    return map;
  }, [columns]);

  // ----- 按用户顺序排序列 -----
  const orderedColumns = useMemo(() => {
    const sorted = [...columns].sort((a, b) => a.order - b.order);
    const visible = sorted
      .filter((c) => c.visible)
      .map((c) => COLUMN_DEFS.find((d) => d.id === c.id))
      .filter(Boolean) as ColumnDef<CandidateListItem>[];
    return visible;
  }, [columns]);

  // ----- TanStack Table -----
  // TanStack Table 的 onSortingChange 既接受 value 也接受 updater 函数；
  // 父组件只关心最终值，所以这里做一层包装。
  const handleSortingChange: OnChangeFn<SortingState> = useCallback(
    (updater: Updater<SortingState>) => {
      const next =
        typeof updater === "function" ? updater(sorting) : updater;
      onSortingChange(next);
    },
    [onSortingChange, sorting],
  );

  const table = useReactTable({
    data,
    columns: orderedColumns,
    state: { sorting, columnVisibility: visibility },
    onSortingChange: handleSortingChange,
    manualPagination: true, // 服务端分页
    manualFiltering: true, // 服务端筛选
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  const rows = table.getRowModel().rows;

  // ----- 行聚焦：数据变化后重置 -----
  useEffect(() => {
    setFocusedRowIndex(-1);
  }, [data]);

  // ----- 点击外部关闭列自定义菜单 -----
  useEffect(() => {
    if (!columnMenuOpen) return;
    const handler = (e: MouseEvent) => {
      if (
        columnMenuRef.current &&
        !columnMenuRef.current.contains(e.target as Node)
      ) {
        setColumnMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [columnMenuOpen]);

  // ----- 键盘导航：方向键移动 + Enter 打开 -----
  const handleTableKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      if (rows.length === 0) return;
      if (
        e.key !== "ArrowDown" &&
        e.key !== "ArrowUp" &&
        e.key !== "Home" &&
        e.key !== "End" &&
        e.key !== "Enter" &&
        e.key !== "PageDown" &&
        e.key !== "PageUp"
      ) {
        return;
      }
      e.preventDefault();

      if (e.key === "Home") {
        setFocusedRowIndex(0);
        return;
      }
      if (e.key === "End") {
        setFocusedRowIndex(rows.length - 1);
        return;
      }
      if (e.key === "Enter") {
        const row = rows[focusedRowIndex];
        if (row && onOpenCandidate) {
          onOpenCandidate(row.original);
        }
        return;
      }
      if (e.key === "PageDown") {
        setFocusedRowIndex((i) =>
          Math.min(rows.length - 1, i < 0 ? 0 : i + 5),
        );
        return;
      }
      if (e.key === "PageUp") {
        setFocusedRowIndex((i) => Math.max(0, i < 0 ? 0 : i - 5));
        return;
      }
      setFocusedRowIndex((i) => {
        if (i < 0) return 0;
        const next = e.key === "ArrowDown" ? i + 1 : i - 1;
        return Math.max(0, Math.min(rows.length - 1, next));
      });
    },
    [rows, focusedRowIndex, onOpenCandidate],
  );

  // ----- 列可见性切换 -----
  const toggleColumn = useCallback(
    (id: string) => {
      const next = columns.map((c) =>
        c.id === id ? { ...c, visible: !c.visible } : c,
      );
      onColumnsChange(next);
    },
    [columns, onColumnsChange],
  );

  // ----- 列顺序上移/下移 -----
  const moveColumn = useCallback(
    (id: string, dir: -1 | 1) => {
      const sorted = [...columns].sort((a, b) => a.order - b.order);
      const idx = sorted.findIndex((c) => c.id === id);
      if (idx < 0) return;
      const target = idx + dir;
      if (target < 0 || target >= sorted.length) return;
      const a = sorted[idx];
      const b = sorted[target];
      const ao = a.order;
      a.order = b.order;
      b.order = ao;
      onColumnsChange([...sorted]);
    },
    [columns, onColumnsChange],
  );

  // ----- 渲染 -----
  return (
    <div className="space-y-3">
      {/* 工具栏 */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          {isLoading && (
            <Loader2 className="h-4 w-4 animate-spin" />
          )}
          <span>共 {pagination.total} 条</span>
        </div>

        <div className="flex items-center gap-2">
          {/* 密度切换 */}
          <div className="inline-flex h-8 overflow-hidden rounded-md border">
            {(
              [
                { v: "compact", label: "紧凑" },
                { v: "default", label: "标准" },
                { v: "comfortable", label: "宽松" },
              ] as Array<{ v: Density; label: string }>
            ).map((opt) => (
              <button
                key={opt.v}
                type="button"
                onClick={() => onDensityChange(opt.v)}
                className={cn(
                  "px-2.5 text-xs transition-colors",
                  density === opt.v
                    ? "bg-primary text-primary-foreground"
                    : "bg-background hover:bg-accent",
                )}
                aria-pressed={density === opt.v}
              >
                {opt.label}
              </button>
            ))}
          </div>

          {/* 列自定义 */}
          <div className="relative" ref={columnMenuRef}>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setColumnMenuOpen((o) => !o)}
              aria-expanded={columnMenuOpen}
              aria-haspopup="true"
            >
              <Columns3 className="mr-1 h-3.5 w-3.5" />
              列
            </Button>
            {columnMenuOpen && (
              <div
                role="dialog"
                aria-label="列自定义"
                className="absolute right-0 z-50 mt-1 w-64 rounded-md border bg-popover p-3 shadow-md"
              >
                <div className="mb-2 text-xs font-medium text-muted-foreground">
                  显示列与顺序
                </div>
                <ul className="max-h-72 space-y-1 overflow-y-auto">
                  {[...columns]
                    .sort((a, b) => a.order - b.order)
                    .map((c) => {
                      const meta = COLUMN_DEFS.find(
                        (d) => d.id === c.id,
                      )?.meta as ColumnMeta | undefined;
                      return (
                        <li
                          key={c.id}
                          className="flex items-center gap-2 rounded px-1 py-1 hover:bg-accent"
                        >
                          <input
                            type="checkbox"
                            checked={c.visible}
                            disabled={!meta?.toggleable}
                            onChange={() => toggleColumn(c.id)}
                            className="h-3.5 w-3.5 cursor-pointer"
                            aria-label={`显示列 ${meta?.label ?? c.id}`}
                          />
                          <span
                            className={cn(
                              "flex-1 text-sm",
                              !meta?.toggleable &&
                                "text-muted-foreground",
                            )}
                          >
                            {meta?.label ?? c.id}
                          </span>
                          <button
                            type="button"
                            onClick={() => moveColumn(c.id, -1)}
                            className="rounded p-0.5 hover:bg-background"
                            aria-label="上移"
                          >
                            <ChevronLeft className="h-3 w-3" />
                          </button>
                          <button
                            type="button"
                            onClick={() => moveColumn(c.id, 1)}
                            className="rounded p-0.5 hover:bg-background"
                            aria-label="下移"
                          >
                            <ChevronRight className="h-3 w-3" />
                          </button>
                        </li>
                      );
                    })}
                </ul>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* 错误 */}
      {error && (
        <div
          role="alert"
          className="rounded-md border border-destructive/50 bg-destructive/5 p-3 text-sm text-destructive"
        >
          加载失败：{error.message}
        </div>
      )}

      {/* 表格 */}
      <div
        className="relative overflow-x-auto rounded-md border focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        tabIndex={0}
        role="grid"
        aria-rowcount={rows.length + 1}
        onKeyDown={handleTableKeyDown}
      >
        <table className={cn("w-full table-fixed", DENSITY_CLASS[density])}>
          <thead className="bg-muted/50 text-muted-foreground">
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id} role="row">
                {hg.headers.map((header) => {
                  const meta = header.column.columnDef.meta as
                    | ColumnMeta
                    | undefined;
                  const canSort = header.column.getCanSort();
                  const sorted = header.column.getIsSorted();
                  return (
                    <th
                      key={header.id}
                      role="columnheader"
                      className={cn(
                        "px-3 text-left font-medium",
                        meta?.widthClass,
                      )}
                      style={{ width: undefined }}
                    >
                      {header.isPlaceholder ? null : canSort ? (
                        <button
                          type="button"
                          className="inline-flex items-center gap-1 hover:text-foreground"
                          onClick={header.column.getToggleSortingHandler()}
                        >
                          {flexRender(
                            header.column.columnDef.header,
                            header.getContext(),
                          )}
                          {sorted === "asc" ? (
                            <ArrowUp className="h-3 w-3" />
                          ) : sorted === "desc" ? (
                            <ArrowDown className="h-3 w-3" />
                          ) : (
                            <ArrowDown className="h-3 w-3 opacity-30" />
                          )}
                        </button>
                      ) : (
                        flexRender(
                          header.column.columnDef.header,
                          header.getContext(),
                        )
                      )}
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td
                  colSpan={orderedColumns.length || 1}
                  className="px-3 py-8 text-center text-sm text-muted-foreground"
                >
                  {emptyHint}
                </td>
              </tr>
            ) : (
              rows.map((row, idx) => {
                const isFocused = idx === focusedRowIndex;
                return (
                  <tr
                    key={row.id}
                    role="row"
                    aria-rowindex={idx + 1}
                    className={cn(
                      "border-t transition-colors",
                      isFocused
                        ? "bg-accent/70 outline outline-2 outline-offset-0 outline-primary"
                        : "hover:bg-accent/40",
                    )}
                    onClick={() => {
                      setFocusedRowIndex(idx);
                      onOpenCandidate?.(row.original);
                    }}
                  >
                    {row.getVisibleCells().map((cell) => {
                      const meta = cell.column.columnDef.meta as
                        | ColumnMeta
                        | undefined;
                      return (
                        <td
                          key={cell.id}
                          role="gridcell"
                          className={cn("px-3 align-top", meta?.widthClass)}
                        >
                          {flexRender(
                            cell.column.columnDef.cell,
                            cell.getContext(),
                          )}
                        </td>
                      );
                    })}
                    {/* 操作列 */}
                    {onOpenCandidate && (
                      <td role="gridcell" className="px-2 align-top">
                        <button
                          type="button"
                          className="rounded-md bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground hover:bg-primary/90"
                          onClick={(e) => {
                            e.stopPropagation();
                            onOpenCandidate(row.original);
                          }}
                        >
                          查看
                        </button>
                      </td>
                    )}
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* 分页 */}
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <div className="flex items-center gap-2">
          <span className="text-muted-foreground">每页</span>
          <select
            value={pagination.pageSize}
            onChange={(e) => onPageSizeChange(Number(e.target.value))}
            className="h-8 rounded-md border bg-background px-2 text-sm"
            aria-label="每页大小"
          >
            {PAGE_SIZE_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <span className="text-muted-foreground">条</span>
        </div>

        <div className="flex items-center gap-1">
          <Button
            variant="outline"
            size="icon"
            className="h-8 w-8"
            disabled={pagination.page <= 1}
            onClick={() => onPageChange(pagination.page - 1)}
            aria-label="上一页"
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <span className="px-2 tabular-nums">
            {pagination.page} /{" "}
            {Math.max(
              1,
              Math.ceil(pagination.total / pagination.pageSize),
            )}
          </span>
          <Button
            variant="outline"
            size="icon"
            className="h-8 w-8"
            disabled={
              pagination.page *
                pagination.pageSize >=
              pagination.total
            }
            onClick={() => onPageChange(pagination.page + 1)}
            aria-label="下一页"
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {/* 键盘提示 */}
      <p className="text-xs text-muted-foreground">
        键盘导航：方向键移动 · Enter 打开详情 · Home/End 跳首尾 ·
        PageUp/PageDown 翻五行
      </p>
    </div>
  );
}

// ============================================================================
// 列自定义辅助
// ============================================================================

/**
 * 暴露给父组件：将 SortingState 转换为 CandidateListParams.sort_by / sort_order。
 */
export function sortingToParams(
  sorting: SortingState,
): { sort_by: string; sort_order: "asc" | "desc" } {
  if (sorting.length === 0) {
    return { sort_by: "total", sort_order: "desc" };
  }
  const first = sorting[0];
  return {
    sort_by: first.id,
    sort_order: first.desc ? "desc" : "asc",
  };
}

/**
 * 暴露给父组件：将 URL 的 sort_by + sort_order 转换为 SortingState。
 */
export function paramsToSorting(
  sortBy: string,
  sortOrder: "asc" | "desc",
): SortingState {
  if (!sortBy) return [];
  return [{ id: sortBy, desc: sortOrder === "desc" }];
}
