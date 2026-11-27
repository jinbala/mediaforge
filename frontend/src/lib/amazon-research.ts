import type { CategoryAnalysis, CategoryInputs } from './amazon-category'

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
  mode: 'store' | 'bestsellers'
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
  kind: 'section' | 'suggestions' | 'board' | 'analysis'
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

export type AmazonRun = AmazonRunSummary & {
  error: string | null
  inputs: { source: string; keyword: string; category?: CategoryInputs | null }
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
    notes: string[]
    evidence: AmazonEvidence[]
    asin_sources: Record<string, string[]>
    product_archive?: AmazonStoreArchive | null
    product_matrix?: AmazonStoreProductMatrix | null
    lifecycle?: AmazonStoreLifecycle | null
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
