import { useState } from 'react';
import { AppButton } from '@/components/app/AppButton';

type StatusQualityToggleProps = {
  status: number;
  qualityFlag: string;
  onChangeStatus: (oldV: number, newV: number) => void;
  onChangeQuality: (oldV: string, newV: string) => void;
  disabled?: boolean;
};

const STATUS_OPTIONS = [
  { value: 0, label: 'Draft' },
  { value: 1, label: 'Published' },
  { value: 2, label: 'Archived' },
];

const QUALITY_OPTIONS = [
  { value: 'none', label: 'None' },
  { value: 'suspect', label: 'Suspect' },
  { value: 'fixed', label: 'Fixed' },
];

export function StatusQualityToggle({
  status,
  qualityFlag,
  onChangeStatus,
  onChangeQuality,
  disabled,
}: StatusQualityToggleProps) {
  const [localStatus, setLocalStatus] = useState(status);
  const [localQuality, setLocalQuality] = useState(qualityFlag);

  const statusDirty = localStatus !== status;
  const qualityDirty = localQuality !== qualityFlag;

  return (
    <div className="flex flex-wrap items-end gap-4">
      <div className="flex flex-col gap-1">
        <label className="text-sm font-medium text-gray-700">Status</label>
        <div className="flex items-center gap-2">
          <select
            value={localStatus}
            onChange={(e) => setLocalStatus(Number(e.target.value))}
            disabled={disabled}
            className="rounded border border-gray-300 bg-white px-3 py-2 text-sm outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
          >
            {STATUS_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          {statusDirty && (
            <AppButton
              type="button"
              disabled={disabled}
              onClick={() => onChangeStatus(status, localStatus)}
              className="text-xs"
            >
              Save
            </AppButton>
          )}
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <label className="text-sm font-medium text-gray-700">Quality</label>
        <div className="flex items-center gap-2">
          <select
            value={localQuality}
            onChange={(e) => setLocalQuality(e.target.value)}
            disabled={disabled}
            className="rounded border border-gray-300 bg-white px-3 py-2 text-sm outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
          >
            {QUALITY_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          {qualityDirty && (
            <AppButton
              type="button"
              disabled={disabled}
              onClick={() => onChangeQuality(qualityFlag, localQuality)}
              className="text-xs"
            >
              Save
            </AppButton>
          )}
        </div>
      </div>
    </div>
  );
}
