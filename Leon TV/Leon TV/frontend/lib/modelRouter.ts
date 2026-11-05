/**
 * 模型路由：按「已配置的密钥 + 能力」自动挑选每一类任务用哪个模型。
 *
 * 当前是**前端路由**（第一版）：只读取已加载的模型目录和 provider 凭据，
 * 产出一个可审阅的选择结果，由用户确认后写入生成配置。
 * 它不会绕过服务端校验，也不会在缺能力时假装可用——缺什么就明确返回 missing。
 */

export type ModelCapability = 'llm' | 'vlm' | 't2i' | 'i2i' | 'video';

export interface RouterGroup {
  provider: string;
  models: Array<{ id: string }>;
}

export type RouterCatalog = Record<ModelCapability, RouterGroup[]>;

export interface ProviderCredentials {
  [provider: string]: { api_key?: string; access_key?: string; secret_key?: string } | undefined;
}

export interface ModelPick {
  provider: string;
  model: string;
}

export interface RouterPlan {
  picks: Partial<Record<ModelCapability, ModelPick>>;
  /** 没有可用 provider 的能力，调用方必须据此拦住生成，而不是猜一个模型。 */
  missing: ModelCapability[];
}

/**
 * 优先级顺序：越靠前越优先。它是当前已知的可用性/质量结论，
 * 不是厂商排名——例如 dashscope 的 qwen 文本、tudou 的 seedance-2.0 都是实测过的。
 */
const PREFERENCE: Record<ModelCapability, Array<{ provider: string; models?: string[] }>> = {
  llm: [
    { provider: 'dashscope', models: ['qwen3.7-max'] },
    { provider: 'deepseek', models: ['deepseek-chat', 'deepseek-v4-flash'] },
    { provider: 'openai', models: ['gpt-4o'] },
    { provider: 'gemini', models: ['gemini-2.5-flash'] },
    { provider: 'tudou', models: ['claude-sonnet-5.5', 'gemini-3.5-flash'] },
  ],
  vlm: [
    { provider: 'dashscope', models: ['qwen3.5-plus'] },
    { provider: 'gemini', models: ['gemini-2.0-flash'] },
    { provider: 'openai', models: ['gpt-4o'] },
    { provider: 'tudou', models: ['claude-sonnet-5.5', 'gemini-3.5-flash'] },
  ],
  t2i: [
    { provider: 'tudou', models: ['gpt-image-2-all'] },
    { provider: 'ark', models: ['doubao-seedream-5-0-260128'] },
    { provider: 'dashscope', models: ['wan2.7-image'] },
    { provider: 'openai', models: ['gpt-image-2'] },
  ],
  i2i: [
    { provider: 'tudou', models: ['gpt-image-2-all'] },
    { provider: 'ark', models: ['doubao-seedream-5-0-260128'] },
    { provider: 'dashscope', models: ['wan2.7-image'] },
    { provider: 'openai', models: ['gpt-image-2'] },
  ],
  video: [
    { provider: 'tudou', models: ['seedance-2.0'] },
    { provider: 'dashscope', models: ['wan2.7-i2v'] },
    { provider: 'ark', models: ['doubao-seedance-2-0-260128'] },
    { provider: 'kling', models: ['kling-v3'] },
  ],
};

export function hasCredentials(provider: string, credentials: ProviderCredentials): boolean {
  const entry = credentials[provider];
  if (!entry) return false;
  if (provider === 'kling') return Boolean(entry.access_key && entry.secret_key);
  return Boolean(entry.api_key);
}

function pickFor(capability: ModelCapability, groups: RouterGroup[], credentials: ProviderCredentials): ModelPick | null {
  for (const preference of PREFERENCE[capability]) {
    if (!hasCredentials(preference.provider, credentials)) continue;
    const group = groups.find(item => item.provider === preference.provider);
    if (!group || !group.models.length) continue;
    const preferred = (preference.models || []).find(id => group.models.some(model => model.id === id));
    const model = preferred || group.models[0].id;
    if (model) return { provider: preference.provider, model };
  }
  return null;
}

export function planModels(catalog: RouterCatalog, credentials: ProviderCredentials): RouterPlan {
  const capabilities: ModelCapability[] = ['llm', 'vlm', 't2i', 'i2i', 'video'];
  const picks: RouterPlan['picks'] = {};
  const missing: ModelCapability[] = [];
  for (const capability of capabilities) {
    const pick = pickFor(capability, catalog[capability] || [], credentials);
    if (pick) picks[capability] = pick;
    else missing.push(capability);
  }
  return { picks, missing };
}
