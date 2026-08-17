import { useState } from 'react';
import { Link } from 'react-router-dom';
import {
  useProvidersQuery,
  useModelsQuery,
  useRunsQuery,
  useCreateRunMutation,
} from '@/api/experiments';
import { ApiError } from '@/api/client';

const STATUS_STYLE: Record<string, string> = {
  running: 'text-yellow-600',
  done: 'text-green-700',
  error: 'text-red-600',
};

export function Experiments() {
  const providersQ = useProvidersQuery();
  const runsQ = useRunsQuery();
  const createRun = useCreateRunMutation();

  const [provider, setProvider] = useState('');
  const [model, setModel] = useState('');
  const [stage, setStage] = useState('paraphrase');
  const [wordCount, setWordCount] = useState(50);
  const [seed, setSeed] = useState(42);
  const [promptOverride, setPromptOverride] = useState('');

  const providerId = provider || providersQ.data?.providers[0]?.id || null;
  const modelsQ = useModelsQuery(providerId);

  const stages = providersQ.data?.stages ?? [];
  const runs = runsQ.data?.items ?? [];

  const submit = () => {
    if (!providerId || !model.trim()) return;
    createRun.mutate({
      provider: providerId,
      model: model.trim(),
      stage,
      prompt_override: promptOverride.trim() || null,
      word_count: wordCount,
      seed,
    });
  };

  const err =
    createRun.error instanceof ApiError
      ? createRun.error.message
      : createRun.error
        ? String(createRun.error)
        : null;

  return (
    <div className="mx-auto max-w-6xl p-6">
      <div className="mb-4 flex items-center gap-4">
        <h1 className="text-xl font-semibold">LLM 实验</h1>
        <Link to="/" className="text-sm text-blue-600 hover:underline">
          ← 词库
        </Link>
        <Link to="/audit" className="text-sm text-blue-600 hover:underline">
          审计日志
        </Link>
      </div>

      <section className="mb-6 rounded border p-4">
        <h2 className="mb-3 text-sm font-semibold text-gray-700">新建实验</h2>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-5">
          <label className="text-sm">
            <span className="mb-1 block text-gray-600">供应商</span>
            <select
              className="w-full rounded border px-2 py-1"
              value={providerId ?? ''}
              onChange={(e) => setProvider(e.target.value)}
            >
              {(providersQ.data?.providers ?? []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.id}
                  {p.available ? '' : '（未配置密钥）'}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-gray-600">
              模型{' '}
              {modelsQ.isFetching ? (
                <span className="text-gray-400">拉取中…</span>
              ) : modelsQ.isError ? (
                <span className="text-gray-400" title={(modelsQ.error as Error).message}>
                  列表拉取失败，可手填
                </span>
              ) : null}
            </span>
            {modelsQ.data && modelsQ.data.length > 0 ? (
              <select
                className="w-full rounded border px-2 py-1"
                value={model}
                onChange={(e) => setModel(e.target.value)}
              >
                <option value="">选择模型…</option>
                {modelsQ.data.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            ) : (
              <input
                className="w-full rounded border px-2 py-1"
                placeholder="例如 deepseek-chat"
                value={model}
                onChange={(e) => setModel(e.target.value)}
              />
            )}
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-gray-600">Stage</span>
            <select
              className="w-full rounded border px-2 py-1"
              value={stage}
              onChange={(e) => setStage(e.target.value)}
            >
              {stages.map((s) => (
                <option key={s.stage} value={s.stage}>
                  {s.stage}
                  {s.default_model ? `（默认 ${s.default_model}）` : ''}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-gray-600">词数（1-200）</span>
            <input
              type="number"
              min={1}
              max={200}
              className="w-full rounded border px-2 py-1"
              value={wordCount}
              onChange={(e) => setWordCount(Number(e.target.value))}
            />
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-gray-600">种子（同种子=同词样）</span>
            <input
              type="number"
              className="w-full rounded border px-2 py-1"
              value={seed}
              onChange={(e) => setSeed(Number(e.target.value))}
            />
          </label>
        </div>
        <label className="mt-3 block text-sm">
          <span className="mb-1 block text-gray-600">
            提示词覆盖（留空用 stage 默认模板；可用变量 {'{word}'} {'{dict_summary}'}）
          </span>
          <textarea
            className="h-24 w-full rounded border px-2 py-1 font-mono text-xs"
            placeholder="留空 = 使用 resources/prompts 下该 stage 的默认模板"
            value={promptOverride}
            onChange={(e) => setPromptOverride(e.target.value)}
          />
        </label>
        <div className="mt-3 flex items-center gap-3">
          <button
            type="button"
            className="rounded bg-blue-600 px-4 py-1.5 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
            disabled={createRun.isPending || !providerId || !model.trim()}
            onClick={submit}
          >
            {createRun.isPending ? '提交中…' : '运行实验'}
          </button>
          {err && <span className="text-sm text-red-600">{err}</span>}
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-gray-700">运行记录（对比口径：同种子同词样）</h2>
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b text-left text-gray-600">
              <th className="py-1.5 pr-3">#</th>
              <th className="py-1.5 pr-3">供应商 / 模型</th>
              <th className="py-1.5 pr-3">Stage</th>
              <th className="py-1.5 pr-3">词数</th>
              <th className="py-1.5 pr-3">解析通过</th>
              <th className="py-1.5 pr-3">Schema 有效</th>
              <th className="py-1.5 pr-3">成本 $</th>
              <th className="py-1.5 pr-3">状态</th>
              <th className="py-1.5 pr-3">时间</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <RunRow key={r.id} run={r} />
            ))}
            {runs.length === 0 && (
              <tr>
                <td colSpan={9} className="py-3 text-center text-gray-400">
                  暂无运行记录
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>
    </div>
  );
}

function RunRow({ run }: { run: import('@/api/experiments').ExperimentRun }) {
  const [open, setOpen] = useState(false);
  const total = run.results?.length ?? 0;
  return (
    <>
      <tr className="border-b hover:bg-gray-50">
        <td className="py-1.5 pr-3">
          <button
            type="button"
            className="text-blue-600 hover:underline"
            onClick={() => setOpen(!open)}
          >
            {run.id}
          </button>
        </td>
        <td className="py-1.5 pr-3 font-mono text-xs">
          {run.provider} / {run.model}
        </td>
        <td className="py-1.5 pr-3">{run.stage}</td>
        <td className="py-1.5 pr-3">{total || '-'}</td>
        <td className="py-1.5 pr-3">
          {run.ok_count}
          {total ? `/${total}` : ''}
        </td>
        <td className="py-1.5 pr-3">
          {run.valid_count}
          {total ? `/${total}` : ''}
        </td>
        <td className="py-1.5 pr-3">{Number(run.total_cost_usd).toFixed(4)}</td>
        <td className={`py-1.5 pr-3 ${STATUS_STYLE[run.status] ?? ''}`}>{run.status}</td>
        <td className="py-1.5 pr-3 text-gray-500">
          {new Date(run.created_at).toLocaleString()}
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={9} className="bg-gray-50 px-4 py-3">
            {run.error && <p className="mb-2 text-red-600">{run.error}</p>}
            <div className="max-h-96 overflow-auto">
              <table className="w-full border-collapse text-xs">
                <thead>
                  <tr className="border-b text-left text-gray-500">
                    <th className="py-1 pr-3">词</th>
                    <th className="py-1 pr-3">有效</th>
                    <th className="py-1 pr-3">成本 $</th>
                    <th className="py-1 pr-3">耗时 ms</th>
                    <th className="py-1 pr-3">输出 / 错误</th>
                  </tr>
                </thead>
                <tbody>
                  {(run.results ?? []).map((w) => (
                    <tr key={w.word_id} className="border-b align-top">
                      <td className="py-1 pr-3 font-medium">{w.word}</td>
                      <td className={`py-1 pr-3 ${w.valid ? 'text-green-700' : 'text-red-600'}`}>
                        {w.valid ? '✓' : w.ok ? '解析失败' : '调用失败'}
                      </td>
                      <td className="py-1 pr-3">{w.cost_usd.toFixed(4)}</td>
                      <td className="py-1 pr-3">{w.latency_ms ?? '-'}</td>
                      <td className="py-1 pr-3">
                        {w.error ? (
                          <span className="text-red-600">{w.error}</span>
                        ) : (
                          <pre className="max-w-3xl whitespace-pre-wrap break-all font-mono">
                            {w.text}
                          </pre>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
