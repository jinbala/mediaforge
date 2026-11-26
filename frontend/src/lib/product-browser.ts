import type { ProductLinkPreview } from '@/lib/api'

const service = 'http://127.0.0.1:8317'
const localOrigins = new Set(['http://127.0.0.1:3101', 'http://localhost:3101', 'http://127.0.0.1:3100', 'http://localhost:3100'])

export function canUseProductBrowser() {
  return typeof window !== 'undefined' && localOrigins.has(window.location.origin)
}

export async function readProductInBrowser(url: string, signal: AbortSignal, interactive = false): Promise<ProductLinkPreview> {
  let response
  try {
    response = await fetch(service + (interactive ? '/login' : '/preview'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
      credentials: 'omit',
      signal,
    })
  } catch (error) {
    if (signal.aborted) throw error
    throw new Error('本机登录助手未启动，请运行 scripts/start-zwd-login.ps1 后重试。')
  }
  const result = await response.json()
  if (!response.ok) throw new Error(result.error || '商品图片读取失败，请重试。')
  return result
}
