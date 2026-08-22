import { Button } from '@douyinfe/semi-ui';

type PaginationProps = {
  hasNext: boolean;
  onNext: () => void;
  onReset: () => void;
  hasCursor: boolean;
};

/** keyset 游标分页：只有 Next / Back to first 两个动作，没有页码。 */
export function Pagination({
  hasNext,
  onNext,
  onReset,
  hasCursor,
}: PaginationProps) {
  return (
    <div className="flex items-center gap-3">
      {hasCursor && (
        <Button theme="light" htmlType="button" onClick={onReset}>
          Back to first page
        </Button>
      )}
      {hasNext && (
        <Button theme="solid" htmlType="button" onClick={onNext}>
          Next page
        </Button>
      )}
    </div>
  );
}
