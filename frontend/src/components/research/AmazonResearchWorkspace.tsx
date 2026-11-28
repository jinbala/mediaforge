'use client'

import { useCallback, useEffect, useId, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Button } from '@/components/ui/button'
import { safeProductUrl } from '@/lib/amazon-product'
import { activeAmazonRun, amazonTime, downloadAmazonFile, type AmazonRun } from '@/lib/amazon-research'
import {
  amazonComparisonValue, amazonWorkspaceStatus, getAmazonResearchComparison, getAmazonResearchEvidence,
  getAmazonResearchReport, getAmazonResearchReports, type AmazonResearchComparison, type AmazonResearchContext,
} from '@/lib/amazon-research-common'

type WorkspaceProps = {
  run: AmazonRun
  onOpen: (id: string) => void
  onRetryTranslation: () => void
  busy: boolean
}

type ResearchInputs = AmazonRun['inputs'] & { context?: AmazonResearchContext | null }
type ResearchSnapshot = AmazonRun['snapshot'] & { field_translations?: Record<string, string> | null }
type RequestResult<T> = { data: T; error?: never } | { data?: never; error: string }

function errorText(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? `${fallback}：${error.message}` : fallback
}

function useWorkspaceRequest<T>(load: (id: string, signal: AbortSignal) => Promise<T>, id: string, enabled = true) {
  const [attempt, setAttempt] = useState(0)
  const [result, setResult] = useState<{ attempt: number; value: RequestResult<T> }>()

  useEffect(() => {
    if (!enabled) return
    const controller = new AbortController()
    void load(id, controller.signal).then(
      (data) => { if (!controller.signal.aborted) setResult({ attempt, value: { data } }) },
      (error: unknown) => {
        if (!controller.signal.aborted) setResult({ attempt, value: { error: errorText(error, '读取失败') } })
      },
    )
    return () => controller.abort()
  }, [attempt, enabled, id, load])

  return { result: result?.attempt === attempt ? result.value : undefined, retry: () => setAttempt((current) => current + 1) }
}

function RequestError({ text, onRetry }: { text: string; onRetry: () => void }) {
  return <div className="flex flex-wrap items-center gap-2">
    <p role="alert" className="break-words text-sm text-destructive">{text}</p>
    <Button type="button" size="sm" variant="outline" onClick={onRetry}>重新读取</Button>
  </div>
}

function Download({ action, children }: { action: (signal: AbortSignal) => Promise<void>; children: string }) {
  const request = useRef<AbortController | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => () => request.current?.abort(), [])

  async function start() {
    if (request.current) return
    const controller = new AbortController()
    request.current = controller
    setPending(true)
    setError('')
    try {
      await action(controller.signal)
    } catch (error) {
      if (!controller.signal.aborted) setError(errorText(error, '下载失败'))
    } finally {
      request.current = null
      if (!controller.signal.aborted) setPending(false)
    }
  }

  return <div>
    <Button type="button" size="sm" variant="outline" disabled={pending} onClick={start}>
      {pending ? '正在下载…' : children}
    </Button>
    {error && <p role="alert" className="mt-1 break-words text-xs text-destructive">{error}</p>}
  </div>
}

function ReportPreview({ id, section }: { id: string; section: string }) {
  const load = useCallback((runId: string, signal: AbortSignal) => getAmazonResearchReport(runId, section, signal), [section])
  const { result, retry } = useWorkspaceRequest(load, id)
  if (!result) return <p role="status" className="text-sm text-muted-foreground">正在读取报告…</p>
  if (result.error !== undefined) return <RequestError text={result.error} onRetry={retry} />
  const { markdown } = result.data
  if (!markdown.trim()) return <p className="text-sm text-muted-foreground">该章节暂时没有可预览的内容。</p>
  return <div className="space-y-2">
    {section !== 'full' && <Button type="button" size="sm" variant="outline" onClick={() => downloadAmazonFile(markdown, `${id}-${section}.md`)}>
      下载所选章节
    </Button>}
    <div className="prose prose-sm max-w-none overflow-x-auto break-words rounded-md border p-3 dark:prose-invert">
      <ReactMarkdown skipHtml remarkPlugins={[remarkGfm]} components={{
        a: ({ href, children }) => {
          const url = safeProductUrl(href)
          return url ? <a className="text-primary underline" href={url} target="_blank" rel="noreferrer">{children}</a> : <span>{children}</span>
        },
        img: () => null,
      }}>{markdown}</ReactMarkdown>
    </div>
  </div>
}

