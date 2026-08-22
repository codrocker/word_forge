import { DatePicker, Input } from '@douyinfe/semi-ui';

type AuditFilterBarProps = {
  wordId: string;
  onWordIdChange: (v: string) => void;
  editorId: string;
  onEditorIdChange: (v: string) => void;
  since: string;
  onSinceChange: (v: string) => void;
  until: string;
  onUntilChange: (v: string) => void;
};

/** 'YYYY-MM-DD' → 本地时区 Date（直接 new Date(str) 会按 UTC 解析，西半球会偏一天）。 */
function parseLocalDate(s: string): Date | undefined {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  if (!m) return undefined;
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
}

export function AuditFilterBar({
  wordId,
  onWordIdChange,
  editorId,
  onEditorIdChange,
  since,
  onSinceChange,
  until,
  onUntilChange,
}: AuditFilterBarProps) {
  return (
    <div className="flex flex-wrap items-end gap-4">
      <label className="flex flex-col gap-1 text-sm text-gray-700">
        Word ID
        <Input
          style={{ width: 140 }}
          placeholder="e.g. 100123"
          value={wordId}
          onChange={(v) => onWordIdChange(v)}
        />
      </label>

      <label className="flex flex-col gap-1 text-sm text-gray-700">
        Editor ID
        <Input
          style={{ width: 120 }}
          placeholder="e.g. 1"
          value={editorId}
          onChange={(v) => onEditorIdChange(v)}
        />
      </label>

      <label className="flex flex-col gap-1 text-sm text-gray-700">
        Since
        <DatePicker
          type="date"
          format="yyyy-MM-dd"
          style={{ width: 160 }}
          placeholder="Since"
          value={parseLocalDate(since)}
          onChange={(formatted) =>
            onSinceChange(typeof formatted === 'string' ? formatted : '')
          }
        />
      </label>

      <label className="flex flex-col gap-1 text-sm text-gray-700">
        Until
        <DatePicker
          type="date"
          format="yyyy-MM-dd"
          style={{ width: 160 }}
          placeholder="Until"
          value={parseLocalDate(until)}
          onChange={(formatted) =>
            onUntilChange(typeof formatted === 'string' ? formatted : '')
          }
        />
      </label>
    </div>
  );
}
