"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useAcceptInvite } from "@/hooks/useAuth";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

function AcceptInviteForm() {
  const router = useRouter();
  const params = useSearchParams();
  const inviteToken = params.get("token") ?? "";
  const acceptInvite = useAcceptInvite();
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);
    if (!inviteToken) {
      setFormError("邀请链接无效（缺少 token）");
      return;
    }
    try {
      await acceptInvite.mutateAsync({ invite_token: inviteToken, name, password });
      router.push("/dashboard");
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { error?: { message?: string } } } })?.response?.data?.error
          ?.message ?? "接受邀请失败";
      setFormError(msg);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-2xl">接受团队邀请</CardTitle>
        <CardDescription>设置姓名与密码完成注册并加入团队</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="space-y-4">
          {formError && (
            <Alert variant="destructive">
              <AlertTitle>无法接受邀请</AlertTitle>
              <AlertDescription>{formError}</AlertDescription>
            </Alert>
          )}

          <div className="space-y-2">
            <Label htmlFor="name">姓名</Label>
            <Input
              id="name"
              required
              maxLength={64}
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={acceptInvite.isPending}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="password">密码</Label>
            <Input
              id="password"
              type="password"
              required
              minLength={8}
              maxLength={72}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={acceptInvite.isPending}
              placeholder="至少 8 位，含字母与数字"
            />
          </div>

          <Button type="submit" className="w-full" disabled={acceptInvite.isPending}>
            {acceptInvite.isPending ? "处理中..." : "加入团队"}
          </Button>

          <div className="text-center text-sm text-muted-foreground">
            已有账号？{" "}
            <Link href="/login" className="font-medium text-primary hover:underline">
              直接登录
            </Link>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

export default function AcceptInvitePage() {
  // useSearchParams 必须 Suspense 包裹（Next 14 静态导出兼容）
  return (
    <div className="flex min-h-screen items-center justify-center px-4 py-12">
      <div className="w-full max-w-sm">
        <Suspense fallback={<div>加载中...</div>}>
          <AcceptInviteForm />
        </Suspense>
      </div>
    </div>
  );
}
