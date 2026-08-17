import { useQuery, keepPreviousData } from '@tanstack/react-query';
import client from './client';
import type { Envelope } from './types';

export type WordListItem = {
  word_id: number;
  form: string;
  type: number;
  status: number;
  quality_flag: string;
  updated_at: string;
  meaning_count: number;
};

export type WordListResponse = {
  items: WordListItem[];
  next_cursor: string | null;
};

export type SearchWordsParams = {
  q?: string;
  status?: number;
  quality?: string;
  type?: number;
  pos?: number;
  cursor?: string;
  limit?: number;
};

export function useSearchWordsQuery(params: SearchWordsParams) {
  return useQuery<WordListResponse>({
    queryKey: ['words', 'list', params],
    queryFn: async () => {
      const res = await client.get<Envelope<WordListResponse>>('/words', {
        params,
      });
      return res.data.data;
    },
    placeholderData: keepPreviousData,
  });
}
