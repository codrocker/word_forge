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
      <label className="flex flex-col text-sm text-gray-700">
        Word ID
        <input
          type="text"
          inputMode="numeric"
          value={wordId}
          onChange={(e) => onWordIdChange(e.target.value)}
          placeholder="e.g. 100123"
          className="mt-1 w-32 rounded border border-gray-300 px-2 py-1 text-sm"
        />
      </label>

      <label className="flex flex-col text-sm text-gray-700">
        Editor ID
        <input
          type="text"
          inputMode="numeric"
          value={editorId}
          onChange={(e) => onEditorIdChange(e.target.value)}
          placeholder="e.g. 1"
          className="mt-1 w-28 rounded border border-gray-300 px-2 py-1 text-sm"
        />
      </label>

      <label className="flex flex-col text-sm text-gray-700">
        Since
        <input
          type="date"
          value={since}
          onChange={(e) => onSinceChange(e.target.value)}
          className="mt-1 rounded border border-gray-300 px-2 py-1 text-sm"
        />
      </label>

      <label className="flex flex-col text-sm text-gray-700">
        Until
        <input
          type="date"
          value={until}
          onChange={(e) => onUntilChange(e.target.value)}
          className="mt-1 rounded border border-gray-300 px-2 py-1 text-sm"
        />
      </label>
    </div>
  );
}
