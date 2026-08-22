import { useState } from 'react';
import {
  Banner,
  Button,
  Input,
  InputNumber,
  Select,
  Table,
  Tag,
  TextArea,
  Typography,
} from '@douyinfe/semi-ui';
import type { ColumnProps } from '@douyinfe/semi-ui/lib/es/table';
import {
  useProvidersQuery,
  useModelsQuery,
  useRunsQuery,
  useCreateRunMutation,
  type ExperimentRun,
} from '@/api/experiments';
import { useAgentsQuery } from '@/api/configCenter';
import { ApiError } from '@/api/client';

type ExperimentResult = NonNullable<ExperimentRun['results']>[number];

const STATUS_COLOR: Record<string, 'amber' | 'green' | 'red'> = {
  running: 'amber',
  done: 'green',
  error: 'red',
};

const runColumns: ColumnProps<ExperimentRun>[] = [
  { title: '#', dataIndex: 'id' },
  {
    title: '供应商 / 模型',
    render: (_: unknown, r: ExperimentRun) => (
      <span className="font-mono text-xs">
        {r.provider} / {r.model}
      </span>
    ),
  },
  { title: 'Stage', dataIndex: 'stage' },
  {
    title: '词数',
    render: (_: unknown, r: ExperimentRun) => r.results?.length || '-',
  },
  {
    title: '解析通过',
    render: (_: unknown, r: ExperimentRun) => {
      const total = r.results?.length ?? 0;
      return total ? `${r.ok_count}/${total}` : r.ok_count;
    },
  },
  {
    title: 'Schema 有效',
    render: (_: unknown, r: ExperimentRun) => {
      const total = r.results?.length ?? 0;
      return total ? `${r.valid_count}/${total}` : r.valid_count;
    },
  },
  {
    title: '成本 $',
    render: (_: unknown, r: ExperimentRun) =>
      Number(r.total_cost_usd).toFixed(4),
  },
  {
    title: '状态',
    dataIndex: 'status',
    render: (status: string) =>
      STATUS_COLOR[status] ? (
        <Tag size="small" color={STATUS_COLOR[status]}>
          {status}
        </Tag>
      ) : (
        status
      ),
  },
  {
    title: '时间',
    dataIndex: 'created_at',
    render: (v: string) => (
      <span className="text-gray-500">{new Date(v).toLocaleString()}</span>
    ),
  },
];

const resultColumns: ColumnProps<ExperimentResult>[] = [
  {
    title: '词',
    dataIndex: 'word',
    render: (v: string) => <span className="font-medium">{v}</span>,
  },
  {
    title: '有效',
    render: (_: unknown, w: ExperimentResult) => (
      <span className={w.valid ? 'text-green-700' : 'text-red-600'}>
        {w.valid ? '✓' : w.ok ? '解析失败' : '调用失败'}
      </span>
    ),
  },
  {
    title: '成本 $',
    dataIndex: 'cost_usd',
    render: (v: number) => v.toFixed(4),
  },
  {
    title: '耗时 ms',
    dataIndex: 'latency_ms',
    render: (v: number | null) => v ?? '-',
  },
  {
    title: '输出 / 错误',
    render: (_: unknown, w: ExperimentResult) =>
      w.error ? (
        <span className="text-red-600">{w.error}</span>
      ) : (
        <pre className="max-w-3xl whitespace-pre-wrap break-all font-mono">
          {w.text}
        </pre>
      ),
  },
];

