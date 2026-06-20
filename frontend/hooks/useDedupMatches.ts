"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchDedupMatches } from "@/lib/api/dedup";

export function useDedupMatches() {
  return useQuery({
    queryKey: ["dedup-matches"],
    queryFn: fetchDedupMatches,
  });
}
