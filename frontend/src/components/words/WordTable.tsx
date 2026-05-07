import type { WordListItem } from '@/api/words';

type WordTableProps = {
  items: WordListItem[];
  onRowClick: (wordId: number) => void;
};

function formatStatus(status: number): string {
  switch (status) {
    case 0:
      return 'Draft';
    case 1:
      return 'Published';
    case 2:
      return 'Archived';
    default:
      return String(status);
  }
}

function formatType(type: number): string {
  switch (type) {
    case 1:
      return 'Word';
    case 2:
      return 'Phrase';
    default:
      return String(type);
  }
}

export function WordTable({ items, onRowClick }: WordTableProps) {
  return (
    <div className="overflow-x-auto rounded border border-gray-200">
      <table className="w-full text-left text-sm">
        <thead className="border-b bg-gray-50 text-xs uppercase text-gray-600">
          <tr>
            <th className="px-4 py-3">ID</th>
            <th className="px-4 py-3">Form</th>
            <th className="px-4 py-3">Type</th>
            <th className="px-4 py-3">Status</th>
            <th className="px-4 py-3">Quality</th>
            <th className="px-4 py-3">Meanings</th>
            <th className="px-4 py-3">Updated</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {items.map((item) => (
            <tr
              key={item.word_id}
              onClick={() => onRowClick(item.word_id)}
              className="cursor-pointer hover:bg-blue-50 transition-colors"
            >
              <td className="px-4 py-3 font-mono text-xs text-gray-500">
                {item.word_id}
              </td>
              <td className="px-4 py-3 font-medium text-gray-900">
                {item.form}
              </td>
              <td className="px-4 py-3">{formatType(item.type)}</td>
              <td className="px-4 py-3">{formatStatus(item.status)}</td>
              <td className="px-4 py-3">{item.quality_flag}</td>
              <td className="px-4 py-3">{item.meaning_count}</td>
              <td className="px-4 py-3 text-xs text-gray-500">
                {item.updated_at}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
