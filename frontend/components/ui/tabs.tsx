"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * 受控 Tabs 组件（KISS：原生 button + 受控 state）。
 *
 * 三分组切换：all / passed / disqualified / pending。
 * 调用方负责持久化 active 值（通常写到 URL query）。
 */
interface TabsProps {
  value: string;
  onChange: (value: string) => void;
  options: Array<{
    value: string;
    label: string;
    count?: number;
  }>;
  className?: string;
}

export function Tabs({ value, onChange, options, className }: TabsProps) {
  return (
    <div
      role="tablist"
      className={cn(
        "inline-flex h-10 items-center justify-center rounded-md bg-muted p-1 text-muted-foreground",
        className,
      )}
    >
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            role="tab"
            aria-selected={active}
            data-state={active ? "active" : "inactive"}
            onClick={() => onChange(opt.value)}
            className={cn(
              "inline-flex items-center justify-center whitespace-nowrap rounded-sm px-3 py-1.5 text-sm font-medium transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              active
                ? "bg-background text-foreground shadow"
                : "hover:text-foreground",
            )}
          >
            {opt.label}
            {opt.count !== undefined && (
              <span
                className={cn(
                  "ml-1.5 rounded-full px-1.5 py-0.5 text-xs",
                  active
                    ? "bg-primary/10 text-primary"
                    : "bg-muted-foreground/10",
                )}
              >
                {opt.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
