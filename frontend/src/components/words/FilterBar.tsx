import { Input, Select } from '@douyinfe/semi-ui';
import { IconSearch } from '@douyinfe/semi-icons';

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

type LabeledSelectProps = {
  label: string;
  options: { label: string; value: string }[];
  placeholder?: string;
  value: string;
  onChange: (value: string) => void;
};

function LabeledSelect({
  label,
  options,
  placeholder,
  value,
  onChange,
}: LabeledSelectProps) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-sm font-medium text-gray-700">{label}</label>
      <Select
        style={{ width: 180 }}
        placeholder={placeholder}
        value={value || undefined}
        onChange={(v) => onChange(String(v ?? ''))}
        optionList={options}
        showClear
      />
    </div>
  );
}

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
        <Input
          style={{ width: 260 }}
          prefix={<IconSearch />}
          showClear
          placeholder="Search by word form..."
          value={qValue}
          onChange={(v) => onQChange(v)}
        />
      </div>

      <LabeledSelect
        label="Status"
        options={STATUS_OPTIONS}
        placeholder="All statuses"
        value={status}
        onChange={onStatusChange}
      />

      <LabeledSelect
        label="Quality"
        options={QUALITY_OPTIONS}
        placeholder="All qualities"
        value={quality}
        onChange={onQualityChange}
      />

      <LabeledSelect
        label="Type"
        options={TYPE_OPTIONS}
        placeholder="All types"
        value={type}
        onChange={onTypeChange}
      />
    </div>
  );
}
