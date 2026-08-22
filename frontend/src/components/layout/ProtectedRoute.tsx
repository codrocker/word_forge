import { Navigate, Outlet } from 'react-router-dom';
import { Spin } from '@douyinfe/semi-ui';
import { useMeQuery } from '@/api/auth';

export function ProtectedRoute() {
  const { data, isLoading, isError } = useMeQuery();

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <Spin size="large" />
      </div>
    );
  }

  if (isError || !data) {
    return <Navigate to="/login" replace />;
  }

  return <Outlet />;
}
