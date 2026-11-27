import type { ReactNode } from 'react'
import { amazonTime } from '@/lib/amazon-research'
import { fieldLabels, tiers, type ProductEvidence, type ScriptResult } from '@/lib/amazon-script-tools'

export function EvidenceLink({ href, children }: { href: string; children: ReactNode }) {
  // 浏览器历史可能来自旧数据，链接也只允许无凭证的 HTTPS 地址。
  const url = URL.canParse(href) ? new URL(href) : null
  if (!url || url.protocol !== 'https:' || url.username || url.password) return <>{children}</>
  return <a className="text-primary underline-offset-2 hover:underline" target="_blank" rel="noreferrer" href={url.href}>{children}</a>
}

function Table({ headers, rows }: { headers: string[]; rows: ReactNode[][] }) {
  return <div className="overflow-x-auto rounded-md border">
    <table className="w-full text-left text-sm">
      <thead className="bg-muted/50"><tr>{headers.map((text) => <th scope="col" className="px-3 py-2 font-medium" key={text}>{text}</th>)}</tr></thead>
      <tbody className="divide-y">{rows.map((cells, row) => <tr key={row}>{cells.map((cell, col) => <td className="min-w-24 max-w-80 break-words px-3 py-2 align-top" key={col}>{cell}</td>)}</tr>)}</tbody>
    </table>
  </div>
}

function ProductTable({ rows, listing = false }: { rows: ProductEvidence[]; listing?: boolean }) {
  const fields = Object.keys(fieldLabels).slice(0, listing ? undefined : 8)
  function value(row: ProductEvidence, field: string) {
    const raw: unknown = Object.entries(row).find(([name]) => name === field)?.[1]
    if (raw === null || raw === undefined) return <span className="text-muted-foreground">未采到</span>
    if (field === 'image_url' && typeof raw === 'string') return <EvidenceLink href={raw}>查看主图</EvidenceLink>
    const text = Array.isArray(raw) ? raw.join(' · ') : typeof raw === 'boolean' ? raw ? '公开页可见' : '识别到空模块' : String(raw)
    return row.field_sources[field] ? <EvidenceLink href={row.field_sources[field]}>{text}</EvidenceLink> : text
  }
  return <Table headers={['字段', ...rows.map((row) => row.asin)]} rows={[
    ['商品页及采集时间', ...rows.map((row) => <div key={row.asin}><EvidenceLink href={row.source_url}>打开 Amazon 商品页</EvidenceLink><p>{amazonTime(row.collected_at)}</p>{row.reason && <p role="status">{row.reason}</p>}</div>)],
    ...fields.map((field) => [fieldLabels[field], ...rows.map((row) => value(row, field))]),
  ]} />
}

export function AmazonScriptResults({ report }: { report: ScriptResult }) {
  switch (report.tool) {
    case 'keywords': return <>
      <p className="text-sm">种子词：{report.keyword} · {report.language} · {report.samples.length}/{report.query_limit} 个查询 · {report.keywords.length} 个去重联想词</p>
      {report.keywords.length > 0 && <div className="max-h-80 overflow-auto"><Table headers={['长尾词', '命中查询']} rows={report.keywords.map((word) => [word.keyword, word.queries.join(' · ')])} /></div>}
      <details className="rounded-md border p-3 text-sm" open={!report.keywords.length}>
        <summary className="cursor-pointer font-medium">查询来源与失败原因（{report.samples.length}）</summary>
        <div className="mt-2"><Table headers={['查询', '采集时间', '公开样本或结果']} rows={report.samples.map((sample) => [
          <EvidenceLink key={sample.query} href={sample.source_url}>{sample.query}</EvidenceLink>, amazonTime(sample.collected_at), sample.reason ?? (sample.suggestions.join(' · ') || '未返回联想词'),
        ])} /></div>
      </details>
    </>
    case 'listing': return <ProductTable rows={[report.listing]} listing />
    case 'ppc': return <><p className="text-sm">{report.competitors.filter((row) => row.status === 'success').length}/{report.competitors.length} 个去重 ASIN 可读取</p><ProductTable rows={report.competitors} /></>
    case 'fba':
    case 'landed': return <>
      {report.tool === 'fba' ? <p className="text-sm">尺寸档：{tiers[report.size_tier] ?? report.size_tier} · {report.sorted_dimensions_in.join(' × ')} in · 计费重量 {Number(report.shipping_weight_lb).toFixed(3)} lb · 体积 {Number(report.volume_ft3).toFixed(5)} ft³</p>
        : <p className="text-sm">{report.inputs.origin} → {report.inputs.destination} · HTSUS {report.hs_code} · 海关计税价格 {report.customs_value_usd ?? '待确认'} USD</p>}
      <Table headers={['项目', '金额 USD', '计算依据', '费率来源与有效期']} rows={report.lines.map((line) => [line.name, line.amount_usd ?? '待确认', line.formula, line.source ?
        <div key={line.name}><EvidenceLink href={line.source.source_url}>{line.source.version}</EvidenceLink><p>{line.source.effective_from} 至 {line.source.effective_to}</p><p>{line.source_type === 'official' ? '官方费率' : line.source_type === 'manual' ? '人工提供' : '待确认'}</p></div> : '—'])} />
      <p className="text-sm font-medium">已知小计：{report.known_subtotal_usd} USD · {report.tool === 'fba' ? '单件费用' : '整票到岸成本'}：{report.total_usd === null ? '待确认' : report.total_usd + ' USD'}{report.tool === 'landed' && report.per_unit_usd !== null && ` · 单件 ${report.per_unit_usd} USD`}</p>
      {report.pending.length > 0 && <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">{report.pending.map((text, i) => <li key={i}>{text}</li>)}</ul>}
      {report.tool === 'landed' && <ul className="flex flex-wrap gap-3 text-sm">{report.review_urls.map((url, i) => <li key={url}><EvidenceLink href={url}>{['USITC 税目', 'CBP 海关估价', 'USTR 301', 'CBP 归类裁定'][i]}</EvidenceLink></li>)}</ul>}
    </>
    case 'hs': return <>
      <p className="text-sm">查询：{report.query} · 返回 {report.candidates.length} 项（本次匹配 {report.observed_count}，展示上限 {report.candidate_limit}） · <EvidenceLink href={report.source_url}>打开 USITC 查询</EvidenceLink></p>
      {!!report.candidates.length && <Table headers={['候选编码', '官方条目说明', '原始税率文本', '待复核']} rows={report.candidates.map((item) => [
        <EvidenceLink key={item.code} href={item.source_url}>{item.code}</EvidenceLink>, item.description,
        `一般：${item.general_rate_text ?? '未列'}；特别：${item.special_rate_text ?? '未列'}；第二栏：${item.column_2_rate_text ?? '未列'}`,
        [...item.pending, item.official_effective_period && `官方有效期：${item.official_effective_period}`].filter(Boolean).join(' ') || '按进口日核对适用性',
      ])} />}
      <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">{report.pending.map((text) => <li key={text}>{text}</li>)}</ul>
    </>
  }
}
