import { Table, Tag } from '@douyinfe/semi-ui';
import type { ColumnProps } from '@douyinfe/semi-ui/lib/es/table';
import type { WordListItem } from '@/api/words';

const STATUS_TAG: Record<
  number,
  { label: string; color: 'orange' | 'green' | 'grey' }
> = {
  0: { label: 'Draft', color: 'orange' },
  1: { label: 'Published', color: 'green' },
  2: { label: 'Archived', color: 'grey' },
};

const TYPE_LABEL: Record<number, string> = { 1: 'Word', 2: 'Phrase' };

const columns: ColumnProps<WordListItem>[] = [
  {
    title: 'ID',
    dataIndex: 'word_id',
    render: (id: number) => (
      <span className="font-mono text-xs text-gray-500">{id}</span>
    ),
  },
  {
    title: 'Form',
    dataIndex: 'form',
    render: (form: string) => (
      <span className="font-medium text-gray-900">{form}</span>
    ),
  },
  {
    title: 'Type',
    dataIndex: 'type',
    render: (t: number) => TYPE_LABEL[t] ?? String(t),
  },
  {
    title: 'Status',
    dataIndex: 'status',
    render: (status: number) => {
      const tag = STATUS_TAG[status];
      return tag ? (
        <Tag color={tag.color} size="small">
          {tag.label}
        </Tag>
      ) : (
        String(status)
      );
    },
  },
  {
    title: 'Quality',
    dataIndex: 'quality_flag',
  },
  {
    title: 'Meanings',
    dataIndex: 'meaning_count',
  },
  {
    title: 'Updated',
    dataIndex: 'updated_at',
    render: (v: string) => <span className="text-xs text-gray-500">{v}</span>,
  },
];

type WordTableProps = {
  items: WordListItem[];
  onRowClick: (wordId: number) => void;
};

export function WordTable({ items, onRowClick }: WordTableProps) {
  return (
    <Table
      columns={columns}
      dataSource={items}
      rowKey="word_id"
      pagination={false}
      onRow={(record) => ({
        onClick: () => onRowClick(record!.word_id),
        style: { cursor: 'pointer' },
      })}
    />
  );
}
