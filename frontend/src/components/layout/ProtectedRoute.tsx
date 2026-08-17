import { Navigate, Outlet } from 'react-router-dom';
import { useMeQuery } from '@/api/auth';

export function ProtectedRoute() {
  const { data, isLoading, isError } = useMeQuery();

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center text-gray-500">
        Loading...
      </div>
    );
  }

  if (isError || !data) {
    return <Navigate to="/login" replace />;
  }

  return <Outlet />;
}
