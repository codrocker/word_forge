import { Button, Modal, Table } from '@douyinfe/semi-ui';
import type { ColumnProps } from '@douyinfe/semi-ui/lib/es/table';
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

const columns: ColumnProps<Change>[] = [
  {
    title: 'Field',
    dataIndex: 'field_path',
    render: (_: unknown, c: Change) => (
      <span className="font-mono text-xs text-gray-700">
        {c.field_path}
        {c.target_id != null && (
          <span className="ml-1 text-gray-400">#{c.target_id}</span>
        )}
      </span>
    ),
  },
  {
    title: 'Old',
    dataIndex: 'old_value',
    render: (v: unknown) => (
      <span className="font-mono text-xs text-red-600">{formatValue(v)}</span>
    ),
  },
  {
    title: 'New',
    dataIndex: 'new_value',
    render: (v: unknown) => (
      <span className="font-mono text-xs text-green-600">{formatValue(v)}</span>
    ),
  },
];

export function DiffPreviewModal({
  changes,
  onConfirm,
  onCancel,
  isPending,
}: DiffPreviewModalProps) {
  return (
    <Modal
      title="Confirm Changes"
      visible
      onCancel={onCancel}
      footer={null}
      width={640}
    >
      <Table
        columns={columns}
        dataSource={changes}
        rowKey={(c) => `${c?.field_path ?? ''}#${c?.target_id ?? ''}`}
        pagination={false}
        size="small"
        scroll={{ y: 320 }}
      />
      <div className="mt-4 flex justify-end gap-3">
        <Button
          theme="light"
          htmlType="button"
          onClick={onCancel}
          disabled={isPending}
        >
          Cancel
        </Button>
        <Button
          theme="solid"
          htmlType="button"
          onClick={onConfirm}
          loading={isPending}
        >
          {isPending ? 'Saving...' : `Apply ${changes.length} change(s)`}
        </Button>
      </div>
    </Modal>
  );
}
