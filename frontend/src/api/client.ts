import axios from 'axios';
import type { Envelope } from './types';

/** Custom error thrown when API returns ok=false. */
export class ApiError extends Error {
  code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
  }
}

const client = axios.create({
  baseURL: '/api/v1',
  withCredentials: true,
  headers: { 'Content-Type': 'application/json' },
});

client.interceptors.response.use((response) => {
  const body = response.data as Envelope<unknown>;
  if (!body.ok) {
    const err = body.error ?? { code: 'UNKNOWN', message: 'Unknown error' };
    throw new ApiError(err.code, err.message);
  }
  return response;
});

export default client;
