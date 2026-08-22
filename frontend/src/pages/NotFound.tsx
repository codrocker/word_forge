import { useNavigate } from 'react-router-dom';
import { Button, Empty } from '@douyinfe/semi-ui';

export function NotFoundPage() {
  const navigate = useNavigate();
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4">
      <Empty title="404 · Page not found" description="" />
      <Button theme="solid" htmlType="button" onClick={() => navigate('/')}>
        Go home
      </Button>
    </div>
  );
}
