import { useState } from 'react'
import {
  amazonArchiveSampleId, amazonEvidenceSampleId, amazonPortfolioPriceRule, amazonPriceBandLabel,
  amazonListingDateLabel, amazonPriceCoverage, amazonSampleChangeLabel, amazonTime,
  amazonUnknownLaunchCadence, revealAmazonSample, type AmazonRun, type AmazonStoreProductMatrix as Matrix,
} from '@/lib/amazon-research'

export function AmazonStoreProductMatrix({ run, matrix, onOpen }: {
  run: AmazonRun; matrix: Matrix; onOpen: (id: string) => void
}) {
  const [expandedBand, setExpandedBand] = useState<number | null>(null)
  const sections = new Map(run.snapshot.evidence.map((item) => [item.key, item]))
  const lifecycle = run.snapshot.lifecycle
  const change = lifecycle?.change
  const names = new Map(run.report?.listings.map((item) => [item.asin, item.title_zh ?? item.title]))
  const section = (key: string) => {
    const item = sections.get(key)
    return <a href={'#evidence-' + key} className="text-primary hover:underline">{item?.title_zh ?? item?.title ?? key}</a>
  }
  const product = (asin: string) => {
    const id = amazonArchiveSampleId(asin)
    return <a href={'#' + id} onClick={() => revealAmazonSample(id)} className="text-primary hover:underline">{asin}{names.get(asin) && ' · ' + names.get(asin)}</a>
  }
  const structure = [
    ['商品类型（平台原文）', matrix.product_types],
    ['配色与尺码组合', matrix.specification_combinations],
    ['标题编号候选（型号待核实）', matrix.model_candidates],
  ] as const
  return (
    <section aria-label="产品组合与价格带" className="space-y-4 rounded-lg border bg-card p-4">
      <h3 className="font-medium">产品组合与价格带</h3>
      <div className="space-y-1 text-sm">
        <p>{amazonPriceCoverage(matrix)}</p>
        <p className="text-xs text-muted-foreground">{amazonPortfolioPriceRule}</p>
        <ul className="divide-y rounded-md border">
          {matrix.price_bands.map((band) => {
            const expanded = expandedBand === band.min_cents
            return (
              <li key={band.min_cents} className="px-3 py-2">
                <button type="button" disabled={!band.asins.length} aria-expanded={expanded}
                  onClick={() => setExpandedBand(expanded ? null : band.min_cents)}
                  className="flex w-full flex-wrap justify-between gap-2 text-left enabled:hover:text-primary">
                  <span>{amazonPriceBandLabel(band)}</span>
                  <span>{band.asins.length} 个 ASIN · {band.share_pct === null ? '占比未知' : band.share_pct + '%'}</span>
                </button>
                {expanded && (
                  <ul className="mt-2 max-h-56 space-y-1 overflow-auto text-xs" aria-label="价格带样本">
                    {band.asins.map((asin) => {
                      const quote = matrix.prices[asin]
                      const id = amazonEvidenceSampleId(quote.section, asin)
                      const page = sections.get(quote.section)
                      return <li key={asin}><a href={'#' + id} onClick={() => revealAmazonSample(id)} className="text-primary hover:underline">{asin} · USD {(quote.price_cents / 100).toFixed(2)} · {page?.title_zh ?? page?.title}</a></li>
                    })}
                  </ul>
                )}
              </li>
            )
          })}
        </ul>
      </div>

      <details className="rounded-md border p-3 text-sm">
        <summary className="cursor-pointer font-medium">栏目数量、报价和评分</summary>
        <p className="my-2 text-xs text-muted-foreground">按栏目 ASIN 计算评分均值和评分数中位数。评分池可能跨同款共享。</p>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[428px] text-left text-xs">
            <thead><tr><th>栏目</th><th>ASIN</th><th>有效价</th><th>评分均值</th><th>评分数中位</th></tr></thead>
            <tbody className="divide-y">
              {matrix.sections.map((row) => (
                <tr key={row.section}>
                  <td className="py-2 pr-3">{section(row.section)}</td><td>{row.asin_count ?? '未知'}</td>
                  <td>{row.priced_count}</td><td>{row.average_rating ?? '未知'}（n={row.rating_count}）</td>
                  <td>{row.median_rating_total ?? '未知'}（n={row.rating_total_count}）</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
      <details className="rounded-md border p-3 text-sm">
        <summary className="cursor-pointer font-medium">跨栏目重叠 · {matrix.overlaps.length} 对栏目</summary>
        {matrix.overlaps.length ? (
          <ul className="mt-2 max-h-48 space-y-1 overflow-auto text-xs">
            {matrix.overlaps.map((row) => <li key={row.sections.join('/')}>{section(row.sections[0])} / {section(row.sections[1])} · {row.asins.length} 个 · {row.asins.join('、')}</li>)}
          </ul>
        ) : <p className="mt-2 text-xs">已采样本中未观察到重叠。</p>}
      </details>
      {structure.map(([label, groups]) => (
        <details key={label} className="rounded-md border p-3 text-sm">
          <summary className="cursor-pointer font-medium">{label}</summary>
          {Object.keys(groups).length ? (
            <ul className="mt-2 max-h-48 space-y-1 overflow-auto text-xs">
              {Object.entries(groups).map(([key, asins]) => <li key={key}>{key} · {asins.length} 个 · {asins.join('、')}</li>)}
            </ul>
          ) : <p className="mt-2 text-xs">样本未知。</p>}
        </details>
      ))}
      <details className="rounded-md border p-3 text-sm">
        <summary className="cursor-pointer font-medium">公开评分数领先款与新品</summary>
        <ul className="my-2 space-y-1 text-xs">
          {matrix.rating_leaders.map((row) => <li key={row.asin}>{product(row.asin)} · {row.count} 个评分</li>)}
          {!matrix.rating_leaders.length && <li>公开评分数未知。</li>}
        </ul>
        <ul className="space-y-1 border-t pt-2 text-xs">
          {Object.entries(matrix.new_sections).map(([key, asins]) => (
            <li key={key}>{section(key)} · {asins.length} 个样本{asins.length > 0 && ' · ' + asins.join('、')}</li>
          ))}
          {!Object.keys(matrix.new_sections).length && <li>公开导航中未识别到新品栏目，新品样本未知。</li>}
        </ul>
      </details>
      {lifecycle && (
        <details className="rounded-md border p-3 text-sm">
          <summary className="cursor-pointer font-medium">新品节奏与产品线</summary>
          <p className="my-2 text-xs text-muted-foreground">{amazonUnknownLaunchCadence}</p>
          <ul className="space-y-1 text-xs">
            <li>配色 · {Object.entries(lifecycle.colors).map(([name, count]) => `${name} ${count} 个`).join('、') || '未知'} · 待确认 {lifecycle.missing_color} 个 ASIN</li>
            <li>尺码 · {Object.entries(lifecycle.sizes).map(([name, count]) => `${name} ${count} 个`).join('、') || '未知'} · 待确认 {lifecycle.missing_size} 个 ASIN</li>
            {lifecycle.dates.map((item) => (
              <li key={item.asin}>
                <a href={item.url} target="_blank" rel="noreferrer" className="text-primary hover:underline">{item.asin}</a>
                {' · ' + amazonListingDateLabel(item) + ' · ' + amazonTime(item.collected_at)}
              </li>
            ))}
            {!lifecycle.dates.length && <li>公开日期样本未知。</li>}
          </ul>
          {change ? (
            <div className="mt-2 space-y-1 border-t pt-2 text-xs">
              <p>{amazonTime(change.previous_collected_at)} → {amazonTime(change.current_collected_at)}</p>
              <p>{amazonSampleChangeLabel(change)}</p>
              {change.newly_seen.length > 0 && <p>新观察 ASIN · {change.newly_seen.join('、')}</p>}
              {change.no_longer_seen.length > 0 && <p>未再观察 ASIN · {change.no_longer_seen.join('、')}</p>}
              {change.series_changes.length ? (
                <ul className="space-y-1">{change.series_changes.map((item) => (
                  <li key={item.family_asin}>规格组 {item.family_asin} · 新观察 {item.newly_seen.join('、') || '无'} · 未再观察 {item.no_longer_seen.join('、') || '无'}</li>
                ))}</ul>
              ) : <p>共同可核实的系列未观察到变化。</p>}
              <button type="button" className="text-primary hover:underline" onClick={() => onOpen(change.previous_run_id)}>打开上期快照</button>
            </div>
          ) : <p className="mt-2 border-t pt-2 text-xs">当前没有同一店铺、同一栏目可比的历史采样。</p>}
        </details>
      )}
    </section>
  )
}
