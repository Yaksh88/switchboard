import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { api } from "./client";
import type {
  EvalRunDetail,
  EvalRunSummary,
  ModelRow,
  Overview,
  ProviderHealth,
  RequestFilters,
  RequestPage,
  Window,
} from "./types";

export const useOverview = (window: Window) =>
  useQuery({
    queryKey: ["overview", window],
    queryFn: () => api<Overview>("/admin/api/overview", { window }),
    refetchInterval: 15_000,
    placeholderData: keepPreviousData,
  });

export const useModels = (window: Window) =>
  useQuery({
    queryKey: ["models", window],
    queryFn: () => api<ModelRow[]>("/admin/api/models", { window }),
    refetchInterval: 15_000,
    placeholderData: keepPreviousData,
  });

export const useRequests = (filters: RequestFilters) =>
  useInfiniteQuery({
    queryKey: ["requests", filters],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }) =>
      api<RequestPage>("/admin/api/requests", { ...filters, limit: 50, before_id: pageParam }),
    getNextPageParam: (page) => page.next_before_id ?? undefined,
  });

export const useProviders = () =>
  useQuery({
    queryKey: ["providers"],
    queryFn: () => api<ProviderHealth[]>("/admin/api/providers"),
    refetchInterval: 5_000,
  });

export const useEvalRuns = () =>
  useQuery({
    queryKey: ["eval-runs"],
    queryFn: () => api<EvalRunSummary[]>("/admin/api/evals", { limit: 200 }),
    refetchInterval: 30_000,
  });

export const useEvalRun = (id: string | null) =>
  useQuery({
    queryKey: ["eval-run", id],
    queryFn: () => api<EvalRunDetail>(`/admin/api/evals/${id}`),
    enabled: id !== null,
  });
