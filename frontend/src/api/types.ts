/** Backend envelope response shape. */
export interface Envelope<T> {
  ok: boolean;
  data: T;
  error?: { code: string; message: string };
}

/** Editor user returned by GET /me. */
export interface Editor {
  id: number;
  email: string;
  name: string;
  role: string;
  is_active: boolean;
}
