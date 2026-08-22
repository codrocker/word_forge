import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { Nav, Button } from '@douyinfe/semi-ui';
import {
  IconSearch,
  IconActivity,
  IconSetting,
  IconHistory,
  IconHelpCircle,
  IconExit,
} from '@douyinfe/semi-icons';
import { useLogoutMutation } from '@/api/auth';

const NAV_ITEMS = [
  { itemKey: '/', text: '词库', icon: <IconSearch /> },
  { itemKey: '/experiments', text: 'LLM 实验', icon: <IconActivity /> },
  { itemKey: '/config-center', text: '配置中心', icon: <IconSetting /> },
  { itemKey: '/audit', text: '审计日志', icon: <IconHistory /> },
  { itemKey: '/help', text: '使用说明', icon: <IconHelpCircle /> },
];

function activeNavKey(pathname: string): string {
  if (pathname.startsWith('/words/')) return '/';
  return pathname;
}

export function AdminLayout() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const logout = useLogoutMutation();

  return (
    <div className="flex h-screen">
      <Nav
        style={{ width: 220, flexShrink: 0 }}
        items={NAV_ITEMS}
        selectedKeys={[activeNavKey(pathname)]}
        onSelect={({ itemKey }) => navigate(String(itemKey))}
        header={{ text: 'WordForge Admin' }}
        footer={{
          collapseButton: true,
          children: (
            <Button
              icon={<IconExit />}
              theme="borderless"
              type="tertiary"
              htmlType="button"
              loading={logout.isPending}
              onClick={() => logout.mutate()}
            >
              退出登录
            </Button>
          ),
        }}
      />
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  );
}
