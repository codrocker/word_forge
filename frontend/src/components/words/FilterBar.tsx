import { AppSelect } from '@/components/app/AppSelect';

const STATUS_OPTIONS = [
  { label: 'Draft (0)', value: '0' },
  { label: 'Published (1)', value: '1' },
  { label: 'Archived (2)', value: '2' },
];

const QUALITY_OPTIONS = [
  { label: 'Good', value: 'good' },
  { label: 'Needs Review', value: 'needs_review' },
  { label: 'Bad', value: 'bad' },
];

const TYPE_OPTIONS = [
  { label: 'Word (1)', value: '1' },
  { label: 'Phrase (2)', value: '2' },
];

type FilterBarProps = {
  qValue: string;
  onQChange: (value: string) => void;
  status: string;
  onStatusChange: (value: string) => void;
  quality: string;
  onQualityChange: (value: string) => void;
  type: string;
  onTypeChange: (value: string) => void;
};

export function FilterBar({
  qValue,
  onQChange,
  status,
  onStatusChange,
  quality,
  onQualityChange,
  type,
  onTypeChange,
}: FilterBarProps) {
  return (
    <div className="flex flex-wrap items-end gap-4">
      <div className="flex flex-col gap-1">
        <label className="text-sm font-medium text-gray-700">Search</label>
        <input
          type="text"
          value={qValue}
          onChange={(e) => onQChange(e.target.value)}
          placeholder="Search by word form..."
          className="rounded border border-gray-300 px-3 py-2 text-sm outline-none transition-colors focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
        />
      </div>

      <AppSelect
        label="Status"
        options={STATUS_OPTIONS}
        placeholder="All statuses"
        value={status}
        onChange={(e) => onStatusChange(e.target.value)}
      />

      <AppSelect
        label="Quality"
        options={QUALITY_OPTIONS}
        placeholder="All qualities"
        value={quality}
        onChange={(e) => onQualityChange(e.target.value)}
      />

      <AppSelect
        label="Type"
        options={TYPE_OPTIONS}
        placeholder="All types"
        value={type}
        onChange={(e) => onTypeChange(e.target.value)}
      />
    </div>
  );
}
