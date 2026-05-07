import { useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import toast from 'react-hot-toast';
import { useQueryClient } from '@tanstack/react-query';
import { ApiError } from '@/api/client';
import {
  useWordDetailQuery,
  usePatchWordMutation,
  useChangeStatusMutation,
  useChangeQualityMutation,
} from '@/api/wordDetail';
import { WordEditForm } from '@/components/words/WordEditForm';
import { DiffPreviewModal } from '@/components/words/DiffPreviewModal';
import { StatusQualityToggle } from '@/components/words/StatusQualityToggle';
import type { Change } from '@/lib/diffChanges';

export function WordDetail() {
  const { id } = useParams();
  const wordId = Number(id);
  const queryClient = useQueryClient();

  const { data, isLoading, error } = useWordDetailQuery(wordId);
  const patch = usePatchWordMutation(wordId);
  const changeStatus = useChangeStatusMutation(wordId);
  const changeQuality = useChangeQualityMutation(wordId);

  const [pendingChanges, setPendingChanges] = useState<Change[]>([]);
  const [showDiff, setShowDiff] = useState(false);

  if (isLoading) {
    return (
      <div className="py-12 text-center text-gray-500">Loading...</div>
    );
  }

  if (error) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-6">
        <div className="rounded border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error instanceof ApiError ? error.message : 'Failed to load word'}
        </div>
      </div>
    );
  }

  if (!data) return null;

  function handleSubmitChanges(changes: Change[]) {
    if (changes.length === 0) {
      toast.success('No changes to submit');
      return;
    }
    setPendingChanges(changes);
    setShowDiff(true);
  }

  function handleConfirm() {
    patch.mutate(
      { changes: pendingChanges },
      {
        onSuccess: () => {
          toast.success('Changes saved');
          setShowDiff(false);
          setPendingChanges([]);
        },
        onError: (e) => {
          if (e instanceof ApiError && e.code === 'conflict') {
            toast.error('Conflict: someone else modified this word. Please refresh and retry.');
            setShowDiff(false);
            // Invalidate to fetch latest, but preserve user form state
            void queryClient.invalidateQueries({
              queryKey: ['words', 'detail', wordId],
            });
          } else {
            toast.error(e instanceof ApiError ? e.message : 'Save failed');
          }
        },
      },
    );
  }

  function handleStatusChange(oldV: number, newV: number) {
    changeStatus.mutate(
      { old_value: oldV, new_value: newV },
      {
        onSuccess: () => toast.success('Status updated'),
        onError: (e) => {
          if (e instanceof ApiError && e.code === 'conflict') {
            toast.error('Status conflict: refresh and retry');
          } else {
            toast.error(e instanceof ApiError ? e.message : 'Failed');
          }
        },
      },
    );
  }

  function handleQualityChange(oldV: string, newV: string) {
    changeQuality.mutate(
      { old_value: oldV, new_value: newV },
      {
        onSuccess: () => toast.success('Quality updated'),
        onError: (e) => {
          if (e instanceof ApiError && e.code === 'conflict') {
            toast.error('Quality conflict: refresh and retry');
          } else {
            toast.error(e instanceof ApiError ? e.message : 'Failed');
          }
        },
      },
    );
  }

  return (
    <div className="mx-auto max-w-4xl px-4 py-6">
      <div className="mb-4 flex items-center gap-3">
        <Link
          to="/"
          className="text-sm text-blue-600 hover:underline"
        >
          &larr; Back to list
        </Link>
        <h1 className="text-2xl font-bold text-gray-900">
          {data.word.form}
        </h1>
        <Link
          to={`/audit?word_id=${wordId}`}
          className="ml-auto text-sm text-blue-600 hover:underline"
        >
          Audit Log
        </Link>
      </div>

      {/* Status / Quality toggle — independent mutations */}
      <div className="mb-6">
        <StatusQualityToggle
          status={data.word.status}
          qualityFlag={data.word.quality_flag}
          onChangeStatus={handleStatusChange}
          onChangeQuality={handleQualityChange}
          disabled={changeStatus.isPending || changeQuality.isPending}
        />
      </div>

      <WordEditForm defaults={data} onSubmitChanges={handleSubmitChanges}>
        {showDiff && (
          <DiffPreviewModal
            changes={pendingChanges}
            onConfirm={handleConfirm}
            onCancel={() => setShowDiff(false)}
            isPending={patch.isPending}
          />
        )}
      </WordEditForm>
    </div>
  );
}
