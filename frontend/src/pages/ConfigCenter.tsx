import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Button, Input, Select, Tabs, TabPane, Tag, TextArea, Typography } from '@douyinfe/semi-ui';
import {
  useProvidersQuery,
  usePromptsQuery,
  useAgentsQuery,
  useProviderModelsQuery,
  useCreateProviderMutation,
  useUpdateProviderMutation,
  useRollbackMutation,
  useCreatePromptMutation,
  useUpdatePromptMutation,
  useCreateAgentMutation,
  useUpdateAgentMutation,
  type ProviderConfig,
  type PromptItem,
  type AgentItem,
} from '@/api/configCenter';
import { ApiError } from '@/api/client';

type Tab = 'providers' | 'prompts' | 'agents';

function Err({ error }: { error: unknown }) {
  if (!error) return null;
  const msg = error instanceof ApiError ? error.message : String(error);
  return <p className="text-sm text-red-600">{msg}</p>;
}

export function ConfigCenter() {
  const [tab, setTab] = useState<Tab>('providers');
  return (
    <div className="mx-auto max-w-6xl p-6">
      <Typography.Title heading={4} className="mb-4">
        LLM 配置中心
      </Typography.Title>
      <Tabs activeKey={tab} onChange={(k) => setTab(k as Tab)}>
        <TabPane tab="供应商配置" itemKey="providers" />
        <TabPane tab="提示词库" itemKey="prompts" />
        <TabPane tab="Agents" itemKey="agents" />
      </Tabs>
      {tab === 'providers' && <ProvidersTab />}
      {tab === 'prompts' && <PromptsTab />}
      {tab === 'agents' && <AgentsTab />}
    </div>
  );
}

function VersionBar({
  current,
  versions,
  onRollback,
}: {
  current: number;
  versions: { version: number }[];
  onRollback: (version: number) => void;
}) {
  return (
    <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-gray-500">
      <span>
        当前 v{current} · 历史 {versions.map((v) => `v${v.version}`).join(' ')}
      </span>
      {versions
        .filter((v) => v.version !== current)
        .map((v) => (
          <Button
            key={v.version}
            size="small"
            theme="light"
            htmlType="button"
            onClick={() => onRollback(v.version)}
          >
            回退到 v{v.version}
          </Button>
        ))}
    </div>
  );
}

