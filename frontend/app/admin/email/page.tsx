"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  useCreateEmailConfig,
  useDeleteEmailConfig,
  useEmailConfig,
  useEmailConfigStatus,
  useUpdateEmailConfig,
} from "@/hooks/useEmailConfigs";
import { useAuthStore } from "@/stores/authStore";
import type { AlertLevel } from "@/lib/api/emailConfigs";

const ALERT_LABEL: Record<AlertLevel, string> = {
  none: "正常",
  warning: "警告",
  critical: "严重",
};

const ALERT_BADGE_CLASS: Record<AlertLevel, string> = {
  none: "bg-emerald-100 text-emerald-700",
  warning: "bg-amber-100 text-amber-700",
  critical: "bg-red-100 text-red-700",
};

export default function EmailConfigPage() {
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === "admin";

  if (!isAdmin) {
    return (
      <div className="p-8">
        <Alert variant="destructive">
          <AlertTitle>权限不足</AlertTitle>
          <AlertDescription>仅团队管理员可访问邮箱配置页面。</AlertDescription>
        </Alert>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-8">
      <header>
        <h1 className="text-2xl font-bold">邮箱抓取配置</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          配置 IMAP 邮箱，系统将每 15 分钟轮询新邮件并自动抓取简历附件。
        </p>
      </header>

      <StatusCard />
      <ConfigCard />
    </div>
  );
}

// ============================================================================
// 状态卡片
// ============================================================================

function StatusCard() {
  const { data, isLoading, isError } = useEmailConfigStatus(15_000);

  if (isLoading) {
    return (
      <Card>
        <CardContent className="py-6 text-sm text-muted-foreground">
          加载状态中…
        </CardContent>
      </Card>
    );
  }
  if (isError || !data) {
    return (
      <Card>
        <CardContent className="py-6 text-sm text-muted-foreground">
          无法加载状态。
        </CardContent>
      </Card>
    );
  }
  if (!data.configured) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>当前状态</CardTitle>
          <CardDescription>尚未配置邮箱</CardDescription>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          请在下方填写 IMAP 凭据并保存。
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div>
            <CardTitle>当前状态</CardTitle>
            <CardDescription>每 15 秒自动刷新</CardDescription>
          </div>
          <span
            className={`rounded-full px-3 py-1 text-xs font-medium ${ALERT_BADGE_CLASS[data.alert_level]}`}
          >
            {ALERT_LABEL[data.alert_level]}
          </span>
        </div>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-3">
          <Field label="启用">{data.enabled ? "是" : "否"}</Field>
          <Field label="暂停中">{data.is_paused ? "是" : "否"}</Field>
          <Field label="连续失败">{data.consecutive_failures}</Field>
          <Field label="上次抓取">
            {data.last_fetched_at
              ? new Date(data.last_fetched_at).toLocaleString("zh-CN")
              : "—"}
          </Field>
          <Field label="暂停至">
            {data.paused_until
              ? new Date(data.paused_until).toLocaleString("zh-CN")
              : "—"}
          </Field>
          <Field label="预计下次">
            {data.next_scheduled_in_seconds != null
              ? `${Math.round(data.next_scheduled_in_seconds / 60)} 分钟后`
              : "未调度"}
          </Field>
        </dl>
        {data.last_error_summary && (
          <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
            <strong>最近错误：</strong> {data.last_error_summary}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 font-medium">{children}</dd>
    </div>
  );
}

// ============================================================================
// 配置卡片（create or update）
// ============================================================================

function ConfigCard() {
  const { data: existing, isLoading } = useEmailConfig();
  const createMut = useCreateEmailConfig();
  const updateMut = useUpdateEmailConfig();
  const deleteMut = useDeleteEmailConfig();

  const [imapHost, setImapHost] = useState("");
  const [imapPort, setImapPort] = useState("993");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [pollInterval, setPollInterval] = useState("15");
  const [enabled, setEnabled] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    if (!isLoading && existing && !hydrated) {
      setImapHost(existing.imap_host);
      setImapPort(String(existing.imap_port));
      setUsername(existing.username);
      setPollInterval(String(existing.poll_interval_min));
      setEnabled(existing.enabled);
      setPassword(""); // 永远不回显 password
      setHydrated(true);
    }
  }, [existing, isLoading, hydrated]);

  const isEdit = !!existing;

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      if (isEdit) {
        const payload: Parameters<typeof updateMut.mutateAsync>[0] = {
          imap_host: imapHost,
          imap_port: Number(imapPort),
          username,
          poll_interval_min: Number(pollInterval),
          enabled,
        };
        if (password) payload.password = password;
        await updateMut.mutateAsync(payload);
        setPassword("");
      } else {
        await createMut.mutateAsync({
          imap_host: imapHost,
          imap_port: Number(imapPort),
          username,
          password, // create 必填
          poll_interval_min: Number(pollInterval),
          enabled,
        });
        setPassword("");
      }
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { error?: { message?: string } } } })?.response
          ?.data?.error?.message ?? "保存失败";
      setError(msg);
    }
  };

  const onClearAlert = async () => {
    setError(null);
    try {
      await updateMut.mutateAsync({ clear_alert: true });
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { error?: { message?: string } } } })?.response
          ?.data?.error?.message ?? "清除告警失败";
      setError(msg);
    }
  };

  const onDelete = async () => {
    if (!confirm("确认删除当前邮箱配置？后续将不再轮询此邮箱。")) return;
    setError(null);
    try {
      await deleteMut.mutateAsync();
      setHydrated(false);
      setImapHost("");
      setImapPort("993");
      setUsername("");
      setPassword("");
      setPollInterval("15");
      setEnabled(true);
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { error?: { message?: string } } } })?.response
          ?.data?.error?.message ?? "删除失败";
      setError(msg);
    }
  };

  const pending = createMut.isPending || updateMut.isPending || deleteMut.isPending;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{isEdit ? "修改配置" : "新建配置"}</CardTitle>
        <CardDescription>
          {isEdit
            ? "密码留空表示不修改；其他字段改动会立即生效。"
            : "每团队只能配置一个邮箱。密码将经过 Fernet 加密入库。"}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2">
          {error && (
            <div className="sm:col-span-2">
              <Alert variant="destructive">
                <AlertTitle>操作失败</AlertTitle>
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            </div>
          )}

          <div className="space-y-2 sm:col-span-2">
            <Label htmlFor="imap_host">IMAP 主机 *</Label>
            <Input
              id="imap_host"
              required
              placeholder="imap.qq.com / imap.exmail.qq.com / imap.gmail.com"
              value={imapHost}
              onChange={(e) => setImapHost(e.target.value)}
              disabled={pending}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="imap_port">IMAP 端口 *</Label>
            <Input
              id="imap_port"
              type="number"
              required
              min={1}
              max={65535}
              value={imapPort}
              onChange={(e) => setImapPort(e.target.value)}
              disabled={pending}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="poll_interval_min">轮询间隔（分钟）*</Label>
            <Input
              id="poll_interval_min"
              type="number"
              required
              min={1}
              max={1440}
              value={pollInterval}
              onChange={(e) => setPollInterval(e.target.value)}
              disabled={pending}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="username">用户名（邮箱地址）*</Label>
            <Input
              id="username"
              type="email"
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              disabled={pending}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="password">
              密码 {isEdit ? "（留空不修改）" : "*"}
            </Label>
            <Input
              id="password"
              type="password"
              required={!isEdit}
              autoComplete="off"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={pending}
              placeholder={isEdit ? "••••••" : ""}
            />
          </div>

          <div className="flex items-center gap-2 sm:col-span-2">
            <input
              id="enabled"
              type="checkbox"
              className="h-4 w-4"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
              disabled={pending}
            />
            <Label htmlFor="enabled" className="cursor-pointer text-sm font-normal">
              启用轮询
            </Label>
          </div>

          <div className="flex flex-wrap gap-2 sm:col-span-2">
            <Button type="submit" disabled={pending}>
              {pending ? "保存中…" : isEdit ? "保存修改" : "创建配置"}
            </Button>

            {isEdit && (
              <>
                <Button
                  type="button"
                  variant="outline"
                  onClick={onClearAlert}
                  disabled={pending}
                >
                  清除告警 / 恢复轮询
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  onClick={onDelete}
                  disabled={pending}
                >
                  删除配置
                </Button>
              </>
            )}
            <Link href="/admin">
              <Button type="button" variant="ghost">
                返回
              </Button>
            </Link>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
