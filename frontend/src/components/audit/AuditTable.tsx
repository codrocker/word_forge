import type { AuditItem } from '@/api/audit';

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return '-';
  if (typeof v === 'string') return v.length > 80 ? v.slice(0, 80) + '...' : v;
  const s = JSON.stringify(v);
  return s.length > 80 ? s.slice(0, 80) + '...' : s;
}

type AuditTableProps = {
  items: AuditItem[];
};

export function AuditTable({ items }: AuditTableProps) {
  return (
    <div className="overflow-x-auto rounded border border-gray-200">
      <table className="min-w-full text-left text-sm">
        <thead className="border-b bg-gray-50 text-xs uppercase text-gray-600">
          <tr>
            <th className="px-3 py-2">Time</th>
            <th className="px-3 py-2">Editor</th>
            <th className="px-3 py-2">Word ID</th>
            <th className="px-3 py-2">Field</th>
            <th className="px-3 py-2">Op</th>
            <th className="px-3 py-2">Old</th>
            <th className="px-3 py-2">New</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {items.map((item) => (
            <tr key={item.id} className="hover:bg-gray-50">
              <td className="whitespace-nowrap px-3 py-2 text-gray-600">
                {new Date(item.created_at).toLocaleString()}
              </td>
              <td className="px-3 py-2">{item.editor.display_name}</td>
              <td className="px-3 py-2 font-mono">{item.word_id}</td>
              <td className="px-3 py-2 font-mono text-xs">
                {item.field_path}
                {item.target_id != null && (
                  <span className="ml-1 text-gray-400">#{item.target_id}</span>
                )}
              </td>
              <td className="px-3 py-2">
                <span className="rounded bg-gray-100 px-1.5 py-0.5 text-xs font-medium">
                  {item.op}
                </span>
              </td>
              <td className="max-w-[200px] truncate px-3 py-2 font-mono text-xs text-red-700">
                {formatValue(item.old_value)}
              </td>
              <td className="max-w-[200px] truncate px-3 py-2 font-mono text-xs text-green-700">
                {formatValue(item.new_value)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
