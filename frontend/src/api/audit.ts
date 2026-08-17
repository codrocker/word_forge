import { useQuery, keepPreviousData } from '@tanstack/react-query';
import client from './client';
import type { Envelope } from './types';

export type AuditEditor = {
  id: number;
  display_name: string;
};

export type AuditItem = {
  id: number;
  word_id: number;
  field_path: string;
  target_id: number | null;
  op: string;
  old_value: unknown;
  new_value: unknown;
  editor: AuditEditor;
  created_at: string;
};

export type AuditListResponse = {
  items: AuditItem[];
  next_cursor: string | null;
};

export type AuditListParams = {
  word_id?: string;
  editor_id?: string;
  since?: string;
  until?: string;
  cursor?: string;
  limit?: number;
};

export function useAuditListQuery(params: AuditListParams) {
  return useQuery<AuditListResponse>({
    queryKey: ['audit', 'list', params],
    queryFn: async () => {
      const res = await client.get<Envelope<AuditListResponse>>('/audit', {
        params,
      });
      return res.data.data;
    },
    placeholderData: keepPreviousData,
  });
}