function ProvidersTab() {
  const q = useProvidersQuery();
  const create = useCreateProviderMutation();
  const update = useUpdateProviderMutation();
  const rollback = useRollbackMutation('providers');

  const [name, setName] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [notes, setNotes] = useState('');
  const [editId, setEditId] = useState<number | null>(null);
  const [editUrl, setEditUrl] = useState('');
  const [editKey, setEditKey] = useState('');
  const [modelsFor, setModelsFor] = useState<number | null>(null);
  const modelsQ = useProviderModelsQuery(modelsFor);

  const submit = () => {
    if (!name.trim() || !baseUrl.trim() || !apiKey.trim()) return;
    create.mutate({
      name: name.trim(),
      transport: 'openai',
      base_url: baseUrl.trim(),
      api_key: apiKey.trim(),
      notes: notes.trim() || null,
    });
    setName('');
    setBaseUrl('');
    setApiKey('');
    setNotes('');
  };

  return (
    <div className="space-y-6">
      <section className="rounded border p-4">
        <h2 className="mb-3 text-sm font-semibold text-gray-700">
          新增供应商配置（密钥仅写入、加密存储，保存后不可查看）
        </h2>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
          <Input placeholder="名称，如 deepseek-official" value={name} onChange={(v) => setName(v)} />
          <Input placeholder="Base URL，如 https://api.deepseek.com/v1" value={baseUrl} onChange={(v) => setBaseUrl(v)} />
          <Input mode="password" placeholder="sk 密钥（只写不读）" value={apiKey} onChange={(v) => setApiKey(v)} />
          <Input placeholder="备注（可选）" value={notes} onChange={(v) => setNotes(v)} />
        </div>
        <div className="mt-3 flex items-center gap-3">
          <Button theme="solid" htmlType="button" loading={create.isPending} onClick={submit}>
            保存
          </Button>
          <Err error={create.error} />
        </div>
      </section>

      <section className="space-y-3">
        {(q.data ?? []).map((p: ProviderConfig) => (
          <div key={p.id} className="rounded border p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-semibold">{p.name}</span>
              <Tag size="small" color="grey">{p.transport}</Tag>
              <span className="font-mono text-xs text-gray-500">{p.base_url}</span>
              <span className="text-xs text-gray-500">
                密钥 {p.has_key ? `已配置（尾4位 ${p.api_key_last4 ?? '****'}）` : '未配置'}
              </span>
              <Button
                size="small"
                theme="borderless"
                htmlType="button"
                className="ml-auto"
                onClick={() => setModelsFor(modelsFor === p.id ? null : p.id)}
              >
                {modelsFor === p.id ? '收起模型' : '拉取模型列表'}
              </Button>
            </div>
            {modelsFor === p.id && (
              <div className="mt-2 rounded bg-gray-50 p-2 text-xs">
                {modelsQ.isLoading && <span className="text-gray-400">拉取中…</span>}
                {modelsQ.isError && <Err error={modelsQ.error} />}
                {modelsQ.data && <span>{modelsQ.data.join(' · ')}</span>}
              </div>
            )}
            {editId === p.id ? (
              <div className="mt-2 grid grid-cols-1 gap-2 md:grid-cols-3">
                <Input placeholder="新的 Base URL" value={editUrl} onChange={(v) => setEditUrl(v)} />
                <Input mode="password" placeholder="新密钥（留空 = 不变）" value={editKey} onChange={(v) => setEditKey(v)} />
                <Button
                  size="small"
                  theme="solid"
                  htmlType="button"
                  onClick={() => {
                    const body: Record<string, unknown> = {};
                    if (editUrl.trim()) body.base_url = editUrl.trim();
                    if (editKey.trim()) body.api_key = editKey.trim();
                    update.mutate({ id: p.id, body });
                    setEditId(null);
                    setEditUrl('');
                    setEditKey('');
                  }}
                >
                  保存为新版本
                </Button>
              </div>
            ) : (
              <Button
                size="small"
                theme="borderless"
                htmlType="button"
                className="mt-1"
                onClick={() => setEditId(p.id)}
              >
                编辑（生成新版本）
              </Button>
            )}
            <VersionBar
              current={p.current_version}
              versions={p.versions}
              onRollback={(v) => rollback.mutate({ id: p.id, version: v })}
            />
          </div>
        ))}
        {q.data?.length === 0 && <p className="text-sm text-gray-400">暂无供应商配置</p>}
      </section>
    </div>
  );
}

function PromptsTab() {
  const q = usePromptsQuery();
  const create = useCreatePromptMutation();
  const update = useUpdatePromptMutation();
  const rollback = useRollbackMutation('prompts');

  const [name, setName] = useState('');
  const [content, setContent] = useState('');
  const [editId, setEditId] = useState<number | null>(null);
  const [editContent, setEditContent] = useState('');

  return (
    <div className="space-y-6">
      <section className="rounded border p-4">
        <h2 className="mb-3 text-sm font-semibold text-gray-700">
          新增提示词（可用变量槽 {'{word}'} {'{dict_summary}'}；保存修改会生成新版本）
        </h2>
        <Input placeholder="名称，如 paraphrase-严格版" value={name} onChange={(v) => setName(v)} />
        <TextArea
          className="mt-2"
          rows={6}
          placeholder="模板内容…"
          value={content}
          onChange={(v) => setContent(v)}
        />
        <div className="mt-3 flex items-center gap-3">
          <Button
            theme="solid"
            htmlType="button"
            loading={create.isPending}
            disabled={!name.trim() || !content.trim()}
            onClick={() => {
              create.mutate({ name: name.trim(), stage: 'paraphrase', content });
              setName('');
              setContent('');
            }}
          >
            保存
          </Button>
          <Err error={create.error} />
        </div>
      </section>

      <section className="space-y-3">
        {(q.data ?? []).map((p: PromptItem) => (
          <div key={p.id} className="rounded border p-3 text-sm">
            <div className="flex items-center gap-2">
              <span className="font-semibold">{p.name}</span>
              <Tag size="small" color="grey">stage: {p.stage}</Tag>
            </div>
            <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-gray-50 p-2 font-mono text-xs">
              {p.versions[0]?.content}
            </pre>
            {editId === p.id ? (
              <div className="mt-2">
                <TextArea
                  rows={6}
                  value={editContent}
                  onChange={(v) => setEditContent(v)}
                />
                <Button
                  size="small"
                  theme="solid"
                  htmlType="button"
                  className="mt-1"
                  onClick={() => {
                    update.mutate({ id: p.id, content: editContent });
                    setEditId(null);
                  }}
                >
                  保存为新版本
                </Button>
              </div>
            ) : (
              <Button
                size="small"
                theme="borderless"
                htmlType="button"
                className="mt-1"
                onClick={() => {
                  setEditId(p.id);
                  setEditContent(p.versions[0]?.content ?? '');
                }}
              >
                编辑（生成新版本）
              </Button>
            )}
            <VersionBar
              current={p.current_version}
              versions={p.versions}
              onRollback={(v) => rollback.mutate({ id: p.id, version: v })}
            />
          </div>
        ))}
        {q.data?.length === 0 && <p className="text-sm text-gray-400">暂无提示词</p>}
      </section>
    </div>
  );
}

