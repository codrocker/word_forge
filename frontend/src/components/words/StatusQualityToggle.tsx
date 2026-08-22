import { useState } from 'react';
import { Button, Select } from '@douyinfe/semi-ui';

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
          <Select
            style={{ width: 140 }}
            value={localStatus}
            onChange={(v) => setLocalStatus(Number(v))}
            optionList={STATUS_OPTIONS}
            disabled={disabled}
          />
          {statusDirty && (
            <Button
              size="small"
              theme="solid"
              htmlType="button"
              disabled={disabled}
              onClick={() => onChangeStatus(status, localStatus)}
            >
              Save
            </Button>
          )}
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <label className="text-sm font-medium text-gray-700">Quality</label>
        <div className="flex items-center gap-2">
          <Select
            style={{ width: 140 }}
            value={localQuality}
            onChange={(v) => setLocalQuality(String(v))}
            optionList={QUALITY_OPTIONS}
            disabled={disabled}
          />
          {qualityDirty && (
            <Button
              size="small"
              theme="solid"
              htmlType="button"
              disabled={disabled}
              onClick={() => onChangeQuality(qualityFlag, localQuality)}
            >
              Save
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