export function Experiments() {
  const providersQ = useProvidersQuery();
  const runsQ = useRunsQuery();
  const createRun = useCreateRunMutation();
  const agentsQ = useAgentsQuery();

  const [agentId, setAgentId] = useState('');
  const [provider, setProvider] = useState('');
  const [model, setModel] = useState('');
  const [stage, setStage] = useState('paraphrase');
  const [wordCount, setWordCount] = useState(50);
  const [seed, setSeed] = useState(42);
  const [promptOverride, setPromptOverride] = useState('');

  const providerId = provider || providersQ.data?.providers[0]?.id || null;
  const modelsQ = useModelsQuery(agentId ? null : providerId);

  const stages = providersQ.data?.stages ?? [];
  const runs = runsQ.data?.items ?? [];
  const agentMode = agentId !== '';
  const selectedAgent = (agentsQ.data ?? []).find(
    (a) => String(a.id) === agentId,
  );
  const agentRecipe = selectedAgent?.versions?.[0];

  const submit = () => {
    if (agentMode) {
      createRun.mutate({
        agent_id: Number(agentId),
        prompt_override: promptOverride.trim() || null,
        word_count: wordCount,
        seed,
      });
      return;
    }
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
      <Typography.Title heading={4} className="mb-4">
        LLM 实验
      </Typography.Title>

      <section className="mb-6 rounded border p-4">
        <h2 className="mb-3 text-sm font-semibold text-gray-700">新建实验</h2>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-5">
          <label className="text-sm md:col-span-2">
            <span className="mb-1 block text-gray-600">
              Agent（选它则用配置中心的版本化组合；留空走下面的点选模式）
            </span>
            <Select
              className="w-full"
              value={agentId || undefined}
              onChange={(v) => setAgentId(String(v ?? ''))}
              optionList={[
                { value: '', label: '— 点选模式 —' },
                ...(agentsQ.data ?? []).map((a) => ({
                  value: String(a.id),
                  label: `${a.name}（v${a.current_version}）`,
                })),
              ]}
            />
          </label>
          {agentMode && agentRecipe ? (
            <div className="text-sm md:col-span-3">
              <span className="mb-1 block text-gray-600">
                Agent 配方（来自配置中心，随版本固定）
              </span>
              <p className="rounded bg-gray-50 px-2 py-1 font-mono text-xs text-gray-700">
                {agentRecipe.provider_config_name} v
                {agentRecipe.provider_config_version} · {agentRecipe.model} ·{' '}
                {agentRecipe.prompt_name} v{agentRecipe.prompt_version}
              </p>
            </div>
          ) : (
            <>
              <label className="text-sm">
                <span className="mb-1 block text-gray-600">供应商</span>
                <Select
                  className="w-full"
                  value={providerId ?? undefined}
                  onChange={(v) => setProvider(String(v ?? ''))}
                  optionList={(providersQ.data?.providers ?? []).map((p) => ({
                    value: p.id,
                    label: p.id + (p.available ? '' : '（未配置密钥）'),
                  }))}
                />
              </label>
              <label className="text-sm">
                <span className="mb-1 block text-gray-600">
                  模型{' '}
                  {modelsQ.isFetching ? (
                    <span className="text-gray-400">拉取中…</span>
                  ) : modelsQ.isError ? (
                    <span
                      className="text-gray-400"
                      title={(modelsQ.error as Error).message}
                    >
                      列表拉取失败，可手填
                    </span>
                  ) : null}
                </span>
                {modelsQ.data && modelsQ.data.length > 0 ? (
                  <Select
                    className="w-full"
                    placeholder="选择模型…"
                    value={model || undefined}
                    onChange={(v) => setModel(String(v ?? ''))}
                    optionList={modelsQ.data.map((m) => ({
                      value: m,
                      label: m,
                    }))}
                  />
                ) : (
                  <Input
                    className="w-full"
                    placeholder="例如 deepseek-chat"
                    value={model}
                    onChange={(v) => setModel(v)}
                  />
                )}
              </label>
              <label className="text-sm">
                <span className="mb-1 block text-gray-600">Stage</span>
                <Select
                  className="w-full"
                  value={stage}
                  onChange={(v) => setStage(String(v ?? 'paraphrase'))}
                  optionList={stages.map((s) => ({
                    value: s.stage,
                    label:
                      s.stage + (s.default_model ? `（默认 ${s.default_model}）` : ''),
                  }))}
                />
              </label>
            </>
          )}
          <label className="text-sm">
            <span className="mb-1 block text-gray-600">词数（1-200）</span>
            <InputNumber
              className="w-full"
              min={1}
              max={200}
              value={wordCount}
              onChange={(v) => setWordCount(Number(v))}
            />
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-gray-600">种子（同种子=同词样）</span>
            <InputNumber
              className="w-full"
              value={seed}
              onChange={(v) => setSeed(Number(v))}
            />
          </label>
        </div>
        <label className="mt-3 block text-sm">
          <span className="mb-1 block text-gray-600">
            提示词覆盖（留空用 stage 默认模板；可用变量 {'{word}'} {'{dict_summary}'}）
          </span>
          <TextArea
            rows={4}
            placeholder="留空 = 使用 resources/prompts 下该 stage 的默认模板"
            value={promptOverride}
            onChange={(v) => setPromptOverride(v)}
          />
        </label>
        <div className="mt-3 flex items-center gap-3">
          <Button
            theme="solid"
            htmlType="button"
            loading={createRun.isPending}
            disabled={agentMode ? !agentId : !providerId || !model.trim()}
            onClick={submit}
          >
            运行实验
          </Button>
          {err && <span className="text-sm text-red-600">{err}</span>}
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-gray-700">
          运行记录（对比口径：同种子同词样）
        </h2>
        <Table
          columns={runColumns}
          dataSource={runs}
          rowKey="id"
          pagination={false}
          empty="暂无运行记录"
          expandedRowRender={(run) =>
            run ? (
              <div className="max-h-96 overflow-auto">
                {run.error && (
                  <Banner
                    type="danger"
                    description={run.error}
                    closeIcon={null}
                    className="mb-2"
                  />
                )}
                <Table
                  columns={resultColumns}
                  dataSource={run.results ?? []}
                  rowKey="word_id"
                  pagination={false}
                  size="small"
                />
              </div>
            ) : null
          }
        />
      </section>
    </div>
  );
}