function AgentsTab() {
  const agentsQ = useAgentsQuery();
  const providersQ = useProvidersQuery();
  const promptsQ = usePromptsQuery();
  const create = useCreateAgentMutation();
  const update = useUpdateAgentMutation();
  const rollback = useRollbackMutation('agents');

  const [name, setName] = useState('');
  const [providerId, setProviderId] = useState('');
  const [model, setModel] = useState('');
  const [promptId, setPromptId] = useState('');

  const submit = () => {
    if (!name.trim() || !providerId || !model.trim() || !promptId) return;
    create.mutate({
      name: name.trim(),
      provider_config_id: Number(providerId),
      model: model.trim(),
      prompt_id: Number(promptId),
    });
    setName('');
    setModel('');
  };

  return (
    <div className="space-y-6">
      <section className="rounded border p-4">
        <h2 className="mb-3 text-sm font-semibold text-gray-700">
          新增 Agent（= 供应商配置版本 + 模型 + 提示词版本；修改会生成新版本，可回退）
        </h2>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
          <Input placeholder="名称，如 paraphrase-deepseek" value={name} onChange={(v) => setName(v)} />
          <Select
            className="w-full"
            placeholder="选供应商配置…"
            value={providerId || undefined}
            onChange={(v) => setProviderId(String(v ?? ''))}
            optionList={(providersQ.data ?? []).map((p) => ({
              value: String(p.id),
              label: `${p.name}（v${p.current_version}）`,
            }))}
          />
          <Input placeholder="模型，如 deepseek-v4-flash" value={model} onChange={(v) => setModel(v)} />
          <Select
            className="w-full"
            placeholder="选提示词…"
            value={promptId || undefined}
            onChange={(v) => setPromptId(String(v ?? ''))}
            optionList={(promptsQ.data ?? []).map((p) => ({
              value: String(p.id),
              label: `${p.name}（v${p.current_version}）`,
            }))}
          />
        </div>
        <div className="mt-3 flex items-center gap-3">
          <Button theme="solid" htmlType="button" loading={create.isPending} onClick={submit}>
            保存
          </Button>
          <Err error={create.error} />
        </div>
      </section>

      <section className="space-y-3">
        {(agentsQ.data ?? []).map((a: AgentItem) => {
          const current = a.versions[0];
          return (
            <div key={a.id} className="rounded border p-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold">{a.name}</span>
                {current && (
                  <span className="text-xs text-gray-600">
                    {current.provider_config_name} v{current.provider_config_version} ·{' '}
                    <span className="font-mono">{current.model}</span> ·{' '}
                    {current.prompt_name} v{current.prompt_version}
                  </span>
                )}
              </div>
              <div className="mt-2 flex items-center gap-3">
                <Input
                  style={{ width: 224 }}
                  size="small"
                  placeholder="改为新模型名（生成新版本）"
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && current) {
                      const value = (e.target as HTMLInputElement).value.trim();
                      if (value) {
                        update.mutate({
                          id: a.id,
                          body: {
                            provider_config_id: current.provider_config_id,
                            model: value,
                            prompt_id: current.prompt_id,
                          },
                        });
                        (e.target as HTMLInputElement).value = '';
                      }
                    }
                  }}
                />
                <Link to="/experiments" className="text-xs text-blue-600 hover:underline">
                  去实验页运行 →
                </Link>
              </div>
              <VersionBar
                current={a.current_version}
                versions={a.versions}
                onRollback={(v) => rollback.mutate({ id: a.id, version: v })}
              />
            </div>
          );
        })}
        {agentsQ.data?.length === 0 && <p className="text-sm text-gray-400">暂无 Agent</p>}
      </section>
    </div>
  );
}
