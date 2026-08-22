import { useState, useEffect, useRef, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Banner, Button, Empty, Spin, Typography } from '@douyinfe/semi-ui';
import { useAuditListQuery } from '@/api/audit';
import { ApiError } from '@/api/client';
import { AuditFilterBar } from '@/components/audit/AuditFilterBar';
import { AuditTable } from '@/components/audit/AuditTable';

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

export function Audit() {
  const [searchParams, setSearchParams] = useSearchParams();

  // URL is SSR
  const params = {
    word_id: searchParams.get('word_id') ?? undefined,
    editor_id: searchParams.get('editor_id') ?? undefined,
    since: searchParams.get('since') ?? undefined,
    until: searchParams.get('until') ?? undefined,
    cursor: searchParams.get('cursor') ?? undefined,
    limit: 50,
  };

  const { data, isLoading, error } = useAuditListQuery(params);

  // Local input state for debounce
  const [wordIdInput, setWordIdInput] = useState(params.word_id ?? '');
  const [editorIdInput, setEditorIdInput] = useState(params.editor_id ?? '');

  // Sync local inputs when URL changes externally
  useEffect(() => {
    setWordIdInput(searchParams.get('word_id') ?? '');
    setEditorIdInput(searchParams.get('editor_id') ?? '');
  }, [searchParams]);

  const debouncedSetParam = useDebouncedCallback(
    (key: string, value: string) => {
      const next = new URLSearchParams(searchParams);
      if (value) next.set(key, value);
      else next.delete(key);
      next.delete('cursor');
      setSearchParams(next, { replace: true });
    },
    300,
  );

  function handleWordIdChange(value: string) {
    setWordIdInput(value);
    debouncedSetParam('word_id', value);
  }

  function handleEditorIdChange(value: string) {
    setEditorIdInput(value);
    debouncedSetParam('editor_id', value);
  }

  function handleDateChange(key: 'since' | 'until', value: string) {
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

  return (
    <div className="mx-auto max-w-7xl px-4 py-6">
      <Typography.Title heading={4} className="mb-4">
        Audit Log
      </Typography.Title>

      <div className="mb-4">
        <AuditFilterBar
          wordId={wordIdInput}
          onWordIdChange={handleWordIdChange}
          editorId={editorIdInput}
          onEditorIdChange={handleEditorIdChange}
          since={searchParams.get('since') ?? ''}
          onSinceChange={(v) => handleDateChange('since', v)}
          until={searchParams.get('until') ?? ''}
          onUntilChange={(v) => handleDateChange('until', v)}
        />
      </div>

      {isLoading && (
        <div className="py-12 text-center">
          <Spin size="large" />
        </div>
      )}

      {error && (
        <Banner
          type="danger"
          description={
            error instanceof ApiError
              ? error.message
              : 'Failed to load audit log'
          }
        />
      )}

      {data && data.items.length === 0 && (
        <div className="py-12">
          <Empty title="No audit entries found." />
        </div>
      )}

      {data && data.items.length > 0 && (
        <>
          <AuditTable items={data.items} />
          <div className="mt-4 flex items-center gap-3">
            {data.next_cursor && (
              <Button theme="solid" htmlType="button" onClick={handleNext}>
                Next page
              </Button>
            )}
            {params.cursor && (
              <Button
                theme="light"
                htmlType="button"
                onClick={() => {
                  const next = new URLSearchParams(searchParams);
                  next.delete('cursor');
                  setSearchParams(next, { replace: true });
                }}
              >
                Back to first page
              </Button>
            )}
          </div>
        </>
      )}
    </div>
  );
}
