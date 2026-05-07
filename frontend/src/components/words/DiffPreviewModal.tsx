import { AppModal } from '@/components/app/AppModal';
import { AppButton } from '@/components/app/AppButton';
import type { Change } from '@/lib/diffChanges';

type DiffPreviewModalProps = {
  changes: Change[];
  onConfirm: () => void;
  onCancel: () => void;
  isPending: boolean;
};

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return '(empty)';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

export function DiffPreviewModal({
  changes,
  onConfirm,
  onCancel,
  isPending,
}: DiffPreviewModalProps) {
  return (
    <AppModal open={true} onClose={onCancel} title="Confirm Changes">
      <div className="max-h-80 overflow-y-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-gray-600">
              <th className="pb-2 pr-2">Field</th>
              <th className="pb-2 pr-2">Old</th>
              <th className="pb-2">New</th>
            </tr>
          </thead>
          <tbody>
            {changes.map((c, i) => (
              <tr key={i} className="border-b last:border-b-0">
                <td className="py-2 pr-2 font-mono text-xs text-gray-700">
                  {c.field_path}
                  {c.target_id != null && (
                    <span className="ml-1 text-gray-400">#{c.target_id}</span>
                  )}
                </td>
                <td className="py-2 pr-2 text-red-600">
                  {formatValue(c.old_value)}
                </td>
                <td className="py-2 text-green-600">
                  {formatValue(c.new_value)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-4 flex justify-end gap-3">
        <AppButton variant="secondary" onClick={onCancel} disabled={isPending}>
          Cancel
        </AppButton>
        <AppButton onClick={onConfirm} disabled={isPending}>
          {isPending ? 'Saving...' : `Apply ${changes.length} change(s)`}
        </AppButton>
      </div>
    </AppModal>
  );
}
