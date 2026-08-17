import axios from 'axios';
import type { Envelope } from './types';

/** Custom error thrown when API returns ok=false. */
export class ApiError extends Error {
  code: string;
  requestId?: string;
  details?: Record<string, unknown>;

  constructor(code: string, message: string, requestId?: string, details?: Record<string, unknown>) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.requestId = requestId;
    this.details = details;
  }
}

const client = axios.create({
  baseURL: '/api/v1',
  withCredentials: true,
  headers: { 'Content-Type': 'application/json' },
});

client.interceptors.response.use(
  (response) => {
    const body = response.data as Envelope<unknown>;
    if (!body.ok) {
      const err = body.error ?? { code: 'UNKNOWN', message: 'Unknown error' };
      const requestId =
        (response.headers['x-request-id'] as string | undefined) ??
        (response.headers['X-Request-ID'] as string | undefined);
      throw new ApiError(err.code, err.message, requestId);
    }
    return response;
  },
  (error: unknown) => {
    if (axios.isAxiosError(error)) {
      const requestId =
        (error.response?.headers?.['x-request-id'] as string | undefined) ??
        (error.response?.headers?.['X-Request-ID'] as string | undefined);
      if (error.response) {
        const body = error.response.data as Envelope<unknown> | undefined;
        const err = body?.error ?? { code: 'UNKNOWN', message: error.message };
        throw new ApiError(err.code, err.message, requestId);
      }
      throw new ApiError('NETWORK', error.message);
    }
    throw error;
  },
);

export default client;
