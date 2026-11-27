import type { AmazonRun } from './amazon-research'

export const costFields = {
  purchase_usd: '采购 USD/件', inbound_usd: '头程 USD/件', fulfillment_usd: '履约 USD/件', storage_usd: '仓储 USD/件',
  referral_rate: '佣金 %', ads_rate: '广告 %', returns_rate: '退货损耗 %', fixed_monthly_usd: '月固定成本 USD',
  launch_fixed_usd: '一次性启动费 USD', lean_units: '精简首批件数', standard_units: '标准首批件数', monthly_units: '中性月销量件数',
} as const

export type CategoryCosts = Record<keyof typeof costFields, string | number> & {
  target_asin?: string; target_price_usd?: string; source_note: string; effective_on: string; version: string; verified_quote_note?: string;
}
export type CategoryInputs = { subcategories: string[]; costs: CategoryCosts | null }
export type CategorySection = {
  key: string; title: string; reqs: string[]; notes: string[];
  tables: { title: string; columns: string[]; rows: { cells: (string | null)[]; asins: string[] }[] }[];
}
export type CategoryAnalysis = { version: string; status: 'complete' | 'incomplete'; sections: CategorySection[] }

export function readCategoryInputs(form: FormData): CategoryInputs {
  const value = (key: string) => String(form.get(key) ?? '').trim()
  const subcategories = value('subcategory-links').split(/\r?\n/).map((url) => url.trim()).filter(Boolean)
  if (subcategories.length > 5) throw new Error('最多输入 5 个子类目链接。')
  if (!form.has('include-category-costs')) return { subcategories, costs: null }
  const rows = Object.keys(costFields) as (keyof typeof costFields)[]
  const costs = Object.fromEntries(rows.map((key) => {
    const input = value('category-cost-' + key)
    if (key.endsWith('_rate')) return [key, (Number(input) / 100).toFixed(4)]
    if (key.endsWith('_units')) return [key, Number(input)]
    return [key, input]
  })) as Record<keyof typeof costFields, string | number>
  return { subcategories, costs: { ...costs, target_asin: value('category-target-asin').toUpperCase() || undefined,
    target_price_usd: value('category-target-price') || undefined, source_note: value('category-cost-source'),
    effective_on: value('category-cost-date'), version: value('category-cost-version'),
    verified_quote_note: value('category-cost-verified') || undefined } }
}

const safe = (text: string) => text.replace(/[\\*_#<>[\]`!|~]/g, '\\$&').replace(/[\r\n]+/g, ' ')

export function categoryMarkdown(run: AmazonRun, selected?: CategorySection) {
  const lines = ['# Amazon 美国站类目深调研', '', '任务：' + run.id, '来源：' + run.source,
    '采样时间：' + (run.report?.collected_at ?? run.created_at), '规则版本：' + (run.snapshot.category_analysis?.version ?? '待生成'), '']
  const sections = selected ? [selected] : run.snapshot.category_analysis?.sections ?? []
  if (!selected) lines.push('## 报告索引', ...sections.map((section) => '- ' + safe(section.title) + ' · ' + section.reqs.join('／')), '')
  for (const section of sections) {
    lines.push('## ' + safe(section.title), '', ...section.notes.map((note) => '- ' + safe(note)), '')
    for (const table of section.tables) {
      lines.push('### ' + safe(table.title), '', '| ' + [...table.columns, 'ASIN 证据'].map(safe).join(' | ') + ' |',
        '| ' + Array(table.columns.length + 1).fill('---').join(' | ') + ' |')
      for (const row of table.rows) lines.push('| ' + [...row.cells.map((item) => safe(item ?? '未知')), row.asins.map((asin) => `[${asin}](https://www.amazon.com/dp/${asin})`).join(' ')].join(' | ') + ' |')
      lines.push('')
    }
  }
  lines.push('## 榜单原始样本', '', ...run.snapshot.notes.map((note) => '- ' + safe(note)), '')
  for (const board of run.snapshot.evidence.filter((item) => item.kind === 'board')) {
    lines.push('### ' + safe(board.title_zh ?? board.title), '来源：' + (board.final_url ?? board.url),
      '采样时间：' + (board.collected_at ?? '待采集'), '状态：' + (board.reason ? safe(board.reason) : board.status), '')
    for (const row of board.products) lines.push(`- ${safe(row.rank ?? '未知排名')} [${row.asin}](${row.url}) · ${safe(row.title)} · ${safe(row.price ?? '未知价格')} ${safe(row.price_currency ?? '')} · 评分 ${row.rating ?? '未知'} · 评分数 ${row.rating_count ?? '未知'}${row.image_url ? ` · [主图](${row.image_url})` : ''}`)
    lines.push('')
  }
  return lines.join('\n') + '\n'
}

export function categorySamples(run: AmazonRun, board?: string) {
  const scope = run.snapshot.category_scope
  const evidence = run.snapshot.evidence.filter((row) => !board || row.key === board)
  return JSON.stringify({ task_id: run.id, inputs: run.inputs, source: run.source, created_at: run.created_at,
    status: run.status, site: 'US', currency: 'USD', scope, evidence, asin_sources: run.snapshot.asin_sources,
    category_analysis: board ? undefined : run.snapshot.category_analysis }, null, 2) + '\n'
}
