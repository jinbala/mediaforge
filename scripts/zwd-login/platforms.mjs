const pddHosts = new Set(['mobile.yangkeduo.com', 'mobile.pinduoduo.com'])
const pddPaths = new Set(['/goods.html', '/goods2.html'])
const douyinHost = 'haohuo.jinritemai.com'
const douyinPath = '/ecommerce/trade/detail/index.html'
const idPattern = /^[0-9]{1,24}$/
const sharePattern = /^[a-zA-Z0-9_-]{1,64}$/
const itemPath = /^\/item\/([0-9]{1,24})\/?$/

function isZwdHost(host) {
  return /^(?:[a-z0-9-]+\.)?17zwd\.com$/.test(host)
}

function productId(url, platform) {
  if (url.protocol !== 'https:' || url.username || url.password || url.port) return null
  if (platform === '17zwd' && isZwdHost(url.host)) return itemPath.exec(url.pathname)?.[1] ?? null
  if (platform === 'pdd' && pddHosts.has(url.host) && pddPaths.has(url.pathname)) {
    const id = url.searchParams.get('goods_id')
    return idPattern.test(id ?? '') ? id : null
  }
  if (platform === 'douyin' && url.host === douyinHost && url.pathname === douyinPath) {
    const id = url.searchParams.get('id')
    return idPattern.test(id ?? '') ? id : null
  }
  return null
}

export function productLink(value) {
  let url
  try {
    url = new URL(value)
  } catch {
    return null
  }
  if (url.protocol !== 'https:' || url.port || url.username || url.password) return null
  const id = productId(url, '17zwd')
  if (id) {
    return { url: new URL(`/item/${id}`, url.origin), platform: '17zwd', name: '17 网', id, sku: `17ZWD-${id}` }
  }
  if (pddHosts.has(url.host) && pddPaths.has(url.pathname)) {
    const id = productId(url, 'pdd')
    const share = url.searchParams.get('ps')
    const params = new URLSearchParams()
    if (id) params.set('goods_id', id)
    if (sharePattern.test(share ?? '')) params.set('ps', share)
    if (!params.size) return null
    return {
      url: new URL(url.pathname + '?' + params, url.origin),
      platform: 'pdd',
      name: '拼多多',
      id,
      sku: id ? `PDD-${id}` : `PDD-SHARE-${share}`,
    }
  }
  if (url.host === 'v.douyin.com' && /^\/[a-zA-Z0-9_-]{3,64}\/?$/.test(url.pathname)) {
    return { url: new URL(url.pathname.replace(/\/?$/, '/'), url.origin), platform: 'douyin', name: '抖音' }
  }
  if (url.host === douyinHost && url.pathname === douyinPath) {
    url.hash = ''
    return { url, platform: 'douyin', name: '抖音', id: productId(url, 'douyin') }
  }
  return null
}

export function rememberProductPage(href, product) {
  const id = productId(new URL(href), product.platform)
  if (id && !product.id) product.id = id
}

export function isProductPage(href, product) {
  const url = new URL(href)
  const id = productId(url, product.platform)
  return Boolean(id && id === product.id && (product.platform !== '17zwd' || url.origin === product.url.origin))
}

export function isLoginPage(url, product) {
  if (url.protocol !== 'https:') return false
  if (product.platform === '17zwd') return url.host === 'pp.17zwd.com' && url.pathname === '/login'
  if (product.platform === 'pdd') return pddHosts.has(url.host) && url.pathname === '/login.html'
  return url.host === 'passport.douyin.com' ||
    (url.host === douyinHost && /(?:^|\/)login(?:\.html|\/|$)/.test(url.pathname))
}

export function isPlatformPage(url, platform) {
  if (url.protocol !== 'https:') return false
  if (platform === '17zwd') return isZwdHost(url.host)
  if (platform === 'pdd') return pddHosts.has(url.host)
  return [douyinHost, 'v.douyin.com', 'www.douyin.com', 'passport.douyin.com'].includes(url.host)
}

export function isProductPhoto(url, platform) {
  if (url.protocol !== 'https:' || url.username || url.password || url.port) return false
  if (platform === '17zwd') {
    return /^img[0-9]+-gms\.17zwd\.com$/.test(url.host) && url.pathname.startsWith('/imgextra/')
  }
  if (platform === 'douyin') {
    return /^[a-z0-9-]+\.ecombdimg\.com$/.test(url.host) &&
      /^\/(?:img|obj)\/ecom-shop-material\//.test(url.pathname)
  }
  return /^(?:img|t[0-9]+img|commimg|p[0-9]+)\.(?:pddpic\.com|yangkeduo\.com|pinduoduo\.com)$/.test(url.host)
}

export async function readGallery(page, product) {
  const result = await page.evaluate(platform => {
    const meta = name => document.querySelector(`meta[property="${name}"]`)?.content
    const imageSource = image => image?.currentSrc || image?.src
    if (platform === '17zwd') {
      const container = document.querySelector('[class*="index-introSwiper-"]')
      const title = document.querySelector('[class*="index-titleBox-"]')?.textContent
      if (!container || !title) return null
      const selector = 'img[src*="/imgextra/"]'
      const cover = container.querySelector('[class*="index-bigImg-"] ' + selector)
      return { title, photos: [cover, ...container.querySelectorAll(selector)].map(imageSource) }
    }
    if (platform === 'douyin') {
      const container = document.querySelector('.head-figure')
      const title = document.querySelector('.title-info__text')?.textContent || meta('og:title')
      if (!container || !title) return null
      const cover = container.querySelector('.head-figure__media-view__video__main-pic')
      return { title, photos: [cover, ...container.querySelectorAll('img')].map(imageSource) }
    }
    const heading = document.querySelector('[data-testid="goods-name"], [class*="goodsName"], [class*="goods-name"], h1')
    const gallery = Array.from(document.querySelectorAll('img, [style*="background-image"]'))
      .flatMap(element => {
        const rect = element.getBoundingClientRect()
        if (rect.width < 150 || rect.height < 150 || rect.bottom <= 0 || rect.top >= innerHeight) return []
        const source = imageSource(element) ||
          /^url\(["']?(.*?)["']?\)$/.exec(element.style.backgroundImage)?.[1]
        return source ? [{ source, top: Math.max(rect.top, 0), area: rect.width * rect.height }] : []
      })
      .sort((first, second) => first.top - second.top || second.area - first.area)
    return {
      title: heading?.textContent || meta('og:title') || document.title,
      photos: [meta('og:image'), ...gallery.slice(0, 16).map(image => image.source)],
    }
  }, product.platform)
  if (!result) return null
  const photos = [...new Set(result.photos.filter(Boolean))].flatMap(source => {
    try {
      const url = new URL(source, page.url())
      return isProductPhoto(url, product.platform) ? [url] : []
    } catch {
      return []
    }
  }).slice(0, 3)
  const title = result.title?.trim().slice(0, 500)
  return photos.length ? { title: title || `${product.name}商品`, photos } : null
}
