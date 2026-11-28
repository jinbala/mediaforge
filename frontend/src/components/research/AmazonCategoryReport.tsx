import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { categoryMarkdown, categorySamples, costFields } from '@/lib/amazon-category'
import { amazonEvidenceSampleId, amazonTime, downloadAmazonFile, revealAmazonSample, type AmazonRun } from '@/lib/amazon-research'

export function AmazonCategoryFields({ saved }: { saved?: AmazonRun['inputs']['category'] }) {
  const [assumptions, setAssumptions] = useState(!!saved?.costs)
  const cost = saved?.costs
  return <details className="space-y-3 rounded-md border p-3" open={!!saved || undefined}>
    <summary className="cursor-pointer text-sm font-medium">子类目与可替换成本（可选）</summary>
    <label className="block space-y-1 text-sm"><span>指定子类目：美国站 Best Sellers 链接，每行一个，最多 5 个</span>
      <Textarea name="subcategory-links" maxLength={10004} defaultValue={saved?.subcategories.join('\n')} />
    </label>
    <label className="flex items-center gap-2 text-sm"><input type="checkbox" name="include-category-costs" checked={assumptions} onChange={(event) => setAssumptions(event.target.checked)} />填写利润测算假设</label>
    {assumptions && <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
      {Object.entries(costFields).map(([key, label]) => {
        const rate = key.endsWith('_rate')
        const units = key.endsWith('_units')
        const previous = cost?.[key as keyof typeof costFields]
        return <label className="space-y-1 text-sm" key={key}>{label}<Input name={'category-cost-' + key} type="number"
          min={units ? 1 : 0} max={rate ? 100 : units ? 100000 : 100000000} step={units ? 1 : 0.01}
          defaultValue={rate && previous != null ? Number(previous) * 100 : previous} required /></label>
      })}
      <label className="space-y-1 text-sm">目标 ASIN（可选）<Input name="category-target-asin" maxLength={10} pattern="[A-Za-z0-9]{10}" defaultValue={cost?.target_asin ?? ''} /></label>
      <label className="space-y-1 text-sm">目标售价 USD（可选）<Input name="category-target-price" type="number" min="0.01" max="100000000" step="0.01" defaultValue={cost?.target_price_usd ?? ''} /></label>
      <label className="space-y-1 text-sm">假设来源说明<Input name="category-cost-source" maxLength={240} defaultValue={cost?.source_note} required /></label>
      <label className="space-y-1 text-sm">有效日期<Input name="category-cost-date" type="date" defaultValue={cost?.effective_on} required /></label>
      <label className="space-y-1 text-sm">假设版本<Input name="category-cost-version" maxLength={240} defaultValue={cost?.version} required /></label>
      <label className="space-y-1 text-sm">已核实供应商报价（可选）<Input name="category-cost-verified" maxLength={240} defaultValue={cost?.verified_quote_note ?? ''} /></label>
    </div>}
  </details>
}

