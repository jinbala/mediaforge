'use client'

import { useEffect, useState, type FormEvent } from 'react'
import Image from 'next/image'
import { useRouter } from 'next/navigation'
import {
  AlertCircle,
  ArrowDownToLine,
  ArrowRight,
  ChevronDown,
  ExternalLink,
  History,
  ImageOff,
  ListOrdered,
  Loader2,
  PackageSearch,
  Search,
  Store,
} from 'lucide-react'

import { AmazonRunDetails } from '@/components/research/AmazonRunDetails'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { api } from '@/lib/api'
import {
  AMAZON_BRIEF_KEY,
  AMAZON_BRIEF_LIMIT,
  AMAZON_BRIEF_PARAM,
  activeAmazonRun,
  amazonHistoryKey,
  amazonRunLabels,
  amazonTime,
  downloadAmazonFile,
  formatAmazonReport,
  groupAmazonListings,
  readAmazonHistory,
  saveAmazonHistory,
  type AmazonListing,
  type AmazonReport,
  type AmazonRetryTarget,
  type AmazonRun,
  type AmazonRunSummary,
  type ResearchMode,
} from '@/lib/amazon-research'
import { cn } from '@/lib/utils'
import { useAuth } from '@/store/auth'

const modes = [
  { value: 'product', label: '单品调研', hint: '卖点、排名与评论样本', sourceLabel: 'ASIN 或商品链接', placeholder: 'B0033SHO4Q 或 https://www.amazon.com/dp/…', icon: PackageSearch },
  { value: 'bestsellers', label: '类目榜单', hint: '对比榜单商品与页面价格', sourceLabel: 'Best Sellers 榜单链接', placeholder: 'https://www.amazon.com/gp/bestsellers/fashion', icon: ListOrdered },
  { value: 'store', label: '品牌店铺', hint: '自动采集品牌店铺多个栏目', sourceLabel: '品牌店铺或栏目链接', placeholder: 'https://www.amazon.com/stores/品牌/page/…', icon: Store },
] as const

// 三种调研结果共用商品缩略图。缺图或 CDN 加载失败时显示占位。
function AmazonThumbnail({ src, size }: { src?: string | null, size: number }) {
  const [failed, setFailed] = useState(false)
  return (
    <span
      className={cn('grid shrink-0 place-items-center overflow-hidden rounded-md border', src && !failed ? 'bg-white' : 'bg-muted')}
      style={{ width: size, height: size }}
    >
      {src && !failed ? (
        <Image
          src={src}
          alt=""
          width={size}
          height={size}
          className="h-full w-full object-contain"
          referrerPolicy="no-referrer"
          unoptimized
          onError={() => setFailed(true)}
        />
      ) : (
        <span className="flex flex-col items-center gap-1 text-[10px] font-normal text-muted-foreground">
          <ImageOff className="h-4 w-4" aria-hidden="true" />
          无主图
        </span>
      )}
    </span>
  )
}

const compactSizes: Record<string, string> = {
  'x-small': 'XS', small: 'S', medium: 'M', large: 'L', 'x-large': 'XL', 'xx-large': 'XXL',
}

function listingSize(item: AmazonListing) {
  const label = item.store_variant?.size ?? ''
  return compactSizes[label.toLowerCase()] ?? label
}

function ListingSources({ asin, run }: { asin: string; run: AmazonRun | null }) {
  if (!run) return null
  const sources = new Set(run.snapshot.asin_sources[asin] ?? [])
  sources.add('product-' + asin)
  const evidence = run.snapshot.evidence.filter((item) => sources.has(item.key))
  if (!evidence.length) return null
  return (
    <div className="mt-1 flex flex-wrap gap-x-2 text-xs text-muted-foreground">
      {evidence.map((item) => (
        <a key={item.key} className="hover:text-primary" href={'#evidence-' + item.key}>
          来源 · {item.title_zh ?? item.title}
        </a>
      ))}
    </div>
  )
}

