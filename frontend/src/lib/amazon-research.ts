export type ResearchMode = 'product' | 'bestsellers' | 'store'

type AmazonStoreVariant = {
  group_asin: string
  size: string
  title: string
  title_zh?: string | null
  color?: string | null
  color_zh?: string | null
}

export type AmazonListing = {
  asin: string
  title: string
  url: string
  title_zh?: string | null
  image_url?: string | null
  price: string | null
  rating: string | null
  rating_count: string | null
  rank: string | null
  store_variant?: AmazonStoreVariant | null
}

export type AmazonProduct = AmazonListing & {
  brand: string | null
  store_url: string | null
  category: string | null
  sales_ranks: string[]
  highlights: string[]
  reviews: { text: string; rating: string | null; source: 'amazon' | 'manual' }[]
  review_topics: { label: string; matches: number; suggestion: string }[]
  image_brief: string
}

export type AmazonReport = {
  mode: ResearchMode
  source_url: string
  title: string
  title_zh?: string | null
  collected_at: string
  product: AmazonProduct | null
  listings: AmazonListing[]
  store_pages: { title: string; title_zh?: string | null; url: string }[]
  keyword: string | null
  suggestions: string[]
  warnings: string[]
}

export const AMAZON_BRIEF_KEY = 'mediaforge:amazon-image-brief'
export const AMAZON_BRIEF_PARAM = 'amazonBrief'
export const AMAZON_BRIEF_LIMIT = 2000

const AMAZON_HISTORY_LIMIT = 20

export function amazonHistoryKey(tenantId: string, userId: string) {
  // 在同一浏览器内按企业和账号分别保存调研报告。
  return `mediaforge:amazon-research-history:v1:${tenantId}:${userId}`
}

export function readAmazonHistory(key: string): AmazonReport[] {
  const stored = window.localStorage.getItem(key)
  try {
    const entries: unknown = JSON.parse(stored ?? '[]')
    if (!Array.isArray(entries)) return []
    return entries.filter((entry) => (
      entry && ['product', 'bestsellers', 'store'].includes(entry.mode)
      && typeof entry.source_url === 'string' && typeof entry.title === 'string'
      && typeof entry.collected_at === 'string' && Array.isArray(entry.listings)
      && Array.isArray(entry.store_pages) && Array.isArray(entry.warnings)
      && Array.isArray(entry.suggestions)
    )).slice(0, AMAZON_HISTORY_LIMIT)
  } catch {
    return []
  }
}

export function saveAmazonHistory(key: string, report: AmazonReport) {
  // 使用后端返回的规范链接。同一类型和链接重查后更新快照并移到最前。
  const previous = readAmazonHistory(key).filter((entry) => (
    entry.mode !== report.mode || entry.source_url !== report.source_url
  ))
  const entries = [report, ...previous].slice(0, AMAZON_HISTORY_LIMIT)
  window.localStorage.setItem(key, JSON.stringify(entries))
}

export function groupAmazonListings(items: AmazonListing[], mode: ResearchMode) {
  // 榜单保留原有排名顺序，店铺按后端给出的规格组折叠，缺少规格信息的商品单独展示。
  if (mode !== 'store') return items.map((item) => [item])
  const groups = new Map<string, AmazonListing[]>()
  for (const item of items) {
    const key = item.store_variant ? `size:${item.store_variant.group_asin}` : item.asin
    const group = groups.get(key)
    if (group) group.push(item)
    else groups.set(key, [item])
  }
  return [...groups.values()]
}

export function formatAmazonReport(report: AmazonReport, brief: string) {
  // 将外部标题和评论中的 Markdown 符号转义，中文译名与原名一起导出。
  const escape = (value: string) => value.replace(/[\\*_#<>\[\]\x60!|~]/g, '\\$&')
  const bilingual = ({ title, title_zh }: { title: string; title_zh?: string | null }) =>
    escape(title_zh ? `${title_zh}（${title}）` : title)
  const lines = [
    '# Amazon 美国站调研',
    '',
    '## ' + escape(report.title_zh ?? report.title),
    '',
    '来源：' + report.source_url,
    '采集时间：' + report.collected_at,
  ]
  const product = report.product
  if (product) {
    lines.push(
      '',
      'ASIN：' + product.asin,
      '品牌：' + escape(product.brand ?? '未公开'),
      '类目：' + escape(product.category ?? '未公开'),
      '页面价格：' + escape(product.price ?? '未公开'),
      '评分：' + (product.rating ?? '未公开'),
      '评分数：' + (product.rating_count ?? '未公开'),
    )
    if (product.sales_ranks.length) lines.push('', '### 畅销排名', ...product.sales_ranks.map((rank) => '- ' + escape(rank)))
    if (product.store_url) lines.push('品牌店铺：' + product.store_url)
    if (product.highlights.length) lines.push('', '### 商品页卖点', ...product.highlights.map((item) => '- ' + escape(item)))
    if (product.review_topics.length) {
      lines.push('', '### 评论样本主题', ...product.review_topics.map((topic) => (
        '- ' + topic.label + '（' + topic.matches + ' 条提及）：' + topic.suggestion
      )))
    }
    if (product.reviews.length) {
      lines.push('', '### 评论样本', ...product.reviews.map((review) => (
        '- ' + (review.source === 'manual' ? '手动录入' : '商品公开页') +
        (review.rating ? ' · ' + review.rating + ' 星' : '') + '：' + escape(review.text)
      )))
    }
    if (brief.trim()) lines.push('', '### 图片需求', brief.split(/\r?\n/).map(escape).join('\n\n'))
  }
  if (report.listings.length) {
    // 下载报告逐条保留原始商品样本和链接，便于按 ASIN 查找每个尺码。
    lines.push('', '### 商品样本', ...report.listings.map((item) => {
      const facts = [item.rank, item.asin, item.price, item.rating && item.rating + ' 星', item.rating_count && item.rating_count + ' 个评分']
      return '- [' + bilingual(item) + '](' + item.url + ') · ' + facts.filter(Boolean).map((fact) => escape(String(fact))).join(' · ')
    }))
  }
  if (report.store_pages.length) {
    lines.push('', '### 店铺栏目', ...report.store_pages.map((page) => `- [${bilingual(page)}](${page.url})`))
  }
  if (report.keyword) {
    lines.push('', '### 搜索联想词 · ' + escape(report.keyword))
    lines.push(...(report.suggestions.length ? report.suggestions.map((word) => '- ' + escape(word)) : ['当前无可用结果。']))
  }
  if (report.warnings.length) lines.push('', '### 数据说明', ...report.warnings.map((warning) => '- ' + warning))
  return lines.join('\n') + '\n'
}
