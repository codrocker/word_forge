import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import client from './client';
import type { Editor, Envelope } from './types';

interface LoginPayload {
  email: string;
  password: string;
}

export function useMeQuery() {
  return useQuery<Editor>({
    queryKey: ['me'],
    queryFn: async () => {
      const res = await client.get<Envelope<Editor>>('/auth/me');
      return res.data.data;
    },
    staleTime: Infinity,
    retry: false,
  });
}

export function useLoginMutation() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  return useMutation({
    mutationFn: async (payload: LoginPayload) => {
      await client.post<Envelope<null>>('/auth/login', payload);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['me'] });
      navigate('/');
    },
  });
}

export function useLogoutMutation() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  return useMutation({
    mutationFn: async () => {
      await client.post<Envelope<null>>('/auth/logout');
    },
    onSuccess: () => {
      queryClient.clear();
      navigate('/login');
    },
  });
}
