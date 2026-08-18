import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { ErrorBoundary } from '@/components/layout/ErrorBoundary';
import { ProtectedRoute } from '@/components/layout/ProtectedRoute';
import { LoginPage } from '@/pages/Login';
import { NotFoundPage } from '@/pages/NotFound';
import { Search } from '@/pages/Search';
import { WordDetail } from '@/pages/WordDetail';
import { Audit } from '@/pages/Audit';
import { Experiments } from '@/pages/Experiments';
import { ConfigCenter } from '@/pages/ConfigCenter';
import { Help } from '@/pages/Help';

export default function App() {
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<ProtectedRoute />}>
            <Route path="/" element={<Search />} />
            <Route path="/words/:id" element={<WordDetail />} />
            <Route path="/audit" element={<Audit />} />
            <Route path="/experiments" element={<Experiments />} />
            <Route path="/config-center" element={<ConfigCenter />} />
            <Route path="/help" element={<Help />} />
          </Route>
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </BrowserRouter>
    </ErrorBoundary>
  );
}
