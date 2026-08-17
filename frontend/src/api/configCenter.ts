import {
  useQuery,
  useMutation,
  useQueryClient,
} from '@tanstack/react-query';
import client from './client';
import type { Envelope } from './types';

export type ProviderVersion = {
  version: number;
  name: string;
  transport: string;
  base_url: string;
  notes: string | null;
  created_at: string;
};

export type ProviderConfig = {
  id: number;
  name: string;
  transport: string;
  base_url: string;
  notes: string | null;
  current_version: number;
  has_key: boolean;
  api_key_last4: string | null;
  versions: ProviderVersion[];
  created_at: string;
  updated_at: string;
};

export type PromptVersion = {
  version: number;
  content: string;
  notes: string | null;
  created_at: string;
};

export type PromptItem = {
  id: number;
  name: string;
  stage: string;
  description: string | null;
  current_version: number;
  versions: PromptVersion[];
};

export type AgentVersion = {
  version: number;
  provider_config_id: number;
  provider_config_version: number;
  provider_config_name: string;
  model: string;
  prompt_id: number;
  prompt_version: number;
  prompt_name: string;
  params: Record<string, unknown> | null;
  notes: string | null;
  created_at: string;
};

export type AgentItem = {
  id: number;
  name: string;
  description: string | null;
  current_version: number;
  versions: AgentVersion[];
};

function invalidate(qc: ReturnType<typeof useQueryClient>, keys: string[][]) {
  for (const key of keys) void qc.invalidateQueries({ queryKey: key });
}

export function useProvidersQuery() {
  return useQuery<ProviderConfig[]>({
    queryKey: ['config-center', 'providers'],
    queryFn: async () => {
      const res = await client.get<Envelope<{ items: ProviderConfig[] }>>(
        '/config-center/providers',
      );
      return res.data.data.items;
    },
  });
}

export function usePromptsQuery() {
  return useQuery<PromptItem[]>({
    queryKey: ['config-center', 'prompts'],
    queryFn: async () => {
      const res = await client.get<Envelope<{ items: PromptItem[] }>>(
        '/config-center/prompts',
      );
      return res.data.data.items;
    },
  });
}

export function useAgentsQuery() {
  return useQuery<AgentItem[]>({
    queryKey: ['config-center', 'agents'],
    queryFn: async () => {
      const res = await client.get<Envelope<{ items: AgentItem[] }>>(
        '/config-center/agents',
      );
      return res.data.data.items;
    },
  });
}

export function useProviderModelsQuery(providerId: number | null) {
  return useQuery<string[]>({
    queryKey: ['config-center', 'provider-models', providerId],
    enabled: providerId !== null,
    retry: false,
    queryFn: async () => {
      const res = await client.get<Envelope<{ models: string[] }>>(
        `/config-center/providers/${providerId}/models`,
      );
      return res.data.data.models;
    },
  });
}

export function useCreateProviderMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      name: string;
      transport: string;
      base_url: string;
      api_key: string;
      notes?: string | null;
    }) => {
      const res = await client.post<Envelope<unknown>>(
        '/config-center/providers',
        payload,
      );
      return res.data;
    },
    onSuccess: () => invalidate(qc, [['config-center', 'providers']]),
  });
}

export function useUpdateProviderMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      id: number;
      body: Record<string, unknown>;
    }) => {
      const res = await client.patch<Envelope<unknown>>(
        `/config-center/providers/${payload.id}`,
        payload.body,
      );
      return res.data;
    },
    onSuccess: () => invalidate(qc, [['config-center', 'providers']]),
  });
}

export function useRollbackMutation(kind: 'providers' | 'prompts' | 'agents') {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { id: number; version: number }) => {
      const res = await client.post<Envelope<unknown>>(
        `/config-center/${kind}/${payload.id}/rollback`,
        { version: payload.version },
      );
      return res.data;
    },
    onSuccess: () => invalidate(qc, [['config-center', kind]]),
  });
}

export function useCreatePromptMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      name: string;
      stage: string;
      content: string;
      description?: string | null;
    }) => {
      const res = await client.post<Envelope<unknown>>(
        '/config-center/prompts',
        payload,
      );
      return res.data;
    },
    onSuccess: () => invalidate(qc, [['config-center', 'prompts']]),
  });
}

export function useUpdatePromptMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { id: number; content: string }) => {
      const res = await client.patch<Envelope<unknown>>(
        `/config-center/prompts/${payload.id}`,
        { content: payload.content },
      );
      return res.data;
    },
    onSuccess: () => invalidate(qc, [['config-center', 'prompts']]),
  });
}

export function useCreateAgentMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      name: string;
      description?: string | null;
      provider_config_id: number;
      model: string;
      prompt_id: number;
    }) => {
      const res = await client.post<Envelope<unknown>>(
        '/config-center/agents',
        payload,
      );
      return res.data;
    },
    onSuccess: () => invalidate(qc, [['config-center', 'agents']]),
  });
}

export function useUpdateAgentMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      id: number;
      body: Record<string, unknown>;
    }) => {
      const res = await client.patch<Envelope<unknown>>(
        `/config-center/agents/${payload.id}`,
        payload.body,
      );
      return res.data;
    },
    onSuccess: () => invalidate(qc, [['config-center', 'agents']]),
  });
}
