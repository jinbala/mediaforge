import type { AmazonProduct } from './amazon-research'

export type ProductMedia = {
  url: string
  label: string | null
  width: number | null
  height: number | null
  poster: string | null
}

export type ProductReviewSample = {
  id: string
  text: string
  source: 'amazon' | 'manual'
  url: string | null
  stars: number | null
  date: string | null
  variant: string | null
  verified: boolean | null
  helpful: number | null
}

export type ProductPage = {
  product: AmazonProduct
  collected_at: string
  specs: Record<string, string>
  important: string[]
  aplus: string[]
  images: ProductMedia[]
  videos: ProductMedia[]
  currency: string | null
  price_amount: number | null
  rating_distribution: Record<string, number>
  reviews: ProductReviewSample[]
  parent_asin: string | null
  variant_asins: string[]
  field_evidence: Record<string, string>
  screenshot_data_url: string | null
}

export type ProductSearchRow = {
  asin: string
  title: string
  url: string
  rank: number
  brand: string | null
  price_amount: number | null
  currency: string | null
  sponsored: boolean | null
}

export type ProductSearch = {
  query: string
  url: string
  collected_at: string
  rows: ProductSearchRow[]
  result_min: number | null
  result_max: number | null
  result_label: string | null
}

export type ProductCandidate = {
  asin: string
  title: string
  url: string
  reasons: string[]
  queries: string[]
}

export type ProductReviewPage = {
  bucket: 'one' | 'two' | 'three' | 'recent'
  url: string
  collected_at: string
  rows: ProductReviewSample[]
  limited: boolean
}

export type ProductCitation = {
  label: string
  url: string
  collected_at: string | null
  asin: string | null
}

export type ProductTable = {
  title: string
  columns: string[]
  rows: (string | number | null)[][]
}

export const productSectionRequirements = [
  'REQ-021', 'REQ-022', 'REQ-023', 'REQ-024', 'REQ-025', 'REQ-026', 'REQ-027', 'REQ-028', 'REQ-029',
] as const

export type ProductSection = {
  req: (typeof productSectionRequirements)[number]
  title: string
  status: 'complete' | 'partial' | 'unknown'
  summary: string[]
  tables: ProductTable[]
  sources: ProductCitation[]
}

export type ProductShot = {
  id: string
  type: 'image' | 'video'
  title: string
  brief: string
  evidence: string[]
}

export type ProductTemplate = {
  title: string
  bullets: string[]
  description: string
  aplus: string[]
  backend_keywords: string
  conditions: string[]
}

export type ProductScenario = {
  name: string
  monthly_units: number
  annual_revenue: number | null
  annual_profit: number | null
}

export type ProductFinance = {
  currency: string | null
  price: number | null
  category: string | null
  rank: number | null
  assumptions: string[]
  missing: string[]
  scenarios: ProductScenario[]
  unit_costs: Record<string, number | null>
  unit_profit: number | null
  roi_pct: number | null
  fee_source: string | null
  fee_date: string | null
}

export type ProductAnalysis = {
  sections: ProductSection[]
  image_plan: ProductShot[]
  listing: ProductTemplate
  finance: ProductFinance
  decision: string
}

export type ProductDepth = {
  overview: ProductPage | null
  searches: ProductSearch[]
  candidates: ProductCandidate[]
  competitors: ProductPage[]
  review_pages: ProductReviewPage[]
  analysis: ProductAnalysis | null
}

export type ProductCosts = {
  purchase?: number
  inbound?: number
  fulfillment?: number
  storage?: number
  ad_pct?: number
  return_pct?: number
  referral_pct?: number
  sale_price?: number
  fee_date?: string
  fee_source?: string
  benchmark_bsr?: number
  benchmark_monthly_units?: number
  benchmark_category?: string
  benchmark_source?: string
  benchmark_date?: string
}

export const productCostFields = {
  purchase: '采购成本 / 件',
  inbound: '头程 / 件',
  fulfillment: '履约费用 / 件',
  storage: '仓储费用 / 件',
  ad_pct: '广告占比 %',
  return_pct: '退货损耗 %',
  referral_pct: '佣金占比 %',
  sale_price: '目标售价',
  fee_date: '费用有效日期',
  fee_source: '费用来源',
  benchmark_bsr: '对标 BSR',
  benchmark_monthly_units: '对标月销量件数',
  benchmark_category: '对标类目',
  benchmark_source: '对标数据来源',
  benchmark_date: '对标采样日期',
} as const

export const productCostNumberFields = [
  'purchase', 'inbound', 'fulfillment', 'storage', 'ad_pct', 'return_pct', 'referral_pct',
  'sale_price', 'benchmark_bsr', 'benchmark_monthly_units',
] as const

