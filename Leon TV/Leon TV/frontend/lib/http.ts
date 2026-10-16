/** Shared response handling for JSON APIs. Never treat a failed request as empty data. */
export function errorMessage(error: unknown, fallback = '请求失败'): string {
  return error instanceof Error ? error.message : fallback;
}

export async function readJsonResponse<T>(response: Response): Promise<T> {
  const text = await response.text();
  let payload: unknown;
  try {
    payload = text.trim() ? JSON.parse(text) : undefined;
  } catch {
    throw new Error(response.ok ? '接口响应不是有效 JSON，请稍后重试。' : `请求失败（HTTP ${response.status}），请检查服务连接。`);
  }
  if (!response.ok) {
    const data = payload && typeof payload === 'object' ? payload as Record<string, unknown> : {};
    const nested = data.error && typeof data.error === 'object' ? data.error as Record<string, unknown> : {};
    const message = [data.detail, nested.message, data.error, data.message].find(value => typeof value === 'string' && value.trim());
    throw new Error(`${message || '请求失败'}（HTTP ${response.status}）`);
  }
  if (payload === undefined || payload === null || typeof payload !== 'object') {
    throw new Error('接口响应为空或格式错误，请稍后重试。');
  }
  return payload as T;
}

export async function requestJson<T>(url: string, options?: RequestInit): Promise<T> {
  return readJsonResponse<T>(await fetch(url, options));
}
