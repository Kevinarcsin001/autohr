"use client";

import Link from "next/link";
import { useState } from "react";
import { PackageOpen, Upload } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useAuthStore } from "@/stores/authStore";

export default function ImportsPage() {
  const user = useAuthStore((s) => s.user);
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [detected, setDetected] = useState<string | null>(null);

  if (!user) {
    return (
      <div className="p-8">
        <p>未登录</p>
        <Link href="/login" className="text-primary underline">
          前往登录
        </Link>
      </div>
    );
  }

  const handleDetect = async () => {
    if (!file) return;
    setUploading(true);
    setResult(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const { default: axios } = await import("axios");
      const resp = await axios.post(
        `${process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000"}/api/platform-imports/detect`,
        form,
        { withCredentials: true },
      );
      setDetected(resp.data?.platform || "未知平台");
    } catch {
      setResult("平台识别失败");
    } finally {
      setUploading(false);
    }
  };

  const handleImport = async () => {
    if (!file) return;
    setUploading(true);
    setResult(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const { default: axios } = await import("axios");
      await axios.post(
        `${process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000"}/api/platform-imports/`,
        form,
        { withCredentials: true },
      );
      setResult("导入成功！简历已加入解析队列。");
      setFile(null);
      setDetected(null);
    } catch {
      setResult("导入失败，请重试。");
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6 px-4 py-8 sm:px-6">
      <header>
        <h1 className="text-2xl font-bold">平台导入</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          导入招聘平台（Boss/智联/猎聘）导出的简历包，自动识别平台并解析
        </p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>选择文件</CardTitle>
          <CardDescription>
            支持 ZIP / Excel / JSON 格式的招聘平台导出包
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-col items-center gap-4 rounded-lg border-2 border-dashed p-8 text-center">
            <PackageOpen className="h-10 w-10 text-muted-foreground/30" />
            <div>
              <p className="text-sm font-medium">
                {file ? file.name : "选择平台导出文件"}
              </p>
              <p className="text-xs text-muted-foreground">
                点击下方按钮选择文件
              </p>
            </div>
            <input
              type="file"
              accept=".zip,.xlsx,.xls,.json,.csv"
              onChange={(e) => {
                setFile(e.target.files?.[0] ?? null);
                setDetected(null);
                setResult(null);
              }}
              className="text-sm"
            />
          </div>

          {detected && (
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">检测结果：</span>
              <Badge variant="outline">{detected}</Badge>
            </div>
          )}

          {result && (
            <Alert variant={result.includes("成功") ? "default" : "destructive"}>
              <AlertDescription>{result}</AlertDescription>
            </Alert>
          )}

          <div className="flex gap-2">
            <Button onClick={handleDetect} disabled={!file || uploading}>
              {uploading ? "处理中..." : "检测平台"}
            </Button>
            <Button
              variant="default"
              onClick={handleImport}
              disabled={!file || uploading}
            >
              <Upload className="mr-1.5 h-4 w-4" />
              {uploading ? "导入中..." : "导入并解析"}
            </Button>
          </div>
        </CardContent>
      </Card>

      <div>
        <Link href="/dashboard" className="text-sm text-primary hover:underline">
          ← 返回工作台
        </Link>
      </div>
    </div>
  );
}
