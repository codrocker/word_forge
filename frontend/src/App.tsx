import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { LocaleProvider } from '@douyinfe/semi-ui';
import zh_CN from '@douyinfe/semi-ui/lib/es/locale/source/zh_CN';
import { ErrorBoundary } from '@/components/layout/ErrorBoundary';
import { ProtectedRoute } from '@/components/layout/ProtectedRoute';
import { AdminLayout } from '@/components/layout/AdminLayout';
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
      <LocaleProvider locale={zh_CN}>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route element={<ProtectedRoute />}>
              <Route element={<AdminLayout />}>
                <Route path="/" element={<Search />} />
                <Route path="/words/:id" element={<WordDetail />} />
                <Route path="/audit" element={<Audit />} />
                <Route path="/experiments" element={<Experiments />} />
                <Route path="/config-center" element={<ConfigCenter />} />
                <Route path="/help" element={<Help />} />
              </Route>
            </Route>
            <Route path="*" element={<NotFoundPage />} />
          </Routes>
        </BrowserRouter>
      </LocaleProvider>
    </ErrorBoundary>
  );
}