function Reports({ id }: { id: string }) {
  const [section, setSection] = useState('')
  const { result, retry } = useWorkspaceRequest(getAmazonResearchReports, id)
  if (!result) return <p role="status" className="text-sm text-muted-foreground">正在读取报告目录…</p>
  if (result.error !== undefined) return <RequestError text={result.error} onRetry={retry} />

  const { sections, site, version } = result.data
  return <div className="space-y-2">
    <p className="text-xs text-muted-foreground">站点 {site} · 报告版本 {version} · {sections.length} 个章节</p>
    {sections.length === 0 && <p className="text-sm text-muted-foreground">该任务暂时没有报告章节。</p>}
    <div className="flex flex-wrap items-end gap-2">
      <label className="grid min-w-0 gap-1 text-sm">
        预览报告
        <select className="h-9 w-full rounded-md border border-input bg-background px-2" value={section} onChange={(event) => setSection(event.target.value)}>
          <option value="">选择报告章节</option>
          <option value="full">完整报告</option>
          {sections.filter((item) => item.id !== 'full').map((item) => <option key={item.id} value={item.id}>
            {item.title} · {amazonWorkspaceStatus(item.status)} · {item.sample_count == null ? '样本数未知' : `${item.sample_count} 个样本`}
          </option>)}
        </select>
      </label>
      <Download action={async (signal) => {
        const { markdown } = await getAmazonResearchReport(id, 'full', signal)
        if (!signal.aborted) downloadAmazonFile(markdown, `${id}-report.md`)
      }}>下载完整报告</Download>
    </div>
    {section && <ReportPreview key={section} id={id} section={section} />}
  </div>
}

function Comparison({ comparison, onOpen, busy }: Pick<WorkspaceProps, 'onOpen' | 'busy'> & { comparison: AmazonResearchComparison }) {
  const { previous, current, metrics, notes } = comparison
  return <div className="space-y-2 text-sm">
    {previous ? <>
      <dl className="grid gap-2 sm:grid-cols-2">
        {([['上期', previous], ['本期', current]] as const).map(([label, item]) => <div key={label} className="min-w-0 rounded-md border p-2">
          <dt className="font-medium">{label} · {item.title || '未命名调研'}</dt>
          <dd className="break-all text-xs text-muted-foreground">{item.source}</dd>
          <dd className="text-xs text-muted-foreground">{amazonWorkspaceStatus(item.status)} · {amazonTime(item.updated_at)}</dd>
        </div>)}
      </dl>
      <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => onOpen(previous.id)}>打开上期任务</Button>
      {metrics.length === 0 && <p className="text-muted-foreground">这两次任务没有可比较的指标。</p>}
    </> : <p className="text-muted-foreground">暂无可比较的上期任务。</p>}
    {metrics.length > 0 && <div className="overflow-x-auto">
      <table className="w-full min-w-72 text-left">
        <caption className="sr-only">上期与本期样本对比</caption>
        <thead><tr className="border-b">
          <th scope="col" className="px-2 py-1">指标</th>
          <th scope="col" className="px-2 py-1">上期</th>
          <th scope="col" className="px-2 py-1">本期</th>
          <th scope="col" className="px-2 py-1">统计口径</th>
        </tr></thead>
        <tbody>{metrics.map((metric, index) => <tr key={`${metric.label}-${index}`} className="border-b">
          <th scope="row" className="px-2 py-1 font-medium">{metric.label}</th>
          <td className="px-2 py-1">{amazonComparisonValue(metric.before, metric.unit)}</td>
          <td className="px-2 py-1">{amazonComparisonValue(metric.after, metric.unit)}</td>
          <td className="px-2 py-1 text-muted-foreground">{metric.basis || '—'}</td>
        </tr>)}</tbody>
      </table>
    </div>}
    {notes.length > 0 && <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground">
      {notes.map((note, index) => <li key={`${note}-${index}`}>{note}</li>)}
    </ul>}
  </div>
}

type ComparisonView = 'idle' | 'open' | 'closed'

function WorkspaceReports({ id, comparisonView, comparisonId, onOpen, busy }: Pick<WorkspaceProps, 'onOpen' | 'busy'> & {
  id: string; comparisonView: ComparisonView; comparisonId: string
}) {
  const { result, retry } = useWorkspaceRequest(getAmazonResearchComparison, id, comparisonView !== 'idle')
  const [view, setView] = useState<'current' | 'previous'>('current')
  const previous = result?.data?.previous
  const reportId = view === 'previous' && previous ? previous.id : id
  return <div className="space-y-3">
    <section id={comparisonId} hidden={comparisonView !== 'open'} className="space-y-2" aria-label="上期对比">
      <h4 className="text-sm font-medium">上期对比</h4>
      {!result && <p role="status" className="text-sm text-muted-foreground">正在读取对比…</p>}
      {result?.error !== undefined && <RequestError text={result.error} onRetry={retry} />}
      {result?.data && <Comparison comparison={result.data} onOpen={onOpen} busy={busy} />}
    </section>
    <section className="space-y-2" aria-label="调研报告">
      <h4 className="text-sm font-medium">调研报告</h4>
      {previous && <label className="flex flex-wrap items-center gap-2 text-sm">
        报告期次
        <select className="h-9 rounded-md border border-input bg-background px-2" value={view} onChange={(event) => setView(event.target.value === 'previous' ? 'previous' : 'current')}>
          <option value="current">本期报告</option>
          <option value="previous">上期报告</option>
        </select>
      </label>}
      <Reports key={reportId} id={reportId} />
    </section>
  </div>
}