function StoreSizeGroup({ items, loading, run, onResearch }: {
  items: AmazonListing[]
  loading: boolean
  run: AmazonRun | null
  onResearch: (url: string) => void
}) {
  const first = items[0]
  const variant = first.store_variant
  // 折叠行用组内首张可用主图，展开后每个尺码仍保留自己的商品链接和调研入口。
  const cover = items.find((item) => item.image_url)?.image_url
  const title = variant?.title_zh ?? variant?.title ?? first.title
  const color = variant?.color_zh ?? variant?.color
  const sizes = [...new Set(items.map(listingSize))]
  return (
    <details className="group">
      <summary className="flex cursor-pointer list-none items-center gap-3 rounded-md px-3 py-3 text-left hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
        <AmazonThumbnail key={cover} src={cover} size={80} />
        <span className="min-w-0 flex-1 space-y-1.5 [overflow-wrap:anywhere]">
          <span className="block font-medium">{title}</span>
          {color && <span className="block text-xs text-muted-foreground">配色 · {color}</span>}
          <span className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
            <span>尺码</span>
            {sizes.map((size) => <span key={size} className="rounded border px-1.5 py-0.5 font-medium text-foreground">{size}</span>)}
          </span>
        </span>
        <span className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
          <span className="hidden sm:inline">{items.length} 个规格</span>
          <ChevronDown aria-hidden="true" className="size-4 transition-transform group-open:rotate-180" />
        </span>
      </summary>
      <div className="mx-3 mb-3 rounded-md border bg-background/60">
        {variant?.title_zh && <p className="border-b px-3 py-2 text-xs text-muted-foreground" lang="en">{variant.title}</p>}
        <ul className="divide-y" aria-label="尺码规格">
          {items.map((item) => (
            <li key={item.asin} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-3 py-1.5">
              <a href={item.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-2 hover:text-primary">
                <span className="font-medium">{listingSize(item)}</span>
                <span className="font-mono text-xs text-muted-foreground">{item.asin}</span>
                <ExternalLink aria-hidden="true" className="size-3" />
              </a>
              <ListingSources asin={item.asin} run={run} />
              <Button variant="ghost" size="sm" disabled={loading} aria-label={'调研 ' + item.asin} onClick={() => onResearch(item.url)}>
                调研<ArrowRight aria-hidden="true" />
              </Button>
            </li>
          ))}
        </ul>
      </div>
    </details>
  )
}

function Listings({ items, groups, ranked, loading, run, onResearch }: {
  items: AmazonListing[]
  groups: AmazonListing[][]
  ranked: boolean
  loading: boolean
  run: AmazonRun | null
  onResearch: (url: string) => void
}) {
  const showStats = ranked || items.some((item) => item.price || item.rating || item.rating_count)
  return (
    <div className="overflow-x-auto">
      <table className={cn('w-full text-left text-sm', showStats && 'min-w-[570px]')}>
        <thead className="border-b text-xs text-muted-foreground">
          <tr>
            {ranked && <th scope="col" className="px-3 py-2 font-medium">排名</th>}
            <th scope="col" className="px-3 py-2 font-medium">商品</th>
            {showStats && (
              <>
                <th scope="col" className="whitespace-nowrap px-3 py-2 font-medium">页面价格</th>
                <th scope="col" className="whitespace-nowrap px-3 py-2 font-medium">评分</th>
                <th scope="col" className="whitespace-nowrap px-3 py-2 font-medium">评分数</th>
              </>
            )}
            <th scope="col" className="sr-only">调研操作</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/70">
          {groups.map(([item, ...rest]) => rest.length ? (
            <tr key={item.asin}>
              <td colSpan={2 + Number(ranked) + (showStats ? 3 : 0)} className="p-0">
                <StoreSizeGroup items={[item, ...rest]} loading={loading} run={run} onResearch={onResearch} />
              </td>
            </tr>
          ) : (
            <tr key={item.asin}>
              {ranked && <td className="px-3 py-3 tabular-nums">{item.rank ?? '—'}</td>}
              <td className="px-3 py-3">
                <a className="group flex items-center gap-3 rounded-md" href={item.url} target="_blank" rel="noreferrer">
                  <AmazonThumbnail key={item.image_url} src={item.image_url} size={80} />
                  <span className="min-w-0 break-words">
                    <span className="font-medium group-hover:text-primary group-hover:underline">{item.title_zh ?? item.title}</span>
                    {item.title_zh && <span className="mt-0.5 block text-xs text-muted-foreground" lang="en">{item.title}</span>}
                    {item.title !== item.asin && <span className="mt-0.5 block font-mono text-xs text-muted-foreground">{item.asin}</span>}
                  </span>
                </a>
                <ListingSources asin={item.asin} run={run} />
              </td>
              {showStats && (
                <>
                  <td className="whitespace-nowrap px-3 py-3 tabular-nums">{item.price ?? '—'}</td>
                  <td className="px-3 py-3 tabular-nums">{item.rating ?? '—'}</td>
                  <td className="px-3 py-3 tabular-nums">{item.rating_count ?? '—'}</td>
                </>
              )}
              <td className="w-px px-3 py-3">
                <Button variant="ghost" size="sm" disabled={loading} aria-label={'调研 ' + item.asin} onClick={() => onResearch(item.url)}>
                  调研<ArrowRight aria-hidden="true" />
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function ResearchPage() {
  const router = useRouter()
  const user = useAuth((state) => state.user)
  const historyKey = user ? amazonHistoryKey(user.tenant_id, user.user_id) : null
  const [mode, setMode] = useState<ResearchMode>('product')
  const [source, setSource] = useState('')
  const [keyword, setKeyword] = useState('')
  const [reviewSamples, setReviewSamples] = useState('')
  const [legacyReport, setLegacyReport] = useState<AmazonReport | null>(null)
  const [run, setRun] = useState<AmazonRun | null>(null)
  const report = run ? run.report : legacyReport
  const [brief, setBrief] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [pollError, setPollError] = useState('')
  const [historyOpen, setHistoryOpen] = useState(false)
  const [history, setHistory] = useState<AmazonReport[]>([])
  const [serverHistory, setServerHistory] = useState<AmazonRunSummary[]>([])
  const [historyBusy, setHistoryBusy] = useState(false)
  const [hasOlder, setHasOlder] = useState(false)
  const [historyError, setHistoryError] = useState('')
  const activeMode = modes.find((item) => item.value === mode) ?? modes[0]

  const runningId = run && activeAmazonRun(run) ? run.id : null

  useEffect(() => {
    if (!runningId) return
    const controller = new AbortController()
    let timer: number
    async function poll() {
      try {
        const updated = await api.get<AmazonRun>('/v1/research/amazon/runs/' + runningId, { signal: controller.signal })
        if (controller.signal.aborted) return
        setRun((current) => current?.id === updated.id ? updated : current)
        setPollError('')
        if (activeAmazonRun(updated)) timer = window.setTimeout(poll, 1800)
      } catch {
        if (!controller.signal.aborted) {
          setPollError('暂时无法刷新进度，正在重连。也可从历史记录打开任务。')
          timer = window.setTimeout(poll, 5000)
        }
      }
    }
    timer = window.setTimeout(poll, 1200)
    return () => {
      controller.abort()
      window.clearTimeout(timer)
    }
  }, [runningId])

  async function runResearch(nextMode: ResearchMode, nextSource: string, samples = '') {
    if (loading || !nextSource.trim()) return
    setLoading(true)
    setError('')
    setPollError('')
    const body = { mode: nextMode, source: nextSource.trim(), keyword: keyword.trim(), review_samples: samples }
    try {
      if (nextMode === 'store') {
        const result = await api.post<AmazonRun>('/v1/research/amazon/runs', {
          ...body, submission_id: crypto.randomUUID(),
        })
        setRun(result)
        setLegacyReport(null)
        setBrief('')
      } else {
        const result = await api.post<AmazonReport>('/v1/research/amazon', body)
        setRun(null)
        setLegacyReport(result)
        setBrief(result.product?.image_brief ?? '')
        if (historyKey) {
          try {
            saveAmazonHistory(historyKey, result)
            setHistoryError('')
          } catch {
            setHistoryError('本次报告未能保存到浏览器历史。')
          }
        }
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '调研失败，请稍后重试。')
    } finally {
      setLoading(false)
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void runResearch(mode, source, mode === 'product' ? reviewSamples : '')
  }

  function openRelated(nextMode: ResearchMode, url: string) {
    if (loading) return
    setMode(nextMode)
    setSource(url)
    setReviewSamples('')
    void runResearch(nextMode, url)
  }

  async function loadServerHistory(before?: string) {
    setHistoryBusy(true)
    setHistoryError('')
    try {
      const items = await api.get<AmazonRunSummary[]>('/v1/research/amazon/runs' + (before ? '?before=' + encodeURIComponent(before) : ''))
      setServerHistory((previous) => before ? [...previous, ...items] : items)
      setHasOlder(items.length === 30)
    } catch {
      setHistoryError('服务器调研历史暂时无法读取。')
    } finally {
      setHistoryBusy(false)
    }
  }

  function toggleHistory(open: boolean) {
    setHistoryOpen(open)
    if (!open || !historyKey) return
    try {
      setHistory(readAmazonHistory(historyKey))
    } catch {
      setHistory([])
    }
    void loadServerHistory()
  }

  function restoreHistory(saved: AmazonReport) {
    // 浏览器旧快照按原格式打开，服务器任务可以在账号的其他设备恢复。
    setRun(null)
    setMode(saved.mode)
    setSource(saved.source_url)
    setKeyword(saved.keyword ?? '')
    setReviewSamples(saved.product?.reviews.filter((review) => review.source === 'manual').map((review) => review.text).join('\n') ?? '')
    setLegacyReport(saved)
    setBrief(saved.product?.image_brief ?? '')
    setError('')
    setHistoryOpen(false)
  }

  async function openRun(id: string) {
    setLoading(true)
    try {
      const saved = await api.get<AmazonRun>('/v1/research/amazon/runs/' + id)
      setRun(saved)
      setLegacyReport(null)
      setMode(saved.mode)
      setSource(saved.source)
      setKeyword(saved.inputs.keyword)
      setReviewSamples('')
      setBrief('')
      setPollError('')
      setError('')
      setHistoryOpen(false)
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : '无法打开调研任务。'
      setError(message)
      setHistoryError(message)
    } finally {
      setLoading(false)
    }
  }

  async function retryRun(target: AmazonRetryTarget, key?: string) {
    if (!run || loading) return
    setLoading(true)
    setError('')
    try {
      const response = await api.post<AmazonRun>('/v1/research/amazon/runs/' + run.id + '/retry?target=' + target + (key ? '&item_key=' + encodeURIComponent(key) : ''), {})
      setRun(response)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '无法恢复调研。')
    } finally {
      setLoading(false)
    }
  }

  function downloadReport(currentReport: AmazonReport) {
    downloadAmazonFile(
      formatAmazonReport(currentReport, brief),
      'amazon-research-' + (currentReport.product?.asin ?? currentReport.mode) + '.md',
    )
  }

  function sendBrief() {
    try {
      // 正文暂存在当前标签页，地址栏只传 ID，图片创作页用它匹配这次带入的需求。
      const id = Array.from(crypto.getRandomValues(new Uint32Array(4)), (part) => part.toString(16).padStart(8, '0')).join('')
      sessionStorage.setItem(AMAZON_BRIEF_KEY, JSON.stringify({ id, brief: brief.trim() }))
      router.push('/batch?' + AMAZON_BRIEF_PARAM + '=' + id)
    } catch {
      setError('带入失败，可复制图片需求后粘贴到图片创作。')
    }
  }

  const product = report?.product
  const storeUrl = product?.store_url
  const listingGroups = report ? groupAmazonListings(report.listings, report.mode) : []

  return (
    <div className="mx-auto max-w-[1180px] space-y-6">
      <header className="border-b pb-4">
        <h1 className="text-2xl font-semibold tracking-tight">亚马逊调研</h1>
        <p className="mt-1 text-sm text-muted-foreground">调研 Amazon 美国站公开页面，查找可用于选品和图片制作的线索。</p>
      </header>

      {/* 关闭表单自动补全，避免历史商品或店铺链接弹层遮挡报告。 */}
      <form onSubmit={submit} autoComplete="off" className="space-y-4 rounded-xl border bg-card p-4 sm:p-5" aria-busy={loading}>
        <div className="flex flex-col gap-2 md:flex-row">
          <div className="grid min-w-0 flex-1 gap-2 sm:grid-cols-3" role="group" aria-label="调研类型">
            {modes.map(({ value, label, hint, icon: Icon }) => (
              <button
                type="button"
                key={value}
                aria-pressed={mode === value}
                disabled={loading}
                onClick={() => {
                  if (value !== mode) {
                    setMode(value)
                    setSource('')
                    setReviewSamples('')
                    setError('')
                  }
                }}
                className={cn(
                  'flex items-center gap-3 rounded-lg border px-3 py-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-70',
                  mode === value ? 'border-primary/50 bg-primary/10' : 'bg-background hover:bg-muted/50',
                )}
              >
                <Icon aria-hidden="true" className={cn('h-5 w-5 shrink-0', mode === value ? 'text-primary' : 'text-muted-foreground')} />
                <span>
                  <span className="block text-sm font-medium">{label}</span>
                  <span className="mt-0.5 block text-xs text-muted-foreground">{hint}</span>
                </span>
              </button>
            ))}
          </div>
          <Dialog open={historyOpen} onOpenChange={toggleHistory}>
            <DialogTrigger asChild>
              <Button type="button" variant="outline" className="h-auto min-h-10 self-end md:self-stretch" disabled={loading || !historyKey}>
                <History aria-hidden="true" />历史记录
              </Button>
            </DialogTrigger>
            <DialogContent className="w-[calc(100%-2rem)] gap-3 rounded-lg p-4 sm:p-6">
              <DialogHeader>
                <DialogTitle>调研历史记录</DialogTitle>
                <DialogDescription>店铺采集记录可在同一账号的其他设备打开。单品和类目结果保存在当前浏览器。</DialogDescription>
              </DialogHeader>
              {historyError && <p role="status" className="text-sm text-muted-foreground">{historyError}</p>}
              {historyBusy && <p role="status" className="text-sm text-muted-foreground">正在读取调研任务…</p>}
              {serverHistory.length > 0 && (
                <ul className="max-h-[40vh] min-w-0 divide-y overflow-y-auto rounded-md border">
                  {serverHistory.map((item) => (
                    <li key={item.id}>
                      <button type="button" disabled={loading} className="block w-full break-words px-3 py-2.5 text-left text-sm hover:bg-muted/50" onClick={() => void openRun(item.id)}>
                        {item.title ?? item.source}
                        <span className="mt-1 block text-xs text-muted-foreground">
                          {modes.find((entry) => entry.value === item.mode)?.label} · {amazonRunLabels[item.status]} · {amazonTime(item.created_at)}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {hasOlder && <Button variant="outline" size="sm" disabled={historyBusy} onClick={() => void loadServerHistory(serverHistory.at(-1)?.created_at)}>加载更早记录</Button>}
              {history.length ? (
                <>
                <p className="text-sm font-medium">本浏览器旧快照</p>
                <ul className="max-h-[55vh] min-w-0 divide-y overflow-y-auto rounded-md border">
                  {history.map((saved) => (
                    <li key={`${saved.mode}:${saved.source_url}`}>
                      <button type="button" className="flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring" onClick={() => restoreHistory(saved)}>
                        <span className="min-w-0 flex-1">
                          <span className="line-clamp-2 break-words text-sm font-medium">{saved.title_zh ?? saved.title}</span>
                          <span className="mt-1 flex flex-wrap gap-x-2 text-xs text-muted-foreground">
                            <span>{modes.find((item) => item.value === saved.mode)?.label}</span>
                            {saved.product && <span className="font-mono">{saved.product.asin}</span>}
                            <time dateTime={saved.collected_at}>{amazonTime(saved.collected_at)}</time>
                          </span>
                        </span>
                        <ArrowRight aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />
                      </button>
                    </li>
                  ))}
                </ul>
                </>
              ) : !historyError && !historyBusy && !serverHistory.length && (
                <p className="rounded-md border px-3 py-6 text-center text-sm text-muted-foreground">暂无调研记录</p>
              )}
            </DialogContent>
          </Dialog>
        </div>

        <div className="grid gap-3 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_auto] md:items-end">
          <div className="space-y-1.5">
            <Label htmlFor="research-source">{activeMode.sourceLabel}</Label>
            <Input id="research-source" required maxLength={2000} value={source} onChange={(event) => setSource(event.target.value)} placeholder={activeMode.placeholder} disabled={loading} spellCheck={false} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="research-keyword">搜索联想词种子（可选）</Label>
            <Input id="research-keyword" maxLength={100} value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder="如 womens linen pants" disabled={loading} />
          </div>
          <Button type="submit" disabled={loading || !source.trim()}>
            {loading ? <Loader2 className="animate-spin" aria-hidden="true" /> : <Search aria-hidden="true" />}
            {loading ? '正在调研' : '开始调研'}
          </Button>
        </div>

        {mode === 'product' && (
          <details className="text-sm">
            <summary className="w-fit cursor-pointer font-medium text-muted-foreground hover:text-foreground">补充评论样本（可选）</summary>
            <Textarea
              aria-label="补充评论样本"
              className="mt-2"
              maxLength={4000}
              value={reviewSamples}
              onChange={(event) => setReviewSamples(event.target.value)}
              disabled={loading}
              placeholder="每行粘贴一条评论片段，支持中英文。"
            />
          </details>
        )}
        <p className="sr-only" role="status">{loading ? '正在读取 Amazon 页面' : report ? '调研报告已就绪' : ''}</p>
      </form>

      {error && (
        <div role="alert" className="flex gap-2 rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />{error}
        </div>
      )}

      {run && <AmazonRunDetails key={run.id} run={run} busy={loading} pollError={pollError} onRetry={(target, key) => void retryRun(target, key)} />}

      {report && (
        <article className="space-y-5" aria-label="调研报告">
          <header className="flex flex-wrap items-start justify-between gap-3 border-b pb-4">
            <div className="min-w-0 flex-1 space-y-1">
              <h2 className="break-words text-lg font-semibold">{report.title_zh ?? report.title}</h2>
              {report.title_zh && <p className="break-words text-xs text-muted-foreground" lang="en">{report.title}</p>}
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                <a href={report.source_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 hover:text-primary">查看 Amazon 来源 <ExternalLink className="h-3 w-3" /></a>
                <time dateTime={report.collected_at}>{amazonTime(report.collected_at)}</time>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              {storeUrl && (
                <Button variant="outline" disabled={loading} onClick={() => openRelated('store', storeUrl)}>
                  <Store aria-hidden="true" />调研品牌店铺
                </Button>
              )}
              <Button variant="outline" onClick={() => downloadReport(report)}><ArrowDownToLine aria-hidden="true" />下载报告</Button>
            </div>
          </header>

          {report.warnings.length > 0 && (
            <ul className="space-y-1 rounded-lg border border-amber-500/30 bg-amber-500/[0.06] px-4 py-3 text-sm text-muted-foreground">
              {report.warnings.map((warning) => <li key={warning}>{warning}</li>)}
            </ul>
          )}

          {product && (
            <>
              <div className="flex flex-wrap items-start gap-4 rounded-lg border bg-card p-4 sm:flex-nowrap">
                <a className="rounded-md" href={product.url} target="_blank" rel="noreferrer" aria-label={`在 Amazon 查看商品 ${product.asin}`}>
                  <AmazonThumbnail key={product.image_url} src={product.image_url} size={128} />
                </a>
                <dl className="grid min-w-0 flex-1 basis-48 grid-cols-2 gap-x-4 gap-y-3 text-sm md:grid-cols-3">
                  {[
                    ['ASIN', product.asin], ['品牌', product.brand], ['类目', product.category],
                    ['页面价格', product.price], ['评分', product.rating && product.rating + ' / 5'], ['评分数', product.rating_count],
                  ].map(([label, value]) => (
                    <div key={label} className="min-w-0">
                      <dt className="text-xs text-muted-foreground">{label}</dt>
                      <dd className="mt-0.5 break-words font-medium">{value ?? '—'}</dd>
                    </div>
                  ))}
                  {product.sales_ranks.length > 0 && (
                    <div className="col-span-full min-w-0">
                      <dt className="text-xs text-muted-foreground">畅销排名</dt>
                      <dd className="mt-1 flex flex-wrap gap-2">{product.sales_ranks.map((rank) => <Badge variant="outline" key={rank}>{rank}</Badge>)}</dd>
                    </div>
                  )}
                </dl>
              </div>
              {product.highlights.length > 0 && (
                <section className="rounded-lg border bg-card p-4">
                  <h3 className="font-medium">商品页卖点</h3>
                  <ul className="mt-2 list-disc space-y-2 pl-5 text-sm text-muted-foreground">
                    {product.highlights.map((item) => <li key={item}>{item}</li>)}
                  </ul>
                </section>
              )}
              {product.reviews.length > 0 && (
                <section className="space-y-3 rounded-lg border bg-card p-4">
                  <h3 className="font-medium">评论样本 · {product.reviews.length} 条</h3>
                  {product.review_topics.length > 0 && (
                    <ul className="grid gap-2 sm:grid-cols-2">
                      {product.review_topics.map((topic) => (
                        <li className="rounded-md border p-3" key={topic.label}>
                          <p className="flex flex-wrap gap-x-2 text-sm font-medium">{topic.label}<span className="font-normal text-muted-foreground">{topic.matches} 条提及</span></p>
                          <p className="mt-1 text-xs text-muted-foreground">{topic.suggestion}</p>
                        </li>
                      ))}
                    </ul>
                  )}
                  <details className="text-sm">
                    <summary className="w-fit cursor-pointer font-medium text-muted-foreground hover:text-foreground">查看评论原文片段</summary>
                    <ul className="mt-2 divide-y">
                      {product.reviews.map((review) => (
                        <li key={review.text} className="py-2">
                          <p className="text-xs text-muted-foreground">{review.source === 'manual' ? '手动录入' : '商品公开页'}{review.rating && ' · ' + review.rating + ' 星'}</p>
                          <p className="mt-1 break-words">{review.text}</p>
                        </li>
                      ))}
                    </ul>
                  </details>
                </section>
              )}
              <section className="space-y-3 rounded-lg border border-primary/25 bg-card p-4">
                <Label htmlFor="image-brief" className="text-base font-medium">图片制作需求</Label>
                <Textarea id="image-brief" className="min-h-32" maxLength={AMAZON_BRIEF_LIMIT} value={brief} onChange={(event) => setBrief(event.target.value)} />
                <Button disabled={loading || !brief.trim()} onClick={sendBrief}>带入图片创作 <ArrowRight aria-hidden="true" /></Button>
              </section>
            </>
          )}

          {report.listings.length > 0 && (
            <section className="rounded-lg border bg-card">
              <h3 className="px-4 pt-4 font-medium">
                商品样本 · {listingGroups.length < report.listings.length
                  ? `${listingGroups.length} 组 · ${report.listings.length} 个 ASIN`
                  : `${report.listings.length} 件`}
              </h3>
              <Listings items={report.listings} groups={listingGroups} ranked={report.mode === 'bestsellers'} loading={loading} run={run} onResearch={(url) => openRelated('product', url)} />
            </section>
          )}

          {report.store_pages.length > 0 && !run && (
            <section className="rounded-lg border bg-card p-4">
              <h3 className="font-medium">店铺栏目 · {report.store_pages.length} 个</h3>
              <ul className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {report.store_pages.map((page) => (
                    <li key={page.url}>
                      <Button className="h-auto min-h-9 w-full justify-between whitespace-normal py-2 text-left" variant="outline" disabled={loading} onClick={() => openRelated('store', page.url)}>
                        <span className="min-w-0 break-words">
                          <span className="block">{page.title_zh ?? page.title}</span>
                          {page.title_zh && <span className="block text-xs font-normal text-muted-foreground" lang="en">{page.title}</span>}
                        </span>
                        <ArrowRight aria-hidden="true" />
                      </Button>
                    </li>
                ))}
              </ul>
            </section>
          )}

          {report.keyword && (
            <section className="rounded-lg border bg-card p-4">
              <h3 className="font-medium">搜索联想词 · {report.keyword}</h3>
              <p className="mt-1 text-xs text-muted-foreground">来自 Amazon 搜索框，数据仅包含联想词。</p>
              {report.suggestions.length ? (
                <ul className="mt-3 flex flex-wrap gap-2">{report.suggestions.map((word) => <li key={word}><Badge variant="secondary">{word}</Badge></li>)}</ul>
              ) : <p className="mt-3 text-sm text-muted-foreground">当前无可用结果。</p>}
            </section>
          )}
        </article>
      )}
    </div>
  )
}
