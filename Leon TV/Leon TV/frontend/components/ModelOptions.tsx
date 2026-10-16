import type { ProviderGroup } from '@/config/models';

/** Keep a saved but unavailable value visible; never silently show a different model. */
export default function ModelOptions({ value, groups }: { value: string; groups: ProviderGroup[] }) {
  const available = groups.some(group => group.models.some(model => model.id === value));
  return <>
    {!available && <option value={value} disabled>{value ? `${value}（不可用）` : '请选择可用模型'}</option>}
    {groups.map(group => <optgroup key={group.provider} label={group.label}>
      {group.models.map(model => <option key={model.id} value={model.id}>{model.label}</option>)}
    </optgroup>)}
  </>;
}
