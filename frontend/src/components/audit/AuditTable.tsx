import { Table, Tag } from '@douyinfe/semi-ui';
import type { ColumnProps } from '@douyinfe/semi-ui/lib/es/table';
import type { AuditItem } from '@/api/audit';

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return '-';
  if (typeof v === 'string') return v.length > 80 ? v.slice(0, 80) + '...' : v;
  const s = JSON.stringify(v);
  return s.length > 80 ? s.slice(0, 80) + '...' : s;
}

const columns: ColumnProps<AuditItem>[] = [
  {
    title: 'Time',
    dataIndex: 'created_at',
    render: (v: string) => (
      <span className="whitespace-nowrap text-gray-600">
        {new Date(v).toLocaleString()}
      </span>
    ),
  },
  {
    title: 'Editor',
    render: (_: unknown, item: AuditItem) => item.editor.display_name,
  },
  {
    title: 'Word ID',
    dataIndex: 'word_id',
    render: (v: number) => <span className="font-mono">{v}</span>,
  },
  {
    title: 'Field',
    dataIndex: 'field_path',
    render: (_: unknown, item: AuditItem) => (
      <span className="font-mono text-xs">
        {item.field_path}
        {item.target_id != null && (
          <span className="ml-1 text-gray-400">#{item.target_id}</span>
        )}
      </span>
    ),
  },
  {
    title: 'Op',
    dataIndex: 'op',
    render: (op: string) => (
      <Tag size="small" color="grey">
        {op}
      </Tag>
    ),
  },
  {
    title: 'Old',
    dataIndex: 'old_value',
    render: (v: unknown) => (
      <span className="max-w-[200px] truncate font-mono text-xs text-red-700">
        {formatValue(v)}
      </span>
    ),
  },
  {
    title: 'New',
    dataIndex: 'new_value',
    render: (v: unknown) => (
      <span className="max-w-[200px] truncate font-mono text-xs text-green-700">
        {formatValue(v)}
      </span>
    ),
  },
];

type AuditTableProps = {
  items: AuditItem[];
};

export function AuditTable({ items }: AuditTableProps) {
  return (
    <Table
      columns={columns}
      dataSource={items}
      rowKey="id"
      pagination={false}
    />
  );
}