export function AmazonCategoryReport({ run, busy, onProduct, onStore }: {
  run: AmazonRun
  busy: boolean
  onProduct: (url: string, asin: string, direction: string) => void
  onStore: (asin: string, direction: string) => void
}) {
  const boards = run.snapshot.evidence.filter((item) => item.kind === 'board')
  const analysis = run.snapshot.category_analysis
  const products = new Map(run.report?.listings.map((item) => [item.asin, item]))
  const actions = (asin: string, direction: string) => <div className="flex flex-wrap gap-1">
    <Button type="button" variant="ghost" size="sm" disabled={busy || !products.has(asin)} onClick={() => {
      const product = products.get(asin)
      if (product) onProduct(product.url, asin, direction)
    }}>调研单品</Button>
    <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => onStore(asin, direction)}>调研品牌店铺</Button>
  </div>
  const file = (contents: string, suffix: string, type?: string) => downloadAmazonFile(contents, `${run.id}-${suffix}`, type)
  return <article className="space-y-4 rounded-lg border bg-card p-4" aria-label="类目深调研报告">
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div><h2 className="font-semibold">类目深调研 · {run.report?.title_zh ?? run.report?.title ?? '等待样本'}</h2>
        <p className="text-sm text-muted-foreground">主榜与子榜 {boards.length} 个 · 去重 {Object.keys(run.snapshot.asin_sources).length} 件 · {analysis ? `分析 ${analysis.status === 'complete' ? '可评分' : '待补证据'} · ${analysis.version}` : '等待分析'}</p></div>
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" size="sm" disabled={!analysis} onClick={() => file(categoryMarkdown(run), 'category-report.md')}>下载完整报告</Button>
        <Button type="button" variant="outline" size="sm" disabled={!boards.length} onClick={() => file(categorySamples(run), 'boards.json', 'application/json')}>下载原始证据</Button>
      </div>
    </header>
    {analysis && <nav aria-label="类目专题" className="flex flex-wrap gap-3 text-sm">{analysis.sections.map((section) => <a className="text-primary underline" href={'#category-' + section.key} key={section.key}>{section.title}</a>)}</nav>}
    {analysis?.sections.map((section) => <section id={'category-' + section.key} className="scroll-mt-6 space-y-3 border-t pt-4" key={section.key}>
      <div className="flex flex-wrap justify-between gap-2"><div><h3 className="font-medium">{section.title}</h3><span className="text-xs text-muted-foreground">{section.reqs.join(' · ')}</span></div>
        <Button type="button" variant="outline" size="sm" onClick={() => file(categoryMarkdown(run, section), section.key + '.md')}>下载本专题</Button></div>
      <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">{section.notes.map((note) => <li key={note}>{note}</li>)}</ul>
      {section.tables.map((table) => <div className="overflow-x-auto rounded border" key={table.title}><table className="w-full text-left text-sm">
        <caption className="p-2 text-left font-medium">{table.title}</caption>
        <thead className="bg-muted"><tr>{[...table.columns, '榜单证据'].map((column) => <th scope="col" className="p-2" key={column}>{column}</th>)}</tr></thead>
        <tbody>{table.rows.map((row, index) => <tr className="border-t" key={index}>{row.cells.map((cell, at) => <td key={at} className="min-w-20 p-2 align-top">{cell ?? '未知'}</td>)}
          <td className="min-w-32 p-2"><details><summary>{row.asins.length} 个 ASIN</summary>{row.asins.map((asin) => <div key={asin}>{(run.snapshot.asin_sources[asin] ?? []).map((key) => {
            const id = amazonEvidenceSampleId(key, asin)
            return <a key={key} href={'#' + id} onClick={() => revealAmazonSample(id)} className="block text-primary">{asin} · {boards.find((board) => board.key === key)?.title_zh ?? boards.find((board) => board.key === key)?.title ?? '榜单'}</a>
          })}{section.key === 'opportunities' && actions(asin, `${section.title} · ${row.cells[0] ?? ''}`)}</div>)}</details></td></tr>)}</tbody>
      </table></div>)}
    </section>)}
    <section className="space-y-3 border-t pt-3" aria-label="逐榜样本">
      <h3 className="font-medium">逐榜原始样本</h3>
      {boards.map((board) => <details key={board.key} className="space-y-2 rounded border p-3 text-sm">
        <summary className="cursor-pointer font-medium">{board.title_zh ?? board.title} · {board.products.length} 件</summary>
        <div className="flex flex-wrap items-center gap-2"><a className="text-primary" href={board.final_url ?? board.url} target="_blank" rel="noreferrer">榜单来源</a>
          <span>{board.collected_at ? amazonTime(board.collected_at) : '等待采集'}</span><Button type="button" size="sm" variant="outline" onClick={() => file(categorySamples(run, board.key), board.key + '.json', 'application/json')}>下载本榜单</Button></div>
        <ul className="space-y-2">{board.products.map((product) => <li key={product.asin} className="flex items-center gap-3 break-words border-b pb-2">
          {product.image_url && <a className="text-primary" href={product.image_url} target="_blank" rel="noreferrer">主图</a>}
          <div className="flex-1"><a className="text-primary" href={product.url} target="_blank" rel="noreferrer">{product.rank ?? '排名未知'} · {product.title_zh ?? product.title}</a>
            <p>{product.asin} · {product.price ?? '缺价'} {product.price_currency ?? ''} · {product.rating ?? '缺评分'} / {product.rating_count ?? '缺评分数'}</p></div>
          {actions(product.asin, board.title_zh ?? board.title)}
        </li>)}</ul>
      </details>)}
    </section>
  </article>
}
