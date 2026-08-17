import { useState, useEffect, useRef, useCallback } from 'react';
import { useSearchParams, useNavigate, Link } from 'react-router-dom';
import { useSearchWordsQuery } from '@/api/words';
import { ApiError } from '@/api/client';
import { WordTable } from '@/components/words/WordTable';
import { FilterBar } from '@/components/words/FilterBar';
import { Pagination } from '@/components/words/Pagination';

/** Debounce a callback that updates URL search params. */
function useDebouncedCallback<T extends (...args: never[]) => void>(
  callback: T,
  delay: number,
): T {
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const callbackRef = useRef(callback);
  callbackRef.current = callback;

  useEffect(() => {
    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    };
  }, []);

  return useCallback(
    (...args: Parameters<T>) => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
      timeoutRef.current = setTimeout(() => {
        callbackRef.current(...args);
      }, delay);
    },
    [delay],
  ) as unknown as T;
}

export function Search() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();

  // Derive query params from URL (single source of truth)
  const params = {
    q: searchParams.get('q') ?? undefined,
    status: searchParams.get('status')
      ? Number(searchParams.get('status'))
      : undefined,
    quality: searchParams.get('quality') ?? undefined,
    type: searchParams.get('type')
      ? Number(searchParams.get('type'))
      : undefined,
    cursor: searchParams.get('cursor') ?? undefined,
    limit: 50,
  };

  const { data, isLoading, error } = useSearchWordsQuery(params);

  // Local input state for responsive typing; debounce updates URL
  const [qInput, setQInput] = useState(params.q ?? '');

  // Sync local input when URL changes externally (e.g. browser back)
  useEffect(() => {
    setQInput(searchParams.get('q') ?? '');
  }, [searchParams]);

  const debouncedSetQ = useDebouncedCallback((value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set('q', value);
    else next.delete('q');
    next.delete('cursor');
    setSearchParams(next, { replace: true });
  }, 300);

  function handleQChange(value: string) {
    setQInput(value);
    debouncedSetQ(value);
  }

  function handleFilterChange(key: string, value: string) {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    next.delete('cursor');
    setSearchParams(next, { replace: true });
  }

  function handleNext() {
    if (!data?.next_cursor) return;
    const next = new URLSearchParams(searchParams);
    next.set('cursor', data.next_cursor);
    setSearchParams(next, { replace: true });
  }

  function handleReset() {
    const next = new URLSearchParams(searchParams);
    next.delete('cursor');
    setSearchParams(next, { replace: true });
  }

  return (
    <div className="mx-auto max-w-7xl px-4 py-6">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Words</h1>
        <div className="flex items-center gap-4">
          <Link to="/experiments" className="text-sm text-blue-600 hover:underline">
            LLM 实验 &rarr;
          </Link>
          <Link to="/config-center" className="text-sm text-blue-600 hover:underline">
            配置中心 &rarr;
          </Link>
          <Link to="/audit" className="text-sm text-blue-600 hover:underline">
            Audit Log &rarr;
          </Link>
        </div>
      </div>

      <div className="mb-4">
        <FilterBar
          qValue={qInput}
          onQChange={handleQChange}
          status={searchParams.get('status') ?? ''}
          onStatusChange={(v) => handleFilterChange('status', v)}
          quality={searchParams.get('quality') ?? ''}
          onQualityChange={(v) => handleFilterChange('quality', v)}
          type={searchParams.get('type') ?? ''}
          onTypeChange={(v) => handleFilterChange('type', v)}
        />
      </div>

      {isLoading && (
        <div className="py-12 text-center text-gray-500">Loading...</div>
      )}

      {error && (
        <div className="rounded border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error instanceof ApiError ? error.message : 'Failed to load words'}
        </div>
      )}

      {data && data.items.length === 0 && (
        <div className="py-12 text-center text-gray-500">
          No matching words found.
        </div>
      )}

      {data && data.items.length > 0 && (
        <>
          <WordTable
            items={data.items}
            onRowClick={(id) => navigate(`/words/${id}`)}
          />
          <div className="mt-4">
            <Pagination
              hasNext={!!data.next_cursor}
              onNext={handleNext}
              onReset={handleReset}
              hasCursor={!!params.cursor}
            />
          </div>
        </>
      )}
    </div>
  );
}
