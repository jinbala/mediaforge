export const scriptTools = {
  keywords: { req: 'REQ-039', label: '关键词采样' },
  listing: { req: 'REQ-040', label: 'Listing 结构提取' },
  ppc: { req: 'REQ-041', label: 'PPC 竞品要素提取' },
  fba: { req: 'REQ-042', label: 'FBA 费用计算' },
  landed: { req: 'REQ-043', label: '关税和到岸成本计算' },
  hs: { req: 'REQ-044', label: 'HS 编码查询' },
} as const

export type ScriptTool = keyof typeof scriptTools
export type ScriptStatus = 'success' | 'empty' | 'partial_fail' | 'failed' | 'incomplete'
export const scriptStatus: Record<ScriptStatus, string> = {
  success: '成功', empty: '未找到样本', partial_fail: '部分成功', failed: '失败', incomplete: '待补充',
}
export const tiers: Record<string, string> = {
  small_standard: '小号标准', large_standard: '大号标准', large_bulky: '大件', extra_large: '超大件',
}
export const fieldLabels: Record<string, string> = {
  title: '标题', brand: '品牌', price: '公开价格', price_currency: '页面币种', category: '类目',
  sales_ranks: '畅销排名（BSR）', highlights: '五点／卖点', rating_count: '公开评分数', rating: '评分',
  description: '描述', category_path: '类目路径', first_available: '首次可见上架日期',
  image_count: '可识别图片数', has_aplus: 'A+ 内容', image_url: '主图',
}
export const fbaCategories = {
  home_kitchen: '家居与厨房', toys: '玩具与游戏', sports: '运动与户外',
  consumer_electronics: '消费电子', clothing: '服装与配饰', media: '媒体',
} as const

type Source = { version: string; effective_from: string; effective_to: string; source_url: string }
export type Charge = { name: string; amount: string; source: Source }
export type Duty = { name: string; basis: 'customs_value' | 'per_unit' | 'fixed'; rate?: string; amount?: string; source: Source }
type FbaInput = {
  tool: 'fba'; category: keyof typeof fbaCategories; dimensions_in: [string, string, string]; weight_lb: string;
  price_usd: string; ship_date: string; storage_start: string; storage_days: number;
  selling_plan: 'individual' | 'professional'; dangerous_goods: boolean;
  fulfillment_override?: Charge; service_fees: Charge[];
}
type LandedInput = {
  tool: 'landed'; origin: string; destination: 'US'; hs_code: string; as_of: string;
  currency: string; goods_value: string; freight: string; quantity: number;
  insurance?: string; brokerage_usd?: string; inland_usd?: string; customs_value_usd?: string;
  usd_per_currency?: string; exchange_source?: Source;
  duties: Duty[]; duties_confirmed: boolean;
}
export type ScriptInput = { site?: 'US' } & (
  { tool: 'keywords'; keyword: string; queries?: string[] }
  | { tool: 'listing'; source: string }
  | { tool: 'ppc'; sources: string[] }
  | FbaInput | LandedInput | { tool: 'hs'; query: string }
)

type Evidence = { source_url: string; collected_at: string; status: ScriptStatus; reason: string | null }
export type ProductEvidence = Evidence & {
  asin: string; title: string | null; brand: string | null; price: string | null; price_currency: string | null;
  category: string | null; sales_ranks: string[] | null; highlights: string[] | null; rating_count: string | null;
  field_sources: Record<string, string>; missing_fields: string[];
}
type Listing = ProductEvidence & {
  rating: string | null; description: string | null; category_path: string[] | null; first_available: string | null;
  image_count: number | null; has_aplus: boolean | null; image_url: string | null;
}
type Line = { name: string; amount_usd: string | null; formula: string; source: Source | null; source_type: 'official' | 'manual' | 'calculated' | 'unknown' }
export type ScriptResult = {
  report_id: string; schema_version: 1; site: 'US'; collected_at: string; status: ScriptStatus; warnings: string[];
} & (
  { tool: 'keywords'; keyword: string; language: 'en-US'; query_limit: number; suggestion_limit_per_query: number;
    keywords: { keyword: string; queries: string[] }[]; samples: (Evidence & { query: string; suggestions: string[] })[] }
  | { tool: 'listing'; listing: Listing }
  | { tool: 'ppc'; competitors: ProductEvidence[] }
  | ({ lines: Line[]; pending: string[]; currency: 'USD'; total_usd: string | null; known_subtotal_usd: string } & (
    { tool: 'fba'; inputs: FbaInput; size_tier: string; sorted_dimensions_in: string[]; dimensional_weight_lb: string;
      shipping_weight_lb: string; volume_ft3: string }
    | { tool: 'landed'; inputs: LandedInput; hs_code: string; customs_value_usd: string | null;
      per_unit_usd: string | null; review_urls: string[] }
  ))
  | { tool: 'hs'; query: string; observed_count: number; candidate_limit: number; source_url: string; pending: string[];
    candidates: { code: string; description: string; general_rate_text: string | null; special_rate_text: string | null;
      column_2_rate_text: string | null; source_url: string; official_effective_period: string | null; pending: string[] }[] }
)

function statusOf(results: Evidence[], hasResults: boolean): ScriptStatus {
  const failed = results.filter((row) => row.status === 'failed').length
  return failed === results.length ? 'failed' : failed ? 'partial_fail' : hasResults ? 'success' : 'empty'
}

export function mergeScriptRetry(previous: ScriptResult, updated: ScriptResult): ScriptResult {
  // 单独重试失败查询／ASIN，保留原有成功证据及各自的采样时间。
  if (previous.tool === 'keywords' && updated.tool === 'keywords') {
    const replacements = new Map(updated.samples.map((sample) => [sample.query, sample]))
    const samples = previous.samples.map((old) => old.status === 'failed' ? replacements.get(old.query) ?? old : old)
    const keywords = new Map<string, { keyword: string; queries: string[] }>()
    for (const sample of samples) {
      for (const suggestion of sample.suggestions) {
        const word = suggestion.trim().replace(/\s+/g, ' ')
        const key = word.toLowerCase()
        if (key === previous.keyword.toLowerCase()) continue
        const entry = keywords.get(key) ?? { keyword: word, queries: [] }
        if (!entry.queries.includes(sample.query)) entry.queries.push(sample.query)
        keywords.set(key, entry)
      }
    }
    return { ...updated, samples, keywords: [...keywords.values()], status: statusOf(samples, !!keywords.size) }
  }
  if (previous.tool === 'ppc' && updated.tool === 'ppc') {
    const replacements = new Map(updated.competitors.map((sample) => [sample.asin, sample]))
    const competitors = previous.competitors.map((old) => old.status === 'failed' ? replacements.get(old.asin) ?? old : old)
    return { ...updated, competitors, status: statusOf(competitors, true) }
  }
  return updated
}

export function readScriptHistory(key: string): ScriptResult[] {
  const data: unknown = JSON.parse(localStorage.getItem(key + ':tools') ?? '[]')
  return Array.isArray(data) ? data.filter((item) => item?.schema_version === 1 && Object.hasOwn(scriptTools, item.tool)).slice(0, 12) : []
}

export function saveScriptHistory(key: string, result: ScriptResult) {
  const items = readScriptHistory(key).filter((item) => item.report_id !== result.report_id)
  localStorage.setItem(key + ':tools', JSON.stringify([result, ...items].slice(0, 12)))
}
