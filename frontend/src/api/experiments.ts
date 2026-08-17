import {
  useQuery,
  useMutation,
  useQueryClient,
  keepPreviousData,
} from '@tanstack/react-query';
import client from './client';
import type { Envelope } from './types';

export type ProviderEntry = {
  id: string;
  completer: string;
  base_url_env: string | null;
  api_key_env: string | null;
  available: boolean;
};

export type StageEntry = {
  stage: string;
  prompt_version: string | null;
  default_provider: string | null;
  default_model: string | null;
};

export type ProvidersResponse = {
  providers: ProviderEntry[];
  stages: StageEntry[];
};

export type RunWordResult = {
  word_id: number;
  word: string;
  ok: boolean;
  valid: boolean;
  cost_usd: number;
  latency_ms: number | null;
  text: string | null;
  error: string | null;
};

export type ExperimentRun = {
  id: number;
  provider: string;
  model: string;
  stage: string;
  prompt_override: string | null;
  seed: number;
  status: 'running' | 'done' | 'error';
  error: string | null;
  results: RunWordResult[] | null;
  total_cost_usd: number;
  ok_count: number;
  valid_count: number;
  created_at: string;
  finished_at: string | null;
};

export type CreateRunPayload = {
  agent_id?: number;
  provider?: string;
  model?: string;
  stage?: string;
  prompt_override?: string | null;
  word_count: number;
  seed: number;
};

export function useProvidersQuery() {
  return useQuery<ProvidersResponse>({
    queryKey: ['experiments', 'providers'],
    queryFn: async () => {
      const res = await client.get<Envelope<ProvidersResponse>>('/experiments/providers');
      return res.data.data;
    },
  });
}

export function useModelsQuery(providerId: string | null) {
  return useQuery<string[]>({
    queryKey: ['experiments', 'models', providerId],
    enabled: !!providerId,
    retry: false,
    queryFn: async () => {
      const res = await client.get<Envelope<{ models: string[] }>>(
        `/experiments/providers/${providerId}/models`,
      );
      return res.data.data.models;
    },
  });
}

export function useRunsQuery() {
  return useQuery<{ items: ExperimentRun[] }>({
    queryKey: ['experiments', 'runs'],
    placeholderData: keepPreviousData,
    refetchInterval: (query) => {
      const items = query.state.data?.items ?? [];
      const anyRunning = items.some((r) => r.status === 'running');
      return anyRunning ? 1500 : false;
    },
    queryFn: async () => {
      const res = await client.get<Envelope<{ items: ExperimentRun[] }>>('/experiments/runs');
      return res.data.data;
    },
  });
}

export function useRunQuery(id: number | null) {
  return useQuery<ExperimentRun>({
    queryKey: ['experiments', 'run', id],
    enabled: id !== null,
    refetchInterval: (query) => (query.state.data?.status === 'running' ? 1200 : false),
    queryFn: async () => {
      const res = await client.get<Envelope<{ run: ExperimentRun }>>(
        `/experiments/runs/${id}`,
      );
      return res.data.data.run;
    },
  });
}

export function useCreateRunMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: CreateRunPayload) => {
      const res = await client.post<Envelope<{ run_id: number }>>(
        '/experiments/runs',
        payload,
      );
      return res.data.data.run_id;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['experiments', 'runs'] });
    },
  });
}
