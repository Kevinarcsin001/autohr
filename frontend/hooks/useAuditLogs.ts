"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchAuditLogs } from "@/lib/api/auditLogs";

export function useAuditLogs(page = 1, pageSize = 50) {
  return useQuery({
    queryKey: ["audit-logs", page, pageSize],
    queryFn: () => fetchAuditLogs(page, pageSize),
  });
}
