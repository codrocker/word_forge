import { AppButton } from '@/components/app/AppButton';

type PaginationProps = {
  hasNext: boolean;
  onNext: () => void;
  onReset: () => void;
  hasCursor: boolean;
};

export function Pagination({
  hasNext,
  onNext,
  onReset,
  hasCursor,
}: PaginationProps) {
  return (
    <div className="flex items-center gap-3">
      {hasCursor && (
        <AppButton variant="secondary" onClick={onReset}>
          Back to first page
        </AppButton>
      )}
      {hasNext && (
        <AppButton variant="primary" onClick={onNext}>
          Next page
        </AppButton>
      )}
    </div>
  );
}
