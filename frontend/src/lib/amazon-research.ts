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

export function formatAmazonReport(report: AmazonReport, brief: string, run?: AmazonRun | null) {
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
  const archive = run?.snapshot.product_archive
  if (archive) {
    lines.push('', '### 商品归档', '', ...amazonArchiveCounts(archive).map(([label, count]) => '- ' + label + ' · ' + count))
    lines.push('', amazonArchiveCoverage(archive))
  }
  if (report.listings.length) {
    // 下载报告逐条保留原始商品样本和链接，便于按 ASIN 查找每个尺码。
    lines.push('', '### 商品样本', ...report.listings.map((item) => {
      const facts = [item.rank, item.asin, item.price, item.rating && item.rating + ' 星', item.rating_count && item.rating_count + ' 个评分']
      const main = '- [' + bilingual(item) + '](' + item.url + ') · ' + facts.filter(Boolean).map((fact) => escape(String(fact))).join(' · ')
      const identity = archive?.identities[item.asin]
      if (!identity || !run) return main
      const sources = amazonListingSources(run, item.asin)
      return [main, '  - ' + escape(amazonIdentityLabel(identity)),
        ...sources.map((source) => '  - 栏目来源 [' + bilingual(source) + '](' + (source.final_url ?? source.url) + ')'),
      ].join('\n')
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

export type AmazonRunStatus = 'queued' | 'running' | 'done' | 'partial_fail' | 'failed' | 'queue_failed' | 'interrupted'
export type AmazonStepStatus = 'pending' | 'success' | 'empty' | 'incomplete' | 'failed'
export type AmazonRetryTarget = 'resume' | 'failed'
export type AmazonRunSummary = {
  id: string
  mode: 'store'
  source: string
  canonical_source: string
  status: AmazonRunStatus
  stage: string
  title: string | null
  updated_at: string
  created_at: string
  attempt: number
}
export type AmazonEvidence = {
  key: string
  url: string
  final_url: string | null
  home_url: string | null
  title: string
  title_zh: string | null
  status: AmazonStepStatus
  kind: 'section' | 'suggestions'
  method: string
  collected_at: string | null
  reason: string | null
  code: string | null
  attempts: number
  duration_ms: number | null
  observed_count: number | null
  role: string | null
  excerpt: string | null
  products: (AmazonListing & {
    price_currency: string | null
    price_label?: string | null
    family_asin?: string | null
    color_group_asin?: string | null
    color?: string | null
    size?: string | null
  })[]
}
type AmazonStoreIdentity = {
  family_asin: string | null
  color_group_asin: string | null
  color: string | null
  size: string | null
  standalone: boolean
}

type AmazonStoreArchive = {
  asin_count: number
  family_count: number
  color_group_count: number
  unknown_family_count: number
  unknown_color_count: number
  unknown_size_count: number
  identities: Record<string, AmazonStoreIdentity>
}

export type AmazonRun = AmazonRunSummary & {
  error: string | null
  inputs: { source: string; keyword: string }
  report: AmazonReport | null
  snapshot: {
    version: number
    site: 'US'
    currency: 'USD'
    source_language: string
    report_language: string
    store_root: string | null
    store_scope?: {
      section_limit: number
      asin_limit_per_section: number
      navigation_truncated: boolean | null
    } | null
    translation_status: AmazonStepStatus
    translation_missing: string[]
    notes: string[]
    evidence: AmazonEvidence[]
    asin_sources: Record<string, string[]>
    product_archive?: AmazonStoreArchive | null
  }
}

export const amazonRunLabels: Record<AmazonRunStatus, string> = {
  queued: '等待队列', running: '进行中', done: '已完成', partial_fail: '部分完成',
  failed: '采集失败', queue_failed: '队列派发失败', interrupted: '进度中断',
}
export const amazonStepLabels: Record<AmazonStepStatus, string> = {
  pending: '待采集', success: '成功', empty: '空页', incomplete: '样本不完整', failed: '失败',
}

export function activeAmazonRun(run: AmazonRun) {
  return ['queued', 'running'].includes(run.status)
}

export function amazonTime(time: string) {
  return new Date(time).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) + '（上海）'
}

export function downloadAmazonFile(contents: string, filename: string, type = 'text/markdown') {
  const url = URL.createObjectURL(new Blob([contents], { type: type + ';charset=utf-8' }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function amazonListingSources(run: AmazonRun, asin: string) {
  const keys = new Set(run.snapshot.asin_sources[asin] ?? [])
  return run.snapshot.evidence.filter((item) => keys.has(item.key))
}

export function amazonArchiveCounts(archive: AmazonStoreArchive) {
  return [
    ['去重 ASIN', archive.asin_count],
    ['已确认款式（含独立款）', archive.family_count],
    ['已确认配色组', archive.color_group_count],
  ] as const
}

export function amazonArchiveCoverage(archive: AmazonStoreArchive) {
  return `待确认的 ASIN · 同款 ${archive.unknown_family_count} 个，配色 ${archive.unknown_color_count} 个，尺码 ${archive.unknown_size_count} 个。`
}

export function amazonIdentityLabel(identity: AmazonStoreIdentity) {
  const family = identity.standalone ? '独立款' : '同款组 ' + (identity.family_asin ?? '待确认')
  const color = identity.color_group_asin
    ? '配色组 ' + identity.color_group_asin + (identity.color ? '（' + identity.color + '）' : '')
    : '配色归属待确认'
  return [family, color, '尺码 ' + (identity.size ?? '待确认')].join(' · ')
}

export function amazonEvidenceSampleId(sectionKey: string, asin: string) {
  return `evidence-${sectionKey}-${asin}`
}

export function amazonArchiveSampleId(asin: string) {
  return `archive-${asin}`
}

export function revealAmazonSample(id: string) {
  // 先展开对应的栏目或尺码组，浏览器才能滚动到具体 ASIN。
  const details = document.getElementById(id)?.closest('details')
  if (details) details.open = true
}

export function amazonCollectionRange(run: AmazonRun) {
  const sections = run.snapshot.evidence.filter((item) => item.kind === 'section')
  const status: Record<AmazonStepStatus, number> = {
    pending: 0, success: 0, empty: 0, incomplete: 0, failed: 0,
  }
  for (const section of sections) status[section.status]++
  return {
    section_count: sections.length,
    section_status_counts: status,
    raw_card_count: sections.reduce((total, section) => total + section.products.length, 0),
    unique_asin_count: Object.keys(run.snapshot.asin_sources).length,
  }
}

export function formatAmazonStoreSamples(run: AmazonRun) {
  // 导出同一个服务端快照，保留各栏目的原始价格、评分、链接及缺值。
  return JSON.stringify({
    task_id: run.id,
    source: run.source,
    created_at: run.created_at,
    updated_at: run.updated_at,
    status: run.status,
    attempt: run.attempt,
    collection_range: amazonCollectionRange(run),
    ...run.snapshot,
  }, null, 2) + '\n'
}
