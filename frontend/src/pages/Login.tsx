import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { Button } from '@douyinfe/semi-ui';
import { useLoginMutation } from '@/api/auth';
import { RhfInput } from '@/components/form/RhfInput';
import { ApiError } from '@/api/client';

const loginSchema = z.object({
  email: z.string().email('Please enter a valid email'),
  password: z.string().min(1, 'Password is required'),
});

type LoginForm = z.infer<typeof loginSchema>;

export function LoginPage() {
  const { handleSubmit, control } = useForm<LoginForm>({
    resolver: zodResolver(loginSchema),
  });

  const login = useLoginMutation();

  const onSubmit = (data: LoginForm) => {
    login.mutate(data);
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50">
      <div className="w-full max-w-sm rounded-lg bg-white p-8 shadow">
        <h1 className="mb-6 text-center text-2xl font-bold text-gray-900">
          WordForge Admin
        </h1>

        <form onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-4">
          <RhfInput
            control={control}
            name="email"
            label="Email"
            type="email"
            autoComplete="email"
          />

          <RhfInput
            control={control}
            name="password"
            label="Password"
            type="password"
            autoComplete="current-password"
          />

          {login.isError && (
            <p className="text-sm text-red-500">
              {login.error instanceof ApiError
                ? login.error.message
                : 'Login failed'}
            </p>
          )}

          <Button
            theme="solid"
            htmlType="submit"
            loading={login.isPending}
          >
            Sign in
          </Button>
        </form>
      </div>
    </div>
  );
}
