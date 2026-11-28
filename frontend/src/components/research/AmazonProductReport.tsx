'use client'

import { useState, type ReactNode } from 'react'
import Image from 'next/image'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { AMAZON_BRIEF_LIMIT, amazonTime, downloadAmazonFile, revealAmazonSample, type AmazonProduct, type AmazonRun } from '@/lib/amazon-research'
import {
  productCostFields,
  productCostNumberFields,
  productDepthJson,
  productReportMarkdown,
  productSectionRequirements,
  productSectionMarkdown,
  safeProductAssetUrl,
  safeProductUrl,
  type ProductAnalysis,
  type ProductCosts,
  type ProductDepth,
  type ProductPage,
  type ProductReviewSample,
  type ProductSection,
  type ProductTemplate,
} from '@/lib/amazon-product'

const statusLabels = { complete: '完整', partial: '部分', unknown: '未知' } as const
const numericCostFields = new Set<string>(productCostNumberFields)
const integerCostFields = new Set(['benchmark_bsr', 'benchmark_monthly_units'])

function ExternalLink({ href, children, className, title }: { href: string | null | undefined; children: ReactNode; className?: string; title?: string }) {
  const safe = safeProductUrl(href)
  return safe ? <a className={className ?? 'text-primary underline'} href={safe} title={title} target="_blank" rel="noopener noreferrer">{children}</a> : <span>{children}</span>
}

function Markdown({ children }: { children: string }) {
  return <div className="prose prose-sm max-w-none break-words dark:prose-invert">
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
      a: ({ href, children, title }) => <ExternalLink href={href} title={title} className="text-primary underline">{children}</ExternalLink>,
      img: () => null,
    }}>{children}</ReactMarkdown>
  </div>
}

export function AmazonProductCostsFields({ saved }: { saved?: ProductCosts | null }) {
  const [enabled, setEnabled] = useState(!!saved)
  return <details className="space-y-3 rounded-md border p-3" open={!!saved || undefined}>
    <summary className="cursor-pointer text-sm font-medium">单品成本与对标假设（可选）</summary>
    <label className="flex items-center gap-2 text-sm">
      <input type="checkbox" name="include-product-costs" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />填写测算参数
    </label>
    {enabled && <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
      {Object.entries(productCostFields).map(([key, label]) => {
        const previous = saved?.[key as keyof ProductCosts]
        const isNumeric = numericCostFields.has(key)
        return <label className="space-y-1 text-sm" key={key}>{label}<Input
          name={'product-cost-' + key}
          type={isNumeric ? 'number' : key.endsWith('_date') ? 'date' : 'text'}
          min={isNumeric ? 0 : undefined}
          max={key.endsWith('_pct') ? 100 : undefined}
          step={integerCostFields.has(key) ? 1 : isNumeric ? 0.01 : undefined}
          defaultValue={typeof previous !== 'number' ? previous ?? '' : key.endsWith('_pct') ? previous * 100 : previous}
          maxLength={!isNumeric && !key.endsWith('_date') ? 240 : undefined}
          /></label>
      })}
    </div>}
  </details>
}

function ReviewSamples({ rows }: { rows: ProductReviewSample[] }) {
  if (!rows.length) return <p className="text-sm text-muted-foreground">没有可展示的评论样本。</p>
  return <ul className="space-y-2 text-sm">
    {rows.map((review, index) => <li className="space-y-1 border-b pb-2" key={review.id || `${review.source}-${index}`}>
      <p className="whitespace-pre-wrap">{review.text}</p>
      <p className="text-xs text-muted-foreground">
        {review.source === 'manual' ? '手动提供' : '商品页样本'} · {review.stars ?? '星级未知'} 星 · {review.date ?? '日期未知'}
        {review.variant ? ` · ${review.variant}` : ''} · {review.verified == null ? '验证状态未知' : review.verified ? '已验证购买' : '未标记验证'}
        {review.helpful == null ? '' : ` · ${review.helpful} 人认为有帮助`}
        {review.url && <> · <ExternalLink href={review.url}>来源链接</ExternalLink></>}
      </p>
    </li>)}
  </ul>
}

