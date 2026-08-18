import { Link } from 'react-router-dom';

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-6 rounded border p-4">
      <h2 className="mb-2 text-base font-semibold text-gray-800">{title}</h2>
      <div className="space-y-2 text-sm leading-6 text-gray-700">{children}</div>
    </section>
  );
}

function Code({ children }: { children: React.ReactNode }) {
  return <code className="rounded bg-gray-100 px-1 py-0.5 font-mono text-xs">{children}</code>;
}

export function Help() {
  return (
    <div className="mx-auto max-w-3xl p-6">
      <div className="mb-4 flex items-center gap-4">
        <h1 className="text-xl font-semibold">使用说明</h1>
        <Link to="/" className="text-sm text-blue-600 hover:underline">← 词库</Link>
        <Link to="/config-center" className="text-sm text-blue-600 hover:underline">配置中心 →</Link>
      </div>

      <Section title="三分钟上手">
        <p>
          本站是「背单词」词库的运营后台：管词库（搜索/编辑/审计）+ 管 LLM（配置中心/实验）。
          使用 LLM 功能的三步：
        </p>
        <ol className="list-decimal space-y-1 pl-6">
          <li>
            <Link to="/config-center" className="text-blue-600 hover:underline">配置中心</Link>
            →「供应商配置」：填中转站/官方的 Base URL 和 sk 密钥（如{' '}
            <Code>https://api.deepseek.com/v1</Code>），保存后点「拉取模型列表」验证密钥可用。
          </li>
          <li>「提示词库」：新建模板（或先用默认），「Agents」：把 供应商+模型+提示词 组合成一个 Agent。</li>
          <li>
            <Link to="/experiments" className="text-blue-600 hover:underline">LLM 实验</Link>
            ：选 Agent、填词数和种子 → 运行 → 结果表里看有效率和成本。
          </li>
        </ol>
      </Section>

      <Section title="供应商配置（密钥）">
        <p>密钥<strong>只写不读</strong>：保存后加密存储，任何页面都不会再显示明文（只显示尾 4 位）。</p>
        <p>修改 Base URL / 换密钥会生成新版本；密钥本身不进版本历史。出问题用「回退到 v N」。</p>
        <p>Base URL 不允许 localhost / 内网地址。</p>
      </Section>

      <Section title="提示词库">
        <p>模板里可用变量槽：<Code>{'{word}'}</Code>（单词）、<Code>{'{dict_summary}'}</Code>（词典摘要）。</p>
        <p>每次保存生成新版本，历史永久保留，可一键回退。建议新提示词先用 5-10 词小批量实验验证，别直接大批量。</p>
      </Section>

      <Section title="Agents（版本化组合）">
        <p>Agent = 供应商配置版本 + 模型 + 提示词版本。创建时会<strong>固定当时各组件的版本</strong>：</p>
        <p>之后供应商/提示词升级不影响已有 Agent；想让 Agent 用新版组件，编辑它生成新 Agent 版本。</p>
      </Section>

      <Section title="实验页：对比效果与成本">
        <p>
          <strong>同种子 = 同一批词</strong>：不同 Agent 用同一种子跑，结果直接可比
          （有效数 / 成本 / 每词输出）。质量对比建议 50 词起步。
        </p>
        <p>每次运行都会记录所用组件的精确版本快照（含提示词内容哈希），事后可审计任何一次结果是谁产出的。</p>
        <p>不选 Agent 则走「点选模式」（服务器环境变量配置的供应商）。</p>
        <p>输出标「解析失败」= 模型返回的内容不符合 schema（通常是提示词或模型质量问题）；「调用失败」= 网络/端点问题。</p>
      </Section>

      <Section title="安全须知">
        <p>· 本站存有 LLM 密钥，<strong>不要分享账号</strong>；</p>
        <p>· 登录会话 90 天内有效（规划中，当前 7 天），公用电脑用完记得退出；</p>
        <p>· 密钥出现泄露嫌疑：立即在供应商侧作废该 key，然后在配置中心更新新 key。</p>
      </Section>

      <Section title="常见问题">
        <p>· <strong>登录 200 但进不去/一直跳登录</strong>：本站要求 HTTPS，请确认地址栏是 https://forge.sailingfor.com。</p>
        <p>· <strong>拉模型列表失败</strong>：密钥无效或端点不对；核对 Base URL 是否带 <Code>/v1</Code>。</p>
        <p>· <strong>实验长时间 running</strong>：推理型模型单词可能要 1-2 分钟，50 词批次请耐心；超 10 分钟算异常。</p>
      </Section>
    </div>
  );
}
