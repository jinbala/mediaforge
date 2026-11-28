import type { CategoryAnalysis, CategoryInputs } from './amazon-category'
import type { ProductCosts, ProductDepth } from './amazon-product'
import type { AmazonResearchContext } from './amazon-research-common'

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
  description?: string | null
  category_path?: string[] | null
  first_available?: string | null
  image_count?: number | null
  has_aplus?: boolean | null
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
    if ('image_count' in product) {
      lines.push('', '### Listing 结构',
        '类目路径：' + escape(product.category_path?.join(' / ') ?? '未采到'),
        '首次可见上架日期：' + escape(product.first_available ?? '未采到'),
        '可识别图片数：' + (product.image_count ?? '未采到'),
        'A+：' + (product.has_aplus == null ? '未采到' : product.has_aplus ? '公开页可见' : '识别到空模块'),
        '描述：' + escape(product.description ?? '未采到'))
    }
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
  const matrix = run?.snapshot.product_matrix
  if (matrix && run) lines.push(...formatAmazonProductMatrix(run, matrix, escape))
  if (run?.snapshot.lifecycle) lines.push(...formatAmazonLifecycle(run.snapshot.lifecycle, escape))
  if (run?.snapshot.competition) lines.push(...formatAmazonCompetition(run.snapshot.competition, escape))
  if (run?.snapshot.revenue) lines.push(...formatAmazonRevenue(run, run.snapshot.revenue, escape))
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
export type AmazonRetryTarget = 'resume' | 'failed' | 'translation'
export type AmazonRunSummary = {
  id: string
  mode: ResearchMode
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
  kind: 'section' | 'suggestions' | 'board' | 'analysis' | 'product' | 'search' | 'competitor' | 'reviews'
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

export type AmazonPriceBand = {
  min_cents: number
  max_cents: number | null
  asins: string[]
  share_pct: number | null
}
export type AmazonStoreProductMatrix = {
  rules_version: string
  asin_count: number
  prices: Record<string, { section: string; price_cents: number }>
  price_bands: AmazonPriceBand[]
  missing_price: string[]
  missing_eligible_price: string[]
  sections: {
    section: string
    asin_count: number | null
    priced_count: number
    rating_count: number
    average_rating: number | null
    rating_total_count: number
    median_rating_total: number | null
  }[]
  overlaps: { sections: [string, string]; asins: string[] }[]
  product_types: Record<string, string[]>
  specification_combinations: Record<string, string[]>
  model_candidates: Record<string, string[]>
  rating_leaders: { asin: string; count: number; family_asin: string | null }[]
  new_sections: Record<string, string[]>
}

export type AmazonStoreLifecycle = {
  colors: Record<string, number>
  sizes: Record<string, number>
  missing_color: number
  missing_size: number
  dates: {
    asin: string
    url: string
    collected_at: string
    label: 'first_available' | 'release_date' | null
    date_text: string | null
    problem: string | null
  }[]
  change: {
    previous_run_id: string
    previous_collected_at: string
    current_collected_at: string
    section_keys: string[]
    newly_seen: string[]
    no_longer_seen: string[]
    series_changes: { family_asin: string; newly_seen: string[]; no_longer_seen: string[] }[]
  } | null
}

export type AmazonMarketListing = {
  asin: string
  url: string
  collected_at: string
  title: string | null
  brand: string | null
  store_url: string | null
  category: string | null
  price: string | null
  price_currency: string | null
  rating: string | null
  rating_count: string | null
  monthly_bought?: number | null
  monthly_bought_label?: string | null
  highlights: string[]
  image_count: number | null
  has_aplus: boolean | null
  problem: string | null
}

export type AmazonStoreCompetition = {
  targets: string[]
  searches: {
    target_asin: string
    query: string
    url: string
    collected_at: string
    status: 'success' | 'empty' | 'failed'
    candidates: { asin: string; title: string }[]
    problem: string | null
  }[]
  listings: Record<string, AmazonMarketListing>
  pairs: {
    target_asin: string
    competitor_asin: string
    shared_terms: string[]
    shared_category: string | null
    audience: string | null
    price_gap_cents: number | null
    swot: Record<string, string>
  }[]
}

type AmazonScenarioValues = [number, number, number]
export type AmazonRevenueTotals = { units: AmazonScenarioValues | null; cents: AmazonScenarioValues | null }
export type AmazonRevenueSample = {
  family_asin: string
  asin: string
  members: string[]
  sections: string[]
  segment: 'representative' | 'tail'
  listing: AmazonMarketListing | null
  price_cents: number | null
  monthly: AmazonRevenueTotals
}
export type AmazonStoreRevenue = {
  family_count: number
  unknown_family_count: number
  representative_count: number
  samples: AmazonRevenueSample[]
  factors_percent: AmazonScenarioValues
  annual_months: number
  monthly: Record<'representative' | 'tail' | 'sample', AmazonRevenueTotals>
  public_store?: AmazonRevenueTotals
  section_monthly: Record<string, AmazonRevenueTotals>
}

export type AmazonRun = AmazonRunSummary & {
  error: string | null
  inputs: { source: string; keyword: string; review_samples: string; site?: 'US'; category?: CategoryInputs | null; product_costs?: ProductCosts | null; context?: AmazonResearchContext | null; supplemental_notes?: string }
  report: AmazonReport | null
  snapshot: {
    version: number
    site: 'US'
    currency: 'USD'
    source_language: string
    report_language: string
    store_root: string | null
    category_scope?: { main_limit: number; child_limit: number; child_asin_limit: number; discovery_truncated: boolean } | null
    category_analysis?: CategoryAnalysis | null
    store_scope?: {
      section_limit: number
      asin_limit_per_section: number
      navigation_truncated: boolean | null
    } | null
    translation_status: AmazonStepStatus
    translation_missing: string[]
    field_translations?: Record<string, string>
    notes: string[]
    evidence: AmazonEvidence[]
    asin_sources: Record<string, string[]>
    product_depth?: ProductDepth | null
    product_archive?: AmazonStoreArchive | null
    product_matrix?: AmazonStoreProductMatrix | null
    lifecycle?: AmazonStoreLifecycle | null
    competition?: AmazonStoreCompetition | null
    revenue?: AmazonStoreRevenue | null
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
  const sections = run.snapshot.evidence.filter((item) => item.kind === (run.mode === 'store' ? 'section' : 'board'))
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

export function amazonPriceBandLabel(band: AmazonPriceBand) {
  return band.max_cents === null
    ? `USD ≥${band.min_cents / 100}`
    : `USD ${band.min_cents / 100}–<${band.max_cents / 100}`
}

export function amazonPriceCoverage(matrix: AmazonStoreProductMatrix) {
  const cents = Object.values(matrix.prices).map((quote) => quote.price_cents)
  const percentage = matrix.asin_count ? (cents.length / matrix.asin_count * 100).toFixed(1) + '%' : '未知'
  const range = cents.length ? ` 已采价格 USD ${(Math.min(...cents) / 100).toFixed(2)}–${(Math.max(...cents) / 100).toFixed(2)}。` : ''
  return `有效美元报价 ${cents.length}/${matrix.asin_count}（覆盖率 ${percentage}）。缺价 ${matrix.missing_price.length} 个，有原报价但无法纳入美元单价统计 ${matrix.missing_eligible_price.length} 个。` + range
}

export const amazonPortfolioPriceRule = '价格带按 USD 单价左闭右开。跨栏目同一 ASIN 取登记顺序中第一个有效报价。占比的分母为有有效美元价的去重 ASIN。'

function formatAmazonProductMatrix(run: AmazonRun, matrix: AmazonStoreProductMatrix, escape: (value: string) => string) {
  const sections = new Map(run.snapshot.evidence.map((item) => [item.key, item]))
  const section = (key: string) => {
    const item = sections.get(key)
    return item ? '[' + escape(item.title_zh ?? item.title) + '](' + (item.final_url ?? item.url) + ')' : key
  }
  const groups = (label: string, values: Record<string, string[]>) => [
    '', '#### ' + label,
    ...(Object.keys(values).length ? Object.entries(values).map(([name, asins]) => `- ${escape(name)} · ${asins.length} 个 · ${asins.join('、')}`) : ['样本未知。']),
  ]
  return [
    '', '### 产品组合与价格带', '',
    `规则版本 ${matrix.rules_version}。评分均值和评分数中位数按栏目 ASIN 样本计算。评分数可能跨同款共享。`,
    amazonPriceCoverage(matrix), amazonPortfolioPriceRule,
    ...(matrix.missing_price.length ? ['缺价 ASIN · ' + matrix.missing_price.join('、')] : []),
    ...(matrix.missing_eligible_price.length ? ['原报价待核对 ASIN · ' + matrix.missing_eligible_price.join('、')] : []),
    ...matrix.price_bands.flatMap((band) => [
      `- ${amazonPriceBandLabel(band)} · ${band.asins.length} 个 · 占比 ${band.share_pct ?? '未知'}${band.share_pct === null ? '' : '%'}`,
      ...band.asins.map((asin) => `  - ${asin} · USD ${(matrix.prices[asin].price_cents / 100).toFixed(2)} · ${section(matrix.prices[asin].section)}`),
    ]),
    '', '#### 栏目商品与评分', '',
    ...matrix.sections.map((row) => `- ${section(row.section)} · 商品 ${row.asin_count ?? '未知'} · 有效价 ${row.priced_count} · 评分均值 ${row.average_rating ?? '未知'}（n=${row.rating_count}）· 评分数中位 ${row.median_rating_total ?? '未知'}（n=${row.rating_total_count}）`),
    '', '#### 栏目重叠', '',
    ...(matrix.overlaps.length ? matrix.overlaps.map((row) => `- ${section(row.sections[0])} / ${section(row.sections[1])} · ${row.asins.length} 个 · ${row.asins.join('、')}`) : ['已采样本中未观察到栏目重叠。']),
    ...groups('商品类型（平台原文）', matrix.product_types),
    ...groups('配色与尺码组合', matrix.specification_combinations),
    ...groups('标题编号候选（型号待核实）', matrix.model_candidates),
    '', '#### 公开评分数领先款', '',
    ...(matrix.rating_leaders.length ? matrix.rating_leaders.map((row) => `- [${row.asin}](https://www.amazon.com/dp/${row.asin}) · ${row.count} 个评分 · 同款组 ${row.family_asin ?? '待确认'}`) : ['公开评分数未知。']),
    '', '#### 新品栏目所见样本', '',
    ...(Object.keys(matrix.new_sections).length ? Object.entries(matrix.new_sections).map(([key, asins]) => `- ${section(key)} · ${asins.length} 个 · ${asins.join('、')}`) : ['公开导航中未识别到新品栏目，新品样本未知。']),
  ]
}

export function amazonListingDateLabel(item: AmazonStoreLifecycle['dates'][number]) {
  if (!item.date_text) return item.problem ?? '公开详情日期未知。'
  return (item.label === 'first_available' ? 'Amazon 首次可售' : '商品发布日期') + ' · ' + item.date_text
}

export function amazonSampleChangeLabel(change: NonNullable<AmazonStoreLifecycle['change']>) {
  return `共同可读 ${change.section_keys.length} 栏 · 新观察 ${change.newly_seen.length} 个 ASIN · 未再观察 ${change.no_longer_seen.length} 个 ASIN。`
}

export const amazonUnknownLaunchCadence = '上新节奏未知。样本变化可能来自公开排序、栏目或覆盖变化。'

function formatAmazonLifecycle(lifecycle: AmazonStoreLifecycle, escape: (text: string) => string) {
  const entries = (rows: Record<string, number>) => Object.entries(rows).map(([key, count]) => `${escape(key)} ${count} 个`).join('、') || '未知'
  const change = lifecycle.change
  return [
    '', '### 新品节奏与产品线', '',
    amazonUnknownLaunchCadence,
    '配色 · ' + entries(lifecycle.colors) + ` · 待确认 ${lifecycle.missing_color} 个 ASIN`,
    '尺码 · ' + entries(lifecycle.sizes) + ` · 待确认 ${lifecycle.missing_size} 个 ASIN`,
    ...lifecycle.dates.map((item) => `- [${item.asin}](${item.url}) · ${escape(amazonListingDateLabel(item))} · 采集 ${amazonTime(item.collected_at)}`),
    ...(!lifecycle.dates.length ? ['公开日期样本未知。'] : []),
    ...(change ? [
      `上期任务 ${change.previous_run_id} · ${amazonTime(change.previous_collected_at)} → ${amazonTime(change.current_collected_at)}`,
      amazonSampleChangeLabel(change),
      '共同栏目 · ' + change.section_keys.join('、'),
      '新观察 ASIN · ' + (change.newly_seen.join('、') || '无'),
      '未再观察 ASIN · ' + (change.no_longer_seen.join('、') || '无'),
      ...change.series_changes.map((item) => `- 规格组 ${item.family_asin} · 新观察 ${item.newly_seen.join('、') || '无'} · 未再观察 ${item.no_longer_seen.join('、') || '无'}`),
      ...(!change.series_changes.length ? ['共同可核实的系列未观察到变化。'] : []),
    ] : ['当前没有同一店铺、同一栏目可比的历史采样。']),
  ]
}

export const amazonBenchmarkScope = '对标仅涵盖公开搜索及入选商品详情。完整店铺款式、市场平均评分和实际搜索量未知。'

export function amazonBenchmarkBasis(pair: AmazonStoreCompetition['pairs'][number]) {
  const category = pair.shared_category ? '同末级类目 ' + pair.shared_category : '末级类目待核对'
  return category + ' · 共同标题用词 ' + pair.shared_terms.join('、')
}

export function amazonBenchmarkCoverage(competition: AmazonStoreCompetition) {
  const peers = [...new Set(competition.pairs.map((pair) => pair.competitor_asin))].map((asin) => competition.listings[asin])
  const brands = new Set(peers.map((item) => item.brand).filter(Boolean)).size
  const categories = new Set(peers.map((item) => item.category).filter(Boolean)).size
  const ratings = peers.flatMap((item) => item.rating && /^\d(?:\.\d+)?$/.test(item.rating) ? [Number(item.rating)] : [])
  const range = ratings.length ? `${Math.min(...ratings)}–${Math.max(...ratings)} / 5（n=${ratings.length}）` : '未知'
  return `对标样本 ${peers.length} 个 ASIN、${brands} 个品牌、${categories} 个已见末级类目。对标评分范围 ${range}。`
}

export function amazonListingPresentation(item: AmazonMarketListing) {
  return `图组 ${item.image_count ?? '未知'} 张 · A+ ${item.has_aplus == null ? '未知' : item.has_aplus ? '已观察' : '未见内容'}`
}

function formatAmazonCompetition(competition: AmazonStoreCompetition, escape: (value: string) => string) {
  return [
    '', '### 竞品与品牌定位', '', amazonBenchmarkScope, amazonBenchmarkCoverage(competition),
    ...competition.searches.map((search) => `- [${escape(search.query)}](${search.url}) · ${search.candidates.length} 张候选卡片 · ${amazonTime(search.collected_at)}${search.problem ? ' · ' + escape(search.problem) : ''}`),
    ...Object.values(competition.listings).filter((item) => item.problem).map((item) => `- [${item.asin}](${item.url}) · ${escape(item.problem ?? '')} · ${amazonTime(item.collected_at)}`),
    ...(competition.pairs.length ? competition.pairs.flatMap((pair) => {
      const items = [competition.listings[pair.target_asin], competition.listings[pair.competitor_asin]]
      return [
        '', `#### ${items.map((item) => escape(item.brand ?? '未知') + ' ' + item.asin).join(' / ')}`,
        escape(amazonBenchmarkBasis(pair)),
        `受众依据 ${pair.audience ?? '待验证'}。标价差（目标款减竞品）${pair.price_gap_cents === null ? '未知' : 'USD ' + (pair.price_gap_cents / 100).toFixed(2)}。`,
        ...items.flatMap((item) => [
          `- [${escape(item.title ?? item.asin)}](${item.url}) · 采集 ${amazonTime(item.collected_at)}`,
          `  - 价 ${escape(item.price ?? '未知')} ${item.price_currency ?? ''} · 评分 ${item.rating ?? '未知'} · 评分数 ${item.rating_count ?? '未知'} · ${amazonListingPresentation(item)}`,
          `  - 公开卖点 ${escape(item.highlights[0]?.slice(0, 150) ?? '未知')}`,
          item.store_url ? `  - [品牌店铺](${item.store_url})` : '  - 公开店铺链接未知。',
        ]),
        ...Object.entries(pair.swot).map(([label, text]) => `- ${label}（基于上面两个 ASIN）· ${escape(text)}`),
      ]
    }) : ['当前无可复核的对标配对。']),
  ]
}

export const amazonRevenueLabels = { representative: '代表款', tail: '长尾样本', sample: '已支持的店内样本汇总' } as const

export function amazonRevenueRows(revenue: AmazonStoreRevenue) {
  const { representative, tail, sample } = revenue.monthly
  return [
    { label: amazonRevenueLabels.representative, totals: representative },
    { label: amazonRevenueLabels.tail, totals: tail },
    { label: amazonRevenueLabels.sample, totals: sample },
    { label: '全店公开目录估算', totals: revenue.public_store ?? { units: null, cents: null } },
  ]
}

export function amazonRevenueCoverage(revenue: AmazonStoreRevenue) {
  const units = revenue.samples.filter((item) => item.monthly.units !== null).length
  const priced = revenue.samples.filter((item) => item.monthly.cents !== null).length
  const tail = revenue.family_count - revenue.representative_count
  return `已确认 ${revenue.family_count} 款（代表款 ${revenue.representative_count} 款，其他 ${tail} 款）。${revenue.samples.length} 款进入详情样本，${units} 款有销量基数，${priced} 款有营收依据。另有 ${revenue.unknown_family_count} 个同款关系待确认的 ASIN 未纳入汇总。`
}

export function amazonRevenueRules(revenue: AmazonStoreRevenue) {
  const factors = revenue.factors_percent.map((value) => (value / 100).toFixed(2)).join('／')
  return [
    `低／中／高按公开「过去一月 N+ 购买」基数 × ${factors}，按 1 次购买计 1 个销售包装并四舍五入。倍数为情景假设，实际销量和情景上界均未经验证。月毛营收为情景件数 × 同一 ASIN 公开 USD 标价，年化乘 ${revenue.annual_months}，未扣折扣、退货、平台费用或税费，也未校正季节。`,
    '同款使用 Amazon 规格关系核对，每款只用一个 ASIN。即使变体或栏目共享评分池，也不累计评分数或用评论率、BSR 换算销量。栏目按该款已采来源等分，件数和分币分别按稳定栏目顺序分余数。',
    '全店实际月／年销量及营收未知。只有公开店铺导航完整、所有已采 ASIN 的同款关系可确认、每款有相应估算依据时，才输出公开目录的整体情景。样本汇总仅覆盖有相应证据的款式。',
  ]
}

export function amazonRevenueSummary(totals: AmazonRevenueTotals, annualMonths: number) {
  const units = (values: AmazonScenarioValues, months: number) => values.map((value) => (value * months).toLocaleString('zh-CN')).join('／')
  const usd = (values: AmazonScenarioValues, months: number) => 'USD ' + values.map((value) => (value * months / 100).toFixed(2)).join('／')
  const { units: count, cents } = totals
  return `月销量 ${count ? units(count, 1) + ' 包装' : '未知'} · 年化销量 ${count ? units(count, annualMonths) + ' 包装' : '未知'} · 月毛营收 ${cents ? usd(cents, 1) : '未知'} · 年化毛营收 ${cents ? usd(cents, annualMonths) : '未知'}`
}

export function amazonRevenueBasis(sample: AmazonRevenueSample) {
  const { listing } = sample
  if (!listing) return '公开详情待采。'
  if (listing.problem) return listing.problem
  const badge = listing.monthly_bought_label ? `${listing.monthly_bought_label}（基数 ${listing.monthly_bought}）` : '页面未见可用于估算的购买提示'
  const price = sample.price_cents !== null ? 'USD ' + (sample.price_cents / 100).toFixed(2) : `有效 USD 单价未知（原文 ${listing.price ?? '缺失'}）`
  return `${badge} · ${price} · 采集 ${amazonTime(listing.collected_at)}`
}

export function amazonRevenueSections(run: AmazonRun, revenue: AmazonStoreRevenue) {
  return run.snapshot.evidence.flatMap((section) => {
    const monthly = revenue.section_monthly[section.key]
    return monthly ? [{ section, monthly }] : []
  })
}

function formatAmazonRevenue(run: AmazonRun, revenue: AmazonStoreRevenue, escape: (value: string) => string) {
  const title = (section: AmazonEvidence) => section.title_zh ?? section.title
  const sections = amazonRevenueSections(run, revenue)
  const link = (section: AmazonEvidence) => `[${escape(title(section))}](${section.final_url ?? section.url})`
  return [
    '', '### 销量与营收估算', '', '情景顺序为低／中／高。',
    amazonRevenueCoverage(revenue), ...amazonRevenueRules(revenue),
    ...amazonRevenueRows(revenue).map(({ label, totals }) => `- ${label} · ${amazonRevenueSummary(totals, revenue.annual_months)}`),
    ...revenue.samples.map((sample) => {
      const name = sample.listing ? `[${sample.asin}](${sample.listing.url})` : sample.asin
      const sources = sections.filter(({ section }) => sample.sections.includes(section.key)).map(({ section }) => link(section))
      return `- ${amazonRevenueLabels[sample.segment]} ${name} · ${escape(amazonRevenueBasis(sample))} · ${amazonRevenueSummary(sample.monthly, revenue.annual_months)}\n  - 同款 ${sample.family_asin}（${sample.members.join('、')}） · 栏目 ${sources.join('、') || '未知'}`
    }),
    '', '#### 栏目贡献',
    ...sections.map(({ section, monthly }) => `- ${link(section)} · ${amazonRevenueSummary(monthly, revenue.annual_months)}`),
  ]
}