function ProductThumbnail({ src, title }: { src: string | null; title: string }) {
  const [failed, setFailed] = useState(false)
  return <div className="grid size-32 shrink-0 place-items-center overflow-hidden rounded-md border bg-muted">
    {src && !failed
      ? <Image src={src} alt={`${title}主图`} width={128} height={128} className="size-full bg-white object-contain" unoptimized referrerPolicy="no-referrer" onError={() => setFailed(true)} />
      : <span className="text-xs text-muted-foreground">无主图</span>}
  </div>
}

function ProductIdentity({ product, page, onStore }: { product: AmazonProduct; page?: ProductPage; onStore?: (url: string) => void }) {
  const image = safeProductAssetUrl(product.image_url, 'image') ?? page?.images.map((media) => safeProductAssetUrl(media.url, 'image')).find(Boolean) ?? null
  const store = safeProductUrl(product.store_url)
  return <div className="flex flex-wrap items-start gap-4">
    <ExternalLink className="block shrink-0 rounded-md" title={`在 Amazon 查看商品 ${product.asin}`} href={product.url}>
      <ProductThumbnail key={image} src={image} title={product.title} />
    </ExternalLink>
    <div className="min-w-0 flex-1 space-y-1 text-sm">
      <h4 className="break-words font-medium">{product.title}</h4>
      <p>{product.asin} · {product.brand ?? '品牌未知'}</p>
      <p>{page?.price_amount ?? product.price ?? '价格未知'} {page?.price_amount == null ? '' : page.currency ?? ''} · 评分 {product.rating ?? '未知'} / 5 · {product.rating_count ?? '未知'} 个评分</p>
      {page?.parent_asin && <p>父 ASIN：{page.parent_asin}</p>}
      {!!page?.variant_asins.length && <p>变体 ASIN：{page.variant_asins.join('、')}</p>}
      <div className="flex flex-wrap items-center gap-3">
        <ExternalLink href={product.url}>打开商品来源</ExternalLink>
        {store && (onStore
          ? <Button type="button" variant="outline" size="sm" onClick={() => onStore(store)}>调研品牌店铺</Button>
          : <ExternalLink href={store}>查看品牌店铺</ExternalLink>)}
      </div>
    </div>
  </div>
}

