const PDD_PRODUCT_HOSTS = new Set(['mobile.yangkeduo.com', 'mobile.pinduoduo.com'])

export type ManualProductLink = {
  url: string
  sku: string
  platform: '拼多多' | '17网'
}

function* shareUrls(text: string) {
  const matches = text.replaceAll('\\:', ':').match(/https:\/\/[^\s<>\[\]()「」【】（）。，！]+/gi) || []
  for (const candidate of matches) {
    let url: URL
    try {
      url = new URL(candidate)
    } catch {
      continue
    }
    if (!url.port && !url.username && !url.password) yield url
  }
}

export function douyinProductLink(text: string) {
  for (const url of shareUrls(text)) {
    if (url.hostname === 'v.douyin.com' && /^\/[a-zA-Z0-9_-]{3,64}\/?$/.test(url.pathname)) {
      return url.origin + url.pathname.replace(/\/?$/, '/')
    }
    if (url.hostname === 'haohuo.jinritemai.com' && url.pathname === '/ecommerce/trade/detail/index.html') {
      url.hash = ''
      return url.href
    }
  }
  return null
}

export function manualProductLink(text: string): ManualProductLink | null {
  for (const url of shareUrls(text)) {
    if (url.hostname === '17zwd.com' || url.hostname.endsWith('.17zwd.com')) {
      const itemId = /^\/item\/([0-9]{1,24})\/?$/.exec(url.pathname)?.[1]
      if (itemId) return { url: url.origin + '/item/' + itemId, sku: '17ZWD-' + itemId, platform: '17网' }
    }
    if (!PDD_PRODUCT_HOSTS.has(url.hostname)) continue
    if (url.pathname !== '/goods.html' && url.pathname !== '/goods2.html') continue
    const goodsId = url.searchParams.get('goods_id') || ''
    const shareCode = url.searchParams.get('ps') || ''
    const params = new URLSearchParams()
    if (/^[0-9]{1,24}$/.test(goodsId)) params.set('goods_id', goodsId)
    if (/^[a-zA-Z0-9_-]{1,64}$/.test(shareCode)) params.set('ps', shareCode)
    if (params.size) {
      const sku = params.has('goods_id') ? `PDD-${goodsId}` : `PDD-SHARE-${shareCode}`
      return { url: `${url.origin}${url.pathname}?${params}`, sku, platform: '拼多多' }
    }
  }
  return null
}
