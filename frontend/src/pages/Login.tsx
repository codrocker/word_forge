import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { useLoginMutation } from '@/api/auth';
import { AppButton } from '@/components/app/AppButton';
import { AppFormInput } from '@/components/app/AppFormInput';
import { ApiError } from '@/api/client';

const loginSchema = z.object({
  email: z.string().email('Please enter a valid email'),
  password: z.string().min(1, 'Password is required'),
});

type LoginForm = z.infer<typeof loginSchema>;

export function LoginPage() {
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<LoginForm>({ resolver: zodResolver(loginSchema) });

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
          <AppFormInput
            label="Email"
            type="email"
            autoComplete="email"
            error={errors.email?.message}
            {...register('email')}
          />

          <AppFormInput
            label="Password"
            type="password"
            autoComplete="current-password"
            error={errors.password?.message}
            {...register('password')}
          />

          {login.isError && (
            <p className="text-sm text-red-500">
              {login.error instanceof ApiError
                ? login.error.message
                : 'Login failed'}
            </p>
          )}

          <AppButton type="submit" disabled={login.isPending}>
            {login.isPending ? 'Signing in...' : 'Sign in'}
          </AppButton>
        </form>
      </div>
    </div>
  );
}