function ProductPageOverview({ page, label, onStore }: { page: ProductPage; label: string; onStore?: (url: string) => void }) {
  const screenshot = page.screenshot_data_url && /^data:image\/(?:png|jpeg|webp|gif);base64,[a-z\d+/=]+$/i.test(page.screenshot_data_url)
    ? page.screenshot_data_url : null
  return <div className="space-y-3">
    <ProductIdentity product={page.product} page={page} onStore={onStore} />
    <p className="text-sm text-muted-foreground">采集时间：<time dateTime={page.collected_at}>{amazonTime(page.collected_at)}</time></p>
    {screenshot
      ? <div className="relative h-80 w-full max-w-xl rounded border"><Image src={screenshot} alt={`${label}页面截图`} fill unoptimized sizes="(max-width: 640px) 100vw, 576px" className="object-contain" /></div>
      : <p className="text-sm text-muted-foreground">未提供页面截图；此处仅展示已采集的商品资料。</p>}
    {Object.keys(page.specs).length > 0 && <dl className="grid gap-2 sm:grid-cols-2">
      {Object.entries(page.specs).map(([key, value]) => <div key={key} className="rounded border p-2 text-sm"><dt className="font-medium">{key}</dt><dd>{value}</dd></div>)}
    </dl>}
    {Object.keys(page.field_evidence).length > 0 && <details className="rounded border p-3 text-sm">
      <summary className="cursor-pointer font-medium">字段来源</summary>
      <ul className="mt-2 space-y-1">{Object.entries(page.field_evidence).map(([field, url]) => <li key={field}>{field} · <ExternalLink href={url}>证据来源</ExternalLink></li>)}</ul>
    </details>}
    {page.important.length > 0 && <div><h5 className="mb-1 text-sm font-medium">重要卖点</h5><ul className="list-disc space-y-1 pl-5 text-sm">{page.important.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul></div>}
    {page.aplus.length > 0 && <details className="rounded border p-3 text-sm"><summary className="cursor-pointer font-medium">A+ 内容</summary><ul className="mt-2 list-disc space-y-1 pl-5">{page.aplus.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul></details>}
    {Object.keys(page.rating_distribution).length > 0 && <p className="text-sm">评分分布：{Object.entries(page.rating_distribution).sort(([a], [b]) => Number(b) - Number(a)).map(([stars, count]) => `${stars} 星 ${count}`).join(' · ')}</p>}
    {(page.images.length > 0 || page.videos.length > 0) && <div className="space-y-2">
      <h5 className="font-medium">商品图片与视频</h5>
      {page.images.length > 0 && <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {page.images.map((media, index) => {
          const image = safeProductAssetUrl(media.url, 'image')
          return <li className="min-w-0 rounded border p-2" key={`${media.url}-${index}`}>
            <ExternalLink className="block" href={image}>
              {image && <span className="relative block aspect-square w-full"><Image src={image} alt="" fill sizes="(max-width: 640px) 50vw, (max-width: 1024px) 33vw, 25vw" className="rounded object-contain" unoptimized referrerPolicy="no-referrer" /></span>}
              <span className="mt-1 block truncate text-sm">{media.label || `图片 ${index + 1}`}</span>
            </ExternalLink>
            <p className="text-xs text-muted-foreground">{media.width ?? '未知'} × {media.height ?? '未知'}</p>
          </li>
        })}
      </ul>}
      {page.videos.length > 0 && <ul className="grid gap-2 sm:grid-cols-2">{page.videos.map((media, index) => {
        const poster = safeProductAssetUrl(media.poster, 'image')
        return <li className="flex items-center gap-3 rounded border p-2" key={`${media.url}-${index}`}>
          {poster && <Image src={poster} alt="" width={96} height={64} className="h-16 w-24 rounded object-cover" unoptimized referrerPolicy="no-referrer" />}
          <div className="min-w-0"><ExternalLink href={safeProductAssetUrl(media.url, 'video')}>{media.label || `视频 ${index + 1}`}</ExternalLink><p className="text-xs text-muted-foreground">{media.width ?? '未知'} × {media.height ?? '未知'}</p></div>
        </li>
      })}</ul>}
    </div>}
    <details className="rounded border p-3"><summary className="cursor-pointer text-sm font-medium">原始评论样本 · {page.reviews.length}</summary><div className="mt-3"><ReviewSamples rows={page.reviews} /></div></details>
  </div>
}

function ProductSectionDetails({ section, download, children }: { section: ProductSection; download: (contents: string, filename: string) => void; children?: ReactNode }) {
  return <details id={`product-${section.req}`} className="scroll-mt-6 rounded-lg border p-3">
    <summary className="cursor-pointer list-inside font-medium">
      {section.title} <span className="ml-2 text-xs text-muted-foreground">{section.req} · {statusLabels[section.status]}</span>
    </summary>
    <div className="mt-3 space-y-3">
      <div className="flex justify-end"><Button type="button" variant="outline" size="sm" onClick={() => download(productSectionMarkdown(section), `${section.req.toLowerCase()}.md`)}>下载本专题 Markdown</Button></div>
      {section.summary.length > 0 ? <div className="space-y-2">{section.summary.map((line, index) => <Markdown key={`${index}-${line}`}>{line}</Markdown>)}</div> : <p className="text-sm text-muted-foreground">专题摘要未知。</p>}
      {section.tables.map((table, index) => <div className="overflow-x-auto rounded border" key={`${table.title}-${index}`}>
        <table className="w-full text-left text-sm"><caption className="p-2 text-left font-medium">{table.title}</caption>
          <thead className="bg-muted"><tr>{table.columns.map((column, at) => <th scope="col" className="p-2" key={`${column}-${at}`}>{column}</th>)}</tr></thead>
          <tbody>{table.rows.map((row, rowIndex) => <tr className="border-t" key={rowIndex}>{table.columns.map((_, columnIndex) => <td className="min-w-24 p-2 align-top" key={columnIndex}>{row[columnIndex] ?? '未知'}</td>)}</tr>)}</tbody>
        </table>
      </div>)}
      {section.sources.length > 0 && <details className="rounded border p-3 text-sm"><summary className="cursor-pointer font-medium">引用来源 · {section.sources.length}</summary>
        <ul className="mt-2 space-y-1">{section.sources.map((source, index) => <li key={`${source.url}-${index}`}><ExternalLink href={source.url}>{source.label || '来源'}</ExternalLink>{source.asin ? ` · ${source.asin}` : ''}{source.collected_at ? ` · ${source.collected_at}` : ''}</li>)}</ul>
      </details>}
      {children}
    </div>
  </details>
}

function FinanceOverview({ analysis }: { analysis: ProductAnalysis }) {
  const finance = analysis.finance
  return <div className="space-y-3 text-sm">
    <p>售价 {finance.price ?? '未知'} {finance.currency ?? ''} · 单件利润 {finance.unit_profit ?? '未知'} {finance.currency ?? ''} · ROI {finance.roi_pct == null ? '未知' : `${finance.roi_pct}%`}</p>
    <p>类目 {finance.category ?? '未知'} · 排名 {finance.rank ?? '未知'} · 费用来源 {finance.fee_source ?? '未知'} · 日期 {finance.fee_date ?? '未知'}</p>
    {finance.assumptions.length > 0 && <div><h4 className="font-medium">假设</h4><ul className="list-disc pl-5">{finance.assumptions.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul></div>}
    {finance.missing.length > 0 && <div><h4 className="font-medium">待补信息</h4><ul className="list-disc pl-5">{finance.missing.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul></div>}
    {Object.keys(finance.unit_costs).length > 0 && <p>单件成本：{Object.entries(finance.unit_costs).map(([key, value]) => `${key} ${value ?? '未知'}`).join(' · ')}</p>}
    {finance.scenarios.length > 0 && <div className="overflow-x-auto rounded border"><table className="w-full text-left">
      <caption className="p-2 text-left font-medium">销量情景</caption><thead className="bg-muted"><tr>{['情景', '月销量', '年收入', '年利润'].map((label) => <th className="p-2" key={label}>{label}</th>)}</tr></thead>
      <tbody>{finance.scenarios.map((scenario, index) => <tr className="border-t" key={`${scenario.name}-${index}`}><td className="p-2">{scenario.name}</td><td className="p-2">{scenario.monthly_units ?? '未知'}</td><td className="p-2">{scenario.annual_revenue ?? '未知'} {finance.currency ?? ''}</td><td className="p-2">{scenario.annual_profit ?? '未知'} {finance.currency ?? ''}</td></tr>)}</tbody>
    </table></div>}
  </div>
}

type ProductReportProps = { run: AmazonRun; onBrief: (text: string) => void; onStore?: (url: string) => void }

export function AmazonProductReport(props: ProductReportProps) {
  return <ProductReport key={props.run.id} {...props} />
}

function ProductReport({ run, onBrief, onStore }: ProductReportProps) {
  const depth: ProductDepth | null = run.snapshot.product_depth ?? null
  const analysis = depth?.analysis ?? null
  const sectionsByReq = new Map((analysis?.sections ?? []).map((section) => [section.req, section]))
  const [selectedShots, setSelectedShots] = useState<Set<string>>(() => new Set())
  const [shotCopy, setShotCopy] = useState('')
  const [editedTemplate, setEditedTemplate] = useState<ProductTemplate | null>(null)
  const reportProduct = run.report?.product

  const file = (contents: string, suffix: string, type = 'text/markdown') => downloadAmazonFile(contents, `${run.id}-${suffix}`, type)
  const shots = analysis?.image_plan ?? []
  const template = editedTemplate ?? analysis?.listing ?? null
  function updateTemplate<K extends keyof ProductTemplate>(key: K, value: ProductTemplate[K]) {
    setEditedTemplate((current) => {
      const base = current ?? analysis?.listing
      return base ? { ...base, [key]: value } : null
    })
  }
  const toggleShot = (shotId: string) => {
    const next = new Set(selectedShots)
    if (next.has(shotId)) next.delete(shotId)
    else next.add(shotId)
    setSelectedShots(next)
    setShotCopy(shots.filter((shot) => next.has(shot.id)).map((shot) => `${shot.title}\n${shot.brief}`).join('\n\n'))
  }

  const shotEditor = analysis && <section className="space-y-3 border-t pt-4" aria-label="图片视频方案">
    <h4 className="font-medium">图片与视频方案</h4>
    {shots.length ? <ul className="space-y-2">{shots.map((shot) => <li className="rounded border p-3" key={shot.id}>
      <label className="flex cursor-pointer items-start gap-2 text-sm"><input type="checkbox" checked={selectedShots.has(shot.id)} onChange={() => toggleShot(shot.id)} />
        <span><strong>{shot.type === 'image' ? '图片' : '视频'} · {shot.title}</strong><span className="mt-1 block">{shot.brief}</span>
          {shot.evidence.length > 0 && <span className="mt-1 block text-xs text-muted-foreground">依据：{shot.evidence.join('；')}</span>}</span>
      </label>
    </li>)}</ul> : <p className="text-sm text-muted-foreground">尚无图片或视频方案。</p>}
    <label className="block space-y-1 text-sm"><span>已选方案文案（可编辑后带入图片创作）</span><Textarea className="min-h-28" maxLength={AMAZON_BRIEF_LIMIT} value={shotCopy} onChange={(event) => setShotCopy(event.target.value)} /></label>
    {shotCopy.length > AMAZON_BRIEF_LIMIT && <p className="text-sm text-muted-foreground">请将方案文案缩短至 {AMAZON_BRIEF_LIMIT} 字符以内。</p>}
    <Button type="button" disabled={!shotCopy.trim() || shotCopy.length > AMAZON_BRIEF_LIMIT} onClick={() => onBrief(shotCopy.trim())}>带入图片创作</Button>
  </section>

  const listingEditor = template && <section className="space-y-3 border-t pt-4" aria-label="Listing 文案模板">
    <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="font-medium">Listing 文案模板（可编辑）</h4>
      <Button type="button" variant="outline" size="sm" onClick={() => file(JSON.stringify(template, null, 2) + '\n', 'product-listing-template.json', 'application/json')}>下载当前模板</Button></div>
    <div className="grid gap-3">
      <label className="space-y-1 text-sm">标题<Input maxLength={500} value={template.title} onChange={(event) => updateTemplate('title', event.target.value)} /></label>
      <label className="space-y-1 text-sm">五点描述（每行一条）<Textarea className="min-h-28" value={template.bullets.join('\n')} onChange={(event) => updateTemplate('bullets', event.target.value.split('\n'))} /></label>
      <label className="space-y-1 text-sm">商品描述<Textarea className="min-h-28" value={template.description} onChange={(event) => updateTemplate('description', event.target.value)} /></label>
      <label className="space-y-1 text-sm">A+ 内容（每行一条）<Textarea className="min-h-24" value={template.aplus.join('\n')} onChange={(event) => updateTemplate('aplus', event.target.value.split('\n'))} /></label>
      <label className="space-y-1 text-sm">后台关键词<Textarea className="min-h-20" value={template.backend_keywords} onChange={(event) => updateTemplate('backend_keywords', event.target.value)} /></label>
      <label className="space-y-1 text-sm">注意事项（每行一条）<Textarea className="min-h-20" value={template.conditions.join('\n')} onChange={(event) => updateTemplate('conditions', event.target.value.split('\n'))} /></label>
    </div>
  </section>

  if (!depth) return <article className="space-y-3 rounded-lg border bg-card p-4" aria-label="单品深调研报告">
    <h2 className="font-semibold">单品深度调研</h2><p className="text-sm text-muted-foreground">当前任务没有单品深调研快照。</p>
    {reportProduct && <ProductIdentity product={reportProduct} onStore={onStore} />}
    <Button type="button" variant="outline" size="sm" onClick={() => file(JSON.stringify({ task_id: run.id, product_depth: null }, null, 2) + '\n', 'product-report.json', 'application/json')}>下载 JSON</Button>
  </article>

  return <article className="space-y-4 rounded-lg border bg-card p-4" aria-label="单品深调研报告">
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div><h2 className="font-semibold">单品深度调研 · {depth.overview?.product.title ?? run.source}</h2>
        <p className="text-sm text-muted-foreground">{depth.overview ? `${depth.overview.product.asin} · 采集 ${amazonTime(depth.overview.collected_at)}` : '商品详情暂无可验证样本'} · 专题 ${analysis?.sections.length ?? 0}/9 · 搜索 ${depth.searches.length} · 竞品 ${depth.competitors.length}</p></div>
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" size="sm" onClick={() => file(productReportMarkdown(run, depth), 'product-report.md')}>下载完整报告</Button>
        <Button type="button" variant="outline" size="sm" onClick={() => file(productDepthJson(run, depth), 'product-report.json', 'application/json')}>下载原始 JSON</Button>
      </div>
    </header>

    {analysis && <section className="space-y-2 rounded-md border p-3">
      <h3 className="font-medium">研究结论</h3><Markdown>{analysis.decision || '暂无结论。'}</Markdown>
    </section>}

    <section id="product-overview" className="scroll-mt-6 space-y-3 border-t pt-4">
      <h3 className="font-medium">商品页来源概览</h3>
      {depth.overview ? <ProductPageOverview page={depth.overview} label="商品页" onStore={onStore} /> : <>
        {reportProduct && <ProductIdentity product={reportProduct} onStore={onStore} />}
        <p className="text-sm text-muted-foreground">没有可展示的商品页字段或截图证据。</p>
      </>}
    </section>

    <section className="space-y-3 border-t pt-4" aria-label="专题分析">
      <h3 className="font-medium">九项专题</h3>
      <nav aria-label="单品专题导航" className="flex flex-wrap gap-x-4 gap-y-2 text-sm">
        {productSectionRequirements.map((req) => {
          const section = sectionsByReq.get(req)
          return <a className="text-primary underline" href={`#product-${req}`} key={req}
            onClick={() => revealAmazonSample(`product-${req}`)}>
            {section?.title ?? req}
          </a>
        })}
      </nav>
      <div className="space-y-2">{productSectionRequirements.map((req) => {
        const section: ProductSection = sectionsByReq.get(req) ?? { req, title: req, status: 'unknown', summary: ['本专题未返回分析内容，证据未知。'], tables: [], sources: [] }
        return <ProductSectionDetails key={req} section={section} download={file}>
          {req === 'REQ-027' && shotEditor}
          {req === 'REQ-028' && listingEditor}
        </ProductSectionDetails>
      })}</div>
    </section>

    <section className="space-y-3 border-t pt-4" aria-label="原始搜索采样">
      <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-medium">搜索样本与竞品候选 · {depth.searches.length}</h3>
        <Button type="button" size="sm" variant="outline" onClick={() => file(JSON.stringify({ searches: depth.searches, candidates: depth.candidates }, null, 2) + '\n', 'product-search-samples.json', 'application/json')}>下载采样 JSON</Button></div>
      {depth.searches.map((search, index) => <details className="rounded border p-3" key={`${search.query}-${index}`}>
        <summary className="cursor-pointer text-sm font-medium">{search.query} · {search.rows.length} 条 · {search.result_label ?? `结果 ${search.result_min ?? '未知'}–${search.result_max ?? '未知'}`}</summary>
        <div className="mt-2 flex flex-wrap items-center gap-3 text-sm"><span>采集 {search.collected_at}</span><ExternalLink href={search.url}>搜索来源</ExternalLink>
          <Button type="button" size="sm" variant="outline" onClick={() => file(JSON.stringify(search, null, 2) + '\n', `product-search-${index + 1}.json`, 'application/json')}>下载本次采样</Button></div>
        {search.rows.length > 0 && <div className="mt-3 overflow-x-auto rounded border"><table className="w-full text-left text-sm">
          <thead className="bg-muted"><tr>{['排名', 'ASIN', '商品', '品牌', '价格', '广告'].map((heading) => <th className="p-2" key={heading}>{heading}</th>)}</tr></thead>
          <tbody>{search.rows.map((row, rowIndex) => <tr className="border-t" key={`${row.asin}-${rowIndex}`}><td className="p-2">{row.rank ?? '未知'}</td><td className="p-2">{row.asin}</td><td className="min-w-48 p-2"><ExternalLink href={row.url}>{row.title}</ExternalLink></td><td className="p-2">{row.brand ?? '未知'}</td><td className="p-2">{row.price_amount ?? '未知'} {row.currency ?? ''}</td><td className="p-2">{row.sponsored == null ? '未知' : row.sponsored ? '是' : '否'}</td></tr>)}</tbody>
        </table></div>}
      </details>)}
      <h4 className="font-medium">候选商品 · {depth.candidates.length}</h4>
      {depth.candidates.length > 0 ? <ul className="space-y-2">{depth.candidates.map((candidate, index) => <li key={`${candidate.asin}-${index}`}>
        <details className="rounded border p-3 text-sm"><summary className="cursor-pointer"><ExternalLink href={candidate.url}>{candidate.title}</ExternalLink> · {candidate.asin}</summary>
          <p className="mt-2">匹配原因：{candidate.reasons.length ? candidate.reasons.join('；') : '未知'}</p><p>关联查询：{candidate.queries.length ? candidate.queries.join('、') : '未知'}</p>
        </details>
      </li>)}</ul> : <p className="text-sm text-muted-foreground">没有候选商品样本。</p>}
    </section>

    <section className="space-y-3 border-t pt-4" aria-label="评论采样">
      <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-medium">评论原始样本与采样分组 · {depth.review_pages.length}</h3>
        <Button type="button" size="sm" variant="outline" onClick={() => file(JSON.stringify(depth.review_pages, null, 2) + '\n', 'product-review-samples.json', 'application/json')}>下载评论 JSON</Button></div>
      {depth.review_pages.map((page, index) => <details className="rounded border p-3" key={`${page.bucket}-${page.url}-${index}`}>
        <summary className="cursor-pointer text-sm font-medium">{page.bucket} · {page.rows.length} 条 · {page.limited ? '采样受限' : '采样状态正常'}</summary>
        <div className="my-2 flex flex-wrap gap-3 text-sm"><span>采集 {page.collected_at}</span><ExternalLink href={page.url}>采样来源</ExternalLink>
          <Button type="button" size="sm" variant="outline" onClick={() => file(JSON.stringify(page, null, 2) + '\n', `product-reviews-${page.bucket}-${index + 1}.json`, 'application/json')}>下载本分组</Button></div>
        <ReviewSamples rows={page.rows} />
      </details>)}
      {depth.review_pages.length === 0 && <p className="text-sm text-muted-foreground">没有评论采样页。</p>}
    </section>

    <section className="space-y-3 border-t pt-4" aria-label="竞品详情">
      <h3 className="font-medium">竞品详情 · {depth.competitors.length}</h3>
      {depth.competitors.map((page, index) => <details className="rounded border p-3" key={`${page.product.asin}-${index}`}>
        <summary className="cursor-pointer font-medium">{page.product.title} · {page.product.asin}</summary>
        <div className="mt-3"><ProductPageOverview page={page} label="竞品" onStore={onStore} /></div>
        <div className="mt-3"><Button type="button" size="sm" variant="outline" onClick={() => file(JSON.stringify(page, null, 2) + '\n', `product-competitor-${page.product.asin}.json`, 'application/json')}>下载本竞品 JSON</Button></div>
      </details>)}
      {depth.competitors.length === 0 && <p className="text-sm text-muted-foreground">没有可展示的竞品详情样本。</p>}
    </section>

    {analysis && <section className="space-y-3 border-t pt-4" aria-label="财务测算">
      <h3 className="font-medium">单件成本、费用与销量情景</h3><FinanceOverview analysis={analysis} />
    </section>}
  </article>
}
