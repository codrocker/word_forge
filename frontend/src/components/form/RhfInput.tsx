import {
  Controller,
  type Control,
  type FieldPath,
  type FieldValues,
} from 'react-hook-form';
import { Input } from '@douyinfe/semi-ui';

type RhfInputProps<T extends FieldValues> = {
  control: Control<T>;
  name: FieldPath<T>;
  label: string;
  type?: string;
  placeholder?: string;
  autoComplete?: string;
  disabled?: boolean;
};

/**
 * Semi Input 接入 React Hook Form 的受控字段：
 * Semi 的 onChange 第一个参数直接是 value，与 Controller 的约定天然对齐。
 */
export function RhfInput<T extends FieldValues>({
  control,
  name,
  label,
  type,
  placeholder,
  autoComplete,
  disabled,
}: RhfInputProps<T>) {
  return (
    <Controller
      control={control}
      name={name}
      render={({ field, fieldState }) => (
        <div className="flex flex-col gap-1">
          <label className="text-sm font-medium text-gray-700">{label}</label>
          <Input
            value={field.value ?? ''}
            onChange={(v) => field.onChange(v)}
            onBlur={field.onBlur}
            type={type}
            placeholder={placeholder}
            autoComplete={autoComplete}
            disabled={disabled}
            validateStatus={fieldState.error ? 'error' : 'default'}
          />
          {fieldState.error && (
            <span className="text-xs text-red-500">
              {fieldState.error.message}
            </span>
          )}
        </div>
      )}
    />
  );
}
