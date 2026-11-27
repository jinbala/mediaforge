import { ArrowDownToLine, ExternalLink, RotateCcw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  activeAmazonRun, amazonArchiveSampleId, amazonCollectionRange, amazonEvidenceSampleId,
  amazonRunLabels, amazonStepLabels, amazonTime, downloadAmazonFile, formatAmazonStoreSamples,
  revealAmazonSample,
  type AmazonEvidence, type AmazonRetryTarget, type AmazonRun,
} from '@/lib/amazon-research'

const stageLabels: Record<string, string> = {
  queue: '派发任务', section: '采集店铺栏目', translation: '补充中文字段', report: '生成结果',
}
const roleLabels: Record<string, string> = {
  home: '首页', new: '新品', popular: '畅销', about: '关于我们', collection: '合集', category: '栏目',
}
const retryable = (item: AmazonEvidence) => (
  ['pending', 'failed', 'incomplete'].includes(item.status)
  && !['sample_limit', 'duplicate', 'store_mismatch'].includes(item.code ?? '')
)

export function AmazonRunDetails({ run, busy, pollError, onRetry }: {
  run: AmazonRun
  busy: boolean
  pollError: string
  onRetry: (target: AmazonRetryTarget, key?: string) => void
}) {
  const snapshot = run.snapshot
  const range = amazonCollectionRange(run)
  const active = activeAmazonRun(run)
  const pending = range.section_status_counts.pending
  const canRetry = !active && run.attempt < 6
  return (
    <section className="space-y-3 rounded-lg border bg-card p-4" aria-label="调研任务进度">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-medium">店铺栏目采集</h3>
        <span role="status" className="text-sm">
          {amazonRunLabels[run.status]} · {stageLabels[run.stage] ?? '恢复采集'}
          {range.section_count > 0 && ' · ' + (range.section_count - pending) + '/' + range.section_count + ' 个栏目'}
        </span>
      </div>
      <p className="break-all text-xs text-muted-foreground">
        Amazon 美国站 · 任务 {run.id} · 第 {run.attempt} 次处理 · {amazonTime(run.updated_at)}
      </p>
      {(pollError || run.error) && <p role="alert" className="text-sm text-destructive">{pollError || run.error}</p>}

      <div className="flex flex-wrap items-center gap-2">
        {snapshot.store_root && (
          <a href={snapshot.store_root} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-sm text-primary">
            店铺首页 <ExternalLink className="size-3" aria-hidden="true" />
          </a>
        )}
        {snapshot.evidence.length > 0 && (
          <Button variant="outline" size="sm" onClick={() => downloadAmazonFile(formatAmazonStoreSamples(run), run.id + '-samples.json', 'application/json')}>
            <ArrowDownToLine aria-hidden="true" />下载栏目采集记录
          </Button>
        )}
        {canRetry && ['queue_failed', 'interrupted'].includes(run.status) && (
          <Button variant="outline" size="sm" onClick={() => onRetry('resume')} disabled={busy}>
            <RotateCcw aria-hidden="true" />从已保存进度恢复
          </Button>
        )}
      </div>

      {range.section_count > 0 && (
        <p className="text-xs text-muted-foreground">
          本次 {range.raw_card_count} 条栏目卡片 · {range.unique_asin_count} 个去重 ASIN
        </p>
      )}
      {snapshot.translation_missing.length > 0 && (
        <p className="text-xs text-muted-foreground">未取得译文的原文字段共 {snapshot.translation_missing.length} 个。</p>
      )}
      {snapshot.notes.map((note) => <p className="text-xs text-muted-foreground" key={note}>{note}</p>)}
      {snapshot.evidence.length > 0 && (
        <ul className="divide-y rounded-md border">
          {snapshot.evidence.map((item) => (
            <li key={item.key} id={'evidence-' + item.key} className="scroll-mt-5 p-3 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <a href={item.final_url ?? item.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-medium text-primary">
                  {item.title_zh ?? item.title} <ExternalLink className="size-3 shrink-0" aria-hidden="true" />
                </a>
                {canRetry && !['queue_failed', 'interrupted'].includes(run.status) && retryable(item) && (
                  <Button variant="ghost" size="sm" disabled={busy} onClick={() => onRetry('failed', item.key)}>
                    <RotateCcw aria-hidden="true" />重试此项
                  </Button>
                )}
              </div>
              {item.title_zh && <p className="text-xs text-muted-foreground" lang="en">{item.title}</p>}
              <p className="mt-1 text-xs text-muted-foreground">
                {(item.role && roleLabels[item.role]) || (item.kind === 'section' ? '栏目' : '搜索联想')}
                {' · ' + amazonStepLabels[item.status] + ' · ' + item.method}
                {item.observed_count !== null && ' · ' + item.observed_count + ' 个样本'}
                {item.collected_at && ' · ' + amazonTime(item.collected_at)}
                {item.attempts > 0 && ' · ' + item.attempts + ' 次读取'}
              </p>
              {item.reason && <p className="mt-1 text-xs text-muted-foreground">{item.reason}</p>}
              {item.excerpt && <p className="mt-1 text-xs text-muted-foreground" lang="en">{item.excerpt}</p>}
              {item.products.length > 0 && (
                <details className="mt-2 text-xs">
                  <summary className="cursor-pointer">查看本页商品证据</summary>
                  <ul className="mt-1 list-disc space-y-1 pl-5">
                    {item.products.map((product) => {
                      const sampleId = amazonEvidenceSampleId(item.key, product.asin)
                      const archiveId = amazonArchiveSampleId(product.asin)
                      const color = product.color ?? product.store_variant?.color
                      const size = product.size ?? product.store_variant?.size
                      return (
                        <li key={product.asin} id={sampleId} tabIndex={-1} className="scroll-mt-6 rounded p-1 target:bg-muted">
                          <a href={product.url} target="_blank" rel="noreferrer" className="text-primary">{product.asin} · {product.title_zh ?? product.title}</a>
                          {' · 价 ' + (product.price ?? '未知') + (product.price_currency ? ' ' + product.price_currency : '')}
                          {product.price_label && '（' + product.price_label + '）'}
                          {' · 评分 ' + (product.rating ?? '未知') + ' · 评分数 ' + (product.rating_count ?? '未知')}
                          {(color || size) && ' · ' + [color, size].filter(Boolean).join(' / ')}
                          {product.image_url && <a href={product.image_url} target="_blank" rel="noreferrer" className="ml-2 text-primary">主图</a>}
                          {snapshot.asin_sources[product.asin] && (
                            <a href={'#' + archiveId} onClick={() => revealAmazonSample(archiveId)} className="ml-2 text-primary">返回归档 {product.asin}</a>
                          )}
                        </li>
                      )
                    })}
                  </ul>
                </details>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