export function readProductCosts(form: FormData): ProductCosts | null {
  if (!form.has('include-product-costs')) return null
  const costs: ProductCosts = {}
  for (const key of productCostNumberFields) {
    const raw = String(form.get('product-cost-' + key) ?? '').trim()
    if (raw) costs[key] = key.endsWith('_pct') ? Number(raw) / 100 : Number(raw)
  }
  for (const key of ['fee_date', 'fee_source', 'benchmark_category', 'benchmark_source', 'benchmark_date'] as const) {
    const value = String(form.get('product-cost-' + key) ?? '').trim()
    if (value) costs[key] = value
  }
  return costs
}

const escapeMarkdown = (value: string) => value
  .replace(/[\\*_#[\]`!~]/g, '\\$&')
  .replace(/[<>]/g, (character) => character === '<' ? '&lt;' : '&gt;')
  .replace(/[\r\n]+/g, ' ')

const cellMarkdown = (value: string | number | null) => escapeMarkdown(value == null ? '未知' : String(value)).replace(/\|/g, '\\|')

const safeLink = (url: string | null | undefined, asset?: 'image' | 'video') => {
  const safe = asset ? safeProductAssetUrl(url, asset) : safeProductUrl(url)
  return safe?.replace(/[()]/g, (character) => character === '(' ? '%28' : '%29') ?? null
}

const markdownLink = (label: string, url: string | null | undefined, asset?: 'image' | 'video') => {
  const safe = safeLink(url, asset)
  return safe ? `[${escapeMarkdown(label)}](${safe})` : null
}

function markdownTable(table: ProductTable) {
  const columns = table.columns.length ? table.columns : ['数据']
  const lines = [
    `### ${escapeMarkdown(table.title)}`,
    '',
    `| ${columns.map(escapeMarkdown).join(' | ')} |`,
    `| ${columns.map(() => '---').join(' | ')} |`,
    ...table.rows.map((row) => `| ${columns.map((_, index) => cellMarkdown(row[index] ?? null)).join(' | ')} |`),
  ]
  return lines.join('\n')
}

export function productSectionMarkdown(section: ProductSection) {
  return [
    `## ${escapeMarkdown(section.title)}`,
    '',
    `要求：${escapeMarkdown(section.req)} · 状态：${section.status}`,
    '',
    ...(section.summary.length ? section.summary.map((line) => '- ' + escapeMarkdown(line)) : ['- 专题摘要未知。']),
    '',
    ...section.tables.map(markdownTable),
    ...(section.sources.length ? [
      '### 来源',
      '',
      ...section.sources.map((source) => {
        const label = markdownLink(source.label, source.url) ?? escapeMarkdown(source.label)
        return `- ${label}${source.asin ? ` · ASIN ${escapeMarkdown(source.asin)}` : ''}${source.collected_at ? ` · 采集 ${escapeMarkdown(source.collected_at)}` : ''}`
      }),
    ] : []),
  ].join('\n') + '\n'
}

function pageMarkdown(page: ProductPage, heading: string) {
  const product = page.product
  const storefront = markdownLink('品牌店铺', product.store_url)
  const thumbnail = markdownLink('主图', product.image_url, 'image')
  const lines = [
    `### ${escapeMarkdown(heading)} · ${escapeMarkdown(product.title)}`,
    '',
    `ASIN：${escapeMarkdown(product.asin)} · 品牌：${escapeMarkdown(product.brand ?? '未知')} · 采集：${escapeMarkdown(page.collected_at)}`,
    `价格：${page.price_amount ?? '未知'} ${escapeMarkdown(page.currency ?? '')} · 评分：${product.rating ?? '未知'} · 评分数：${product.rating_count ?? '未知'}`,
    `来源：${markdownLink('商品页', product.url) ?? '链接不可用'}${storefront ? ` · ${storefront}` : ''}${thumbnail ? ` · ${thumbnail}` : ''}`,
    `父 ASIN：${escapeMarkdown(page.parent_asin ?? '未知')} · 变体：${escapeMarkdown(page.variant_asins.join('、') || '未知')}`,
    '',
    ...(page.important.length ? ['卖点', ...page.important.map((item) => '- ' + escapeMarkdown(item)), ''] : []),
    ...(page.aplus.length ? ['A+ 内容', ...page.aplus.map((item) => '- ' + escapeMarkdown(item)), ''] : []),
    ...(Object.keys(page.specs).length ? ['规格', ...Object.entries(page.specs).map(([key, value]) => `- ${escapeMarkdown(key)}：${escapeMarkdown(value)}`), ''] : []),
    ...(Object.keys(page.rating_distribution).length ? ['评分分布', ...Object.entries(page.rating_distribution).map(([stars, count]) => `- ${stars} 星 · ${count}`), ''] : []),
    ...(Object.keys(page.field_evidence).length ? ['字段证据', ...Object.entries(page.field_evidence).map(([field, url]) => `- ${escapeMarkdown(field)} · ${markdownLink('来源', url) ?? '链接不可用'}`), ''] : []),
    ...(page.images.length ? ['图片', ...page.images.map((media, index) => `- ${escapeMarkdown(media.label || `图片 ${index + 1}`)} · ${markdownLink('查看', media.url, 'image') ?? '链接不可用'}`), ''] : []),
    ...(page.videos.length ? ['视频', ...page.videos.map((media, index) => `- ${escapeMarkdown(media.label || `视频 ${index + 1}`)} · ${markdownLink('查看', media.url, 'video') ?? '链接不可用'}`), ''] : []),
    ...(page.reviews.length ? ['评论样本', ...page.reviews.map((review) => `- ${review.source === 'manual' ? '手动提供' : '商品页'} · ${review.stars ?? '未知'} 星 · ${escapeMarkdown(review.text)}${review.url && safeLink(review.url) ? ` · [来源](${safeLink(review.url)})` : ''}`), ''] : []),
  ]
  return lines.join('\n')
}

export function productReportMarkdown(run: { id: string; source: string; created_at: string }, depth: ProductDepth) {
  const analysis = depth.analysis
  const sections = productSectionRequirements.map((req) => analysis?.sections.find((section) => section.req === req) ?? {
    req, title: req, status: 'unknown' as const, summary: ['本专题未返回分析内容，证据未知。'], tables: [], sources: [],
  })
  const lines = [
    '# Amazon 单品深度调研',
    '',
    `任务：${escapeMarkdown(run.id)}`,
    `来源：${safeLink(run.source) ? `[商品来源](${safeLink(run.source)})` : '链接不可用'}`,
    `创建时间：${escapeMarkdown(run.created_at)}`,
    '',
    '## 结论',
    '',
    escapeMarkdown(analysis?.decision ?? '分析结论未知。'),
    '',
    ...sections.map((section) => productSectionMarkdown(section)),
    ...(depth.overview ? [pageMarkdown(depth.overview, '商品页概览')] : ['## 商品页概览', '', '尚无商品页详情证据。']),
    '',
    '## 搜索采样与候选',
    '',
    ...depth.searches.flatMap((search) => [
      `### ${escapeMarkdown(search.query)}`,
      '',
      `采集 ${escapeMarkdown(search.collected_at)} · 结果范围 ${search.result_min ?? '未知'}–${search.result_max ?? '未知'} · ${escapeMarkdown(search.result_label ?? '')}`,
      `来源：${safeLink(search.url) ? `[搜索页](${safeLink(search.url)})` : '链接不可用'}`,
      ...search.rows.map((row) => `- ${row.rank ?? '未知'} · ${escapeMarkdown(row.asin)} · ${escapeMarkdown(row.title)} · ${escapeMarkdown(row.brand ?? '品牌未知')} · ${row.price_amount ?? '价格未知'} ${escapeMarkdown(row.currency ?? '')} · ${row.sponsored == null ? '广告状态未知' : row.sponsored ? '赞助' : '自然结果'}${safeLink(row.url) ? ` · [商品页](${safeLink(row.url)})` : ''}`),
      '',
    ]),
    ...depth.candidates.map((candidate) => `- ${escapeMarkdown(candidate.asin)} · ${escapeMarkdown(candidate.title)} · 原因 ${escapeMarkdown(candidate.reasons.join('；') || '未知')} · 查询 ${escapeMarkdown(candidate.queries.join('、') || '未知')}${safeLink(candidate.url) ? ` · [商品页](${safeLink(candidate.url)})` : ''}`),
    '',
    '## 竞品详情',
    '',
    ...depth.competitors.map((page) => pageMarkdown(page, '竞品')),
    '',
    '## 评论样本与采样页',
    '',
    ...depth.review_pages.flatMap((page) => [
      `### ${page.bucket} · ${escapeMarkdown(page.collected_at)}${page.limited ? ' · 样本受限' : ''} · ${safeLink(page.url) ? `[采样来源](${safeLink(page.url)})` : '链接不可用'}`,
      ...page.rows.map((review) => `- ${review.source === 'manual' ? '手动提供' : '商品页'} · ${review.stars ?? '未知'} 星 · ${escapeMarkdown(review.text)} · ${escapeMarkdown(review.date ?? '日期未知')} · ${escapeMarkdown(review.variant ?? '变体未知')} · ${review.verified == null ? '验证状态未知' : review.verified ? '已验证购买' : '未标记验证'} · 有帮助 ${review.helpful ?? '未知'}${review.url && safeLink(review.url) ? ` · [来源](${safeLink(review.url)})` : ''}`),
      '',
    ]),
    ...(analysis ? [
      '## 图片与视频方案',
      '',
      ...analysis.image_plan.map((shot) => `- ${escapeMarkdown(shot.type)} · ${escapeMarkdown(shot.title)}：${escapeMarkdown(shot.brief)} · 依据 ${escapeMarkdown(shot.evidence.join('；') || '未知')}`),
      '',
      '## Listing 文案模板',
      '',
      `标题：${escapeMarkdown(analysis.listing.title)}`,
      ...analysis.listing.bullets.map((item) => '- ' + escapeMarkdown(item)),
      `描述：${escapeMarkdown(analysis.listing.description)}`,
      ...analysis.listing.aplus.map((item) => `A+：${escapeMarkdown(item)}`),
      `后台关键词：${escapeMarkdown(analysis.listing.backend_keywords)}`,
      ...analysis.listing.conditions.map((item) => `注意事项：${escapeMarkdown(item)}`),
      '',
      '## 财务测算',
      '',
      ...analysis.finance.assumptions.map((item) => '- 假设：' + escapeMarkdown(item)),
      ...analysis.finance.missing.map((item) => '- 缺失：' + escapeMarkdown(item)),
      `类目 ${escapeMarkdown(analysis.finance.category ?? '未知')} · 排名 ${analysis.finance.rank ?? '未知'} · 售价 ${analysis.finance.price ?? '未知'} ${escapeMarkdown(analysis.finance.currency ?? '')}`,
      `单件利润 ${analysis.finance.unit_profit ?? '未知'} · ROI ${analysis.finance.roi_pct == null ? '未知' : `${analysis.finance.roi_pct}%`} · 费用来源 ${escapeMarkdown(analysis.finance.fee_source ?? '未知')} · 日期 ${escapeMarkdown(analysis.finance.fee_date ?? '未知')}`,
      ...Object.entries(analysis.finance.unit_costs).map(([key, value]) => `- 单件成本 ${escapeMarkdown(key)}：${value ?? '未知'}`),
      ...analysis.finance.scenarios.map((scenario) => `- ${escapeMarkdown(scenario.name)} · 月销 ${scenario.monthly_units ?? '未知'} · 年收入 ${scenario.annual_revenue ?? '未知'} · 年利润 ${scenario.annual_profit ?? '未知'}`),
    ] : []),
  ]
  return lines.join('\n').replace(/\n{3,}/g, '\n\n') + '\n'
}

export function productDepthJson(run: { id: string; source: string; created_at: string }, depth: ProductDepth) {
  return JSON.stringify({ task_id: run.id, source: run.source, created_at: run.created_at, product_depth: depth }, null, 2) + '\n'
}

export function safeProductUrl(input: string | null | undefined): string | null {
  if (!input) return null
  try {
    const url = new URL(input)
    const host = url.hostname.toLowerCase().replace(/^\[|\]$|\.$/g, '')
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return null
    if (host === 'localhost' || host.endsWith('.localhost') || host.endsWith('.local') || host.endsWith('.lan')
      || host.endsWith('.home') || host === 'internal' || host.endsWith('.internal') || host === '::1') return null
    if (host.includes(':') && !/^[23][0-9a-f]{0,3}:/i.test(host)) return null
    if (!host.includes(':') && !host.includes('.') && !/^\d+$/.test(host)) return null
    const ipv4 = host.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/)
    if (ipv4) {
      const octets = ipv4.slice(1).map(Number)
      if (octets.some((part) => part > 255) || octets[0] === 0 || octets[0] === 10 || octets[0] === 127
        || (octets[0] === 169 && octets[1] === 254) || (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31)
        || (octets[0] === 192 && octets[1] === 168)) return null
    }
    return url.href
  } catch {
    return null
  }
}

export function safeProductAssetUrl(input: string | null | undefined, kind: 'image' | 'video'): string | null {
  const safe = safeProductUrl(input)
  if (!safe) return null
  const { protocol, hostname, port, pathname } = new URL(safe)
  if (protocol !== 'https:' || port) return null
  if (kind === 'image') {
    return (hostname === 'm.media-amazon.com' || hostname === 'images-na.ssl-images-amazon.com')
      && /^\/images\/I\/[^/]+$/.test(pathname) ? safe : null
  }
  return /(?:^|\.)(?:amazon|media-amazon)\.com$/.test(hostname) ? safe : null
}