function Workspace({ run, onOpen, onRetryTranslation, busy }: WorkspaceProps) {
  const [comparisonView, setComparisonView] = useState<ComparisonView>('idle')
  const comparisonId = useId()
  const active = activeAmazonRun(run)
  const { context }: ResearchInputs = run.inputs
  const snapshot: ResearchSnapshot = run.snapshot
  const translationCount = Object.keys(snapshot.field_translations ?? {}).length
  const missing = snapshot.translation_missing.length
  const retryTranslation = Boolean(run.report && ['done', 'partial_fail'].includes(run.status)
    && snapshot.translation_status !== 'success')
  const incomplete = snapshot.evidence.filter((item) => item.status === 'incomplete' || item.status === 'failed').length

  return <section aria-label="Amazon 调研工作台" className="space-y-3 rounded-lg border bg-card p-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="font-medium">Amazon 调研工作台</h3>
      <p role="status" className="text-sm">{amazonWorkspaceStatus(run.status)}</p>
    </div>
    <div className="space-y-1 text-xs text-muted-foreground">
      <p>结果仅代表本次公开样本，不是全量数据；未取得的数据不等于零。</p>
      {incomplete > 0 && <p>有 {incomplete} 个采集项失败或样本不完整，详情见任务进度。</p>}
      {snapshot.store_scope?.navigation_truncated && <p>店铺导航采样已截断，本次报告未覆盖所有栏目。</p>}
      {snapshot.category_scope?.discovery_truncated && <p>子榜发现已截断，本次报告未覆盖所有子榜。</p>}
    </div>
    {context && <div className="space-y-1 rounded-md border p-2 text-sm">
      <h4 className="font-medium">来源任务</h4>
      <p className="break-all text-xs text-muted-foreground">
        任务 {context.run_id}{context.asin && ` · ASIN ${context.asin}`}{context.direction && ` · 调研方向 ${context.direction}`}
      </p>
      <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => onOpen(context.run_id)}>打开来源任务</Button>
    </div>}
    <div className="flex flex-wrap items-center gap-2">
      <p className="text-sm text-muted-foreground">
        中文补充：{snapshot.translation_status === 'pending' ? '待处理' : amazonWorkspaceStatus(snapshot.translation_status)}
        {translationCount > 0 && ` · 已补充 ${translationCount} 个字段`}
        {missing > 0 && ` · 缺少 ${missing} 个字段的译文`}
      </p>
      {retryTranslation && <Button type="button" size="sm" variant="outline" disabled={busy} onClick={onRetryTranslation}>重试中文补充</Button>}
    </div>
    {translationCount > 0 && <details className="rounded-md border p-2 text-sm">
      <summary className="cursor-pointer">核对中文补充和原文（{translationCount} 项）</summary>
      <dl className="mt-2 max-h-72 space-y-2 overflow-auto">
        {Object.entries(snapshot.field_translations ?? {}).map(([original, translated]) => <div key={original} className="border-b pb-2">
          <dt className="break-words font-medium">{translated}</dt>
          <dd className="whitespace-pre-wrap break-words text-xs text-muted-foreground" lang="en">{original}</dd>
        </div>)}
      </dl>
    </details>}
    {run.inputs.supplemental_notes && <details className="rounded-md border p-2 text-sm">
      <summary className="cursor-pointer">用户补充资料（未独立核验）</summary>
      <p className="mt-2 whitespace-pre-wrap break-words">{run.inputs.supplemental_notes}</p>
      <p className="mt-1 text-xs text-muted-foreground">不计入公开样本统计。</p>
    </details>}
    <div className="flex flex-wrap items-start gap-2">
      <Download action={async (signal) => {
        const evidence = await getAmazonResearchEvidence(run.id, signal)
        if (!signal.aborted) downloadAmazonFile(JSON.stringify(evidence, null, 2) + '\n', `${run.id}-evidence.json`, 'application/json')
      }}>下载原始证据</Download>
      {!active && <Button type="button" size="sm" variant="outline" aria-expanded={comparisonView === 'open'} aria-controls={comparisonId}
        onClick={() => setComparisonView((view) => view === 'open' ? 'closed' : 'open')}>
        {comparisonView === 'open' ? '收起上期对比' : '查看上期对比'}
      </Button>}
    </div>
    {active ? <p className="text-sm text-muted-foreground">任务停止运行后可查看报告与上期对比。</p>
      : <WorkspaceReports key={run.updated_at} id={run.id} comparisonView={comparisonView} comparisonId={comparisonId} onOpen={onOpen} busy={busy} />}
  </section>
}

export function AmazonResearchWorkspace(props: WorkspaceProps) {
  return <Workspace key={props.run.id} {...props} />
}
