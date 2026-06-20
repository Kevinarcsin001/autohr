"use client";

import { useState } from "react";
import Link from "next/link";
import {
  useInviteTeamMember,
  useMyTeam,
  useRemoveMember,
  useTeamInvites,
  useUpdateMemberRole,
} from "@/hooks/useTeams";
import { useAuthStore } from "@/stores/authStore";
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

export default function MembersPage() {
  const user = useAuthStore((s) => s.user);
  const { data, isLoading, isError } = useMyTeam();
  const isAdmin = user?.role === "admin";

  if (isLoading) {
    return <div className="p-8">加载中...</div>;
  }
  if (isError || !data) {
    return (
      <div className="p-8">
        <Alert variant="destructive">
          <AlertTitle>无法加载团队信息</AlertTitle>
          <AlertDescription>请确认你已加入团队并重新登录。</AlertDescription>
        </Alert>
        <Link href="/dashboard" className="mt-4 inline-block text-primary underline">
          返回
        </Link>
      </div>
    );
  }
  if (!isAdmin) {
    return (
      <div className="p-8">
        <Alert variant="destructive">
          <AlertTitle>权限不足</AlertTitle>
          <AlertDescription>只有团队管理员可以访问成员管理页面。</AlertDescription>
        </Alert>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-8">
      <header>
        <h1 className="text-2xl font-bold">成员管理</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          团队：{data.team.name}（共 {data.members.length} 人）
        </p>
      </header>

      <MemberList teamId={data.team.id} members={data.members} selfId={user?.id} />
      <PendingInvites teamId={data.team.id} />
      <InviteForm teamId={data.team.id} />
    </div>
  );
}

// ============================================================================
// 成员列表
// ============================================================================

function MemberList({
  teamId,
  members,
  selfId,
}: {
  teamId: string;
  members: { id: string; email: string; name: string; role: string; created_at: string }[];
  selfId?: string;
}) {
  const updateRole = useUpdateMemberRole(teamId);
  const removeMember = useRemoveMember(teamId);

  const onChangeRole = (userId: string, role: string) => {
    if (userId === selfId && role === "member") {
      alert("不能修改自己的角色");
      return;
    }
    updateRole.mutate({ userId, payload: { role: role as "admin" | "member" } });
  };

  const onRemove = (userId: string, name: string) => {
    if (userId === selfId) {
      alert("不能移除自己；如需离开请使用登出");
      return;
    }
    if (!confirm(`确定要移除 ${name} 吗？该用户将被解绑出团队。`)) return;
    removeMember.mutate(userId);
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>当前成员</CardTitle>
        <CardDescription>修改角色或移除成员（admin 权限）</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-muted-foreground">
                <th className="py-2 pr-4 font-medium">姓名</th>
                <th className="py-2 pr-4 font-medium">邮箱</th>
                <th className="py-2 pr-4 font-medium">角色</th>
                <th className="py-2 pr-4 font-medium">加入时间</th>
                <th className="py-2 pr-4 font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {members.map((m) => (
                <tr key={m.id} className="border-b last:border-0">
                  <td className="py-3 pr-4">
                    {m.name}
                    {m.id === selfId && (
                      <span className="ml-2 text-xs text-muted-foreground">（你）</span>
                    )}
                  </td>
                  <td className="py-3 pr-4 text-muted-foreground">{m.email}</td>
                  <td className="py-3 pr-4">
                    <select
                      className="rounded-md border border-input bg-background px-2 py-1 text-sm"
                      value={m.role}
                      disabled={
                        updateRole.isPending ||
                        (m.id === selfId) // self 角色不让改
                      }
                      onChange={(e) => onChangeRole(m.id, e.target.value)}
                    >
                      <option value="member">普通成员</option>
                      <option value="admin">管理员</option>
                    </select>
                  </td>
                  <td className="py-3 pr-4 text-xs text-muted-foreground">
                    {new Date(m.created_at).toLocaleString("zh-CN")}
                  </td>
                  <td className="py-3 pr-4">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => onRemove(m.id, m.name)}
                      disabled={removeMember.isPending || m.id === selfId}
                    >
                      移除
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

// ============================================================================
// 邀请表单
// ============================================================================

function InviteForm({ teamId }: { teamId: string }) {
  const invite = useInviteTeamMember(teamId);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<"admin" | "member">("member");
  const [error, setError] = useState<string | null>(null);
  const [createdToken, setCreatedToken] = useState<string | null>(null);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setCreatedToken(null);
    try {
      const result = await invite.mutateAsync({ email, role, name });
      setCreatedToken(result.invite_token);
      setEmail("");
      setName("");
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { error?: { message?: string } } } })?.response?.data?.error
          ?.message ?? "邀请失败";
      setError(msg);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>邀请新成员</CardTitle>
        <CardDescription>
          被邀请人将通过邮件链接（48h 内有效）完成注册
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2">
          {error && (
            <div className="sm:col-span-2">
              <Alert variant="destructive">
                <AlertTitle>邀请失败</AlertTitle>
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            </div>
          )}
          {createdToken && (
            <div className="sm:col-span-2">
              <Alert>
                <AlertTitle>邀请已创建</AlertTitle>
                <AlertDescription>
                  <p>请把以下链接发送给被邀请人（一次性，48h 内有效）：</p>
                  <code className="mt-2 block break-all rounded bg-muted px-2 py-1 text-xs">
                    {typeof window !== "undefined"
                      ? `${window.location.origin}/accept-invite?token=${createdToken}`
                      : `/accept-invite?token=${createdToken}`}
                  </code>
                </AlertDescription>
              </Alert>
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="email">邮箱 *</Label>
            <Input
              id="email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={invite.isPending}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="name">姓名（可选）</Label>
            <Input
              id="name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={invite.isPending}
              maxLength={64}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="role">角色</Label>
            <select
              id="role"
              className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
              value={role}
              onChange={(e) => setRole(e.target.value as "admin" | "member")}
              disabled={invite.isPending}
            >
              <option value="member">普通成员</option>
              <option value="admin">管理员</option>
            </select>
          </div>
          <div className="flex items-end">
            <Button type="submit" disabled={invite.isPending} className="w-full">
              {invite.isPending ? "发送中..." : "发起邀请"}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

// ============================================================================
// 待接受邀请列表
// ============================================================================

function PendingInvites({ teamId }: { teamId: string }) {
  const { data, isLoading } = useTeamInvites(teamId);
  if (isLoading || !data || data.length === 0) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>邀请记录</CardTitle>
        <CardDescription>本团队全部邀请（含已接受 / 已过期）</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-muted-foreground">
                <th className="py-2 pr-4 font-medium">邮箱</th>
                <th className="py-2 pr-4 font-medium">角色</th>
                <th className="py-2 pr-4 font-medium">过期时间</th>
              </tr>
            </thead>
            <tbody>
              {data.map((inv) => (
                <tr key={inv.id} className="border-b last:border-0">
                  <td className="py-3 pr-4">{inv.email}</td>
                  <td className="py-3 pr-4 text-muted-foreground">
                    {inv.role === "admin" ? "管理员" : "普通成员"}
                  </td>
                  <td className="py-3 pr-4 text-xs text-muted-foreground">
                    {new Date(inv.expires_at).toLocaleString("zh-CN")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}
