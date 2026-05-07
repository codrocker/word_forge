import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import client from './client';
import type { Envelope } from './types';
import type { WordDetailForm } from '@/lib/diffChanges';
import type { Change } from '@/lib/diffChanges';

export type WordDetailResponse = WordDetailForm;

export function useWordDetailQuery(id: number) {
  return useQuery<WordDetailResponse>({
    queryKey: ['words', 'detail', id],
    queryFn: async () => {
      const res = await client.get<Envelope<WordDetailResponse>>(
        `/words/${id}`,
      );
      return res.data.data;
    },
    enabled: id > 0,
  });
}

export function usePatchWordMutation(id: number) {
  const queryClient = useQueryClient();
  return useMutation<{ applied: number }, Error, { changes: Change[] }>({
    mutationFn: async (body) => {
      const res = await client.patch<Envelope<{ applied: number }>>(
        `/words/${id}`,
        body,
      );
      return res.data.data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ['words', 'detail', id],
      });
    },
    // onError: do NOT invalidate — preserve dirty state
  });
}

export function useChangeStatusMutation(id: number) {
  const queryClient = useQueryClient();
  return useMutation<
    unknown,
    Error,
    { old_value: number; new_value: number }
  >({
    mutationFn: async (body) => {
      const res = await client.post<Envelope<unknown>>(
        `/words/${id}/status`,
        body,
      );
      return res.data.data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ['words', 'detail', id],
      });
    },
  });
}

export function useChangeQualityMutation(id: number) {
  const queryClient = useQueryClient();
  return useMutation<
    unknown,
    Error,
    { old_value: string; new_value: string }
  >({
    mutationFn: async (body) => {
      const res = await client.post<Envelope<unknown>>(
        `/words/${id}/quality`,
        body,
      );
      return res.data.data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ['words', 'detail', id],
      });
    },
  });
}
