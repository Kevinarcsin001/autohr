"use client";

import { Badge } from "@/components/ui/badge";

/**
 * 多值标签输入：每行一项的 textarea + chip 预览。
 *
 * MVP 形态（仿 JobForm.required_skills）：受控 value 为 string[]，
 * textarea 每行一项，失焦/回车解析；下方 chip 展示当前已解析的标签。
 * 后续可升级为 inline chip 输入。
 */

interface TagInputProps {
  value: string[];
  onChange: (value: string[]) => void;
  placeholder?: string;
  id?: string;
}

/** 文本 → 标签数组：按换行/逗号/分号拆分，去空白去重。 */
export function parseTags(text: string): string[] {
  return Array.from(
    new Set(
      text
        .split(/[,，;；\n]/)
        .map((s) => s.trim())
        .filter(Boolean),
    ),
  );
}

export function TagInput({
  value,
  onChange,
  placeholder = "每行一个标签，或用逗号分隔",
  id,
}: TagInputProps) {
  // 受控展示：textarea 显示 join(\n)，chip 显示解析后的 value
  const text = value.join("\n");

  return (
    <div className="space-y-2">
      <textarea
        id={id}
        value={text}
        onChange={(e) => onChange(parseTags(e.target.value))}
        placeholder={placeholder}
        rows={3}
        className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      />
      {value.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {value.map((tag) => (
            <Badge key={tag} variant="secondary" className="text-xs">
              {tag}
              <button
                type="button"
                aria-label={`移除 ${tag}`}
                className="ml-1 text-muted-foreground hover:text-foreground"
                onClick={() => onChange(value.filter((t) => t !== tag))}
              >
                ×
              </button>
            </Badge>
          ))}
        </div>
      )}
    </div>
  );
}
