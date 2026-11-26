'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { Loader2, RotateCcw, Trash2 } from 'lucide-react'

import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { api, ApiError } from '@/lib/api'
import { PLATFORM_OPTIONS } from '@/lib/sku-options'
import type { Job, JobStatus } from '@/types/job'

const STATUS_LABELS: Record<JobStatus, string> = {
  pending: '等待中',
  running: '运行中',
  done: '已完成',
  partial_fail: '部分失败',
  failed: '失败',
}

const STATUS_VARIANTS: Record<JobStatus, 'default' | 'secondary' | 'destructive' | 'outline'> = {
  pending: 'secondary',
  running: 'default',
  done: 'outline',
  partial_fail: 'destructive',
  failed: 'destructive',
}

const TERMINAL: ReadonlySet<JobStatus> = new Set<JobStatus>([
  'done',
  'failed',
  'partial_fail',
])

export function RetryUngeneratedSkuButton({ jobId, disabled, legacy = false }: {
  jobId: string
  disabled?: boolean
  legacy?: boolean
}) {
  const router = useRouter()
  const [isRetrying, setIsRetrying] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const retry = async () => {
    setIsRetrying(true)
    setError(null)
    try {
      const job = await api.post<{ job_id: string }>(`/v1/tasks/${jobId}/retry`, { without_assets_only: !legacy })
      router.push(`/tasks?jobId=${job.job_id}`)
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) return
      setError(err instanceof Error ? err.message : '重试提交失败')
    } finally {
      setIsRetrying(false)
    }
  }

  return (
    <div className="space-y-1">
      <Button type="button" variant="outline" size="sm" onClick={retry} disabled={disabled || isRetrying}>
        {isRetrying ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RotateCcw className="mr-2 h-4 w-4" />}
        {isRetrying ? '提交中' : legacy ? '重试失败商品' : '重试未出图商品'}
      </Button>
      {error && <p role="alert" className="max-w-60 text-xs text-destructive">{error}</p>}
    </div>
  )
}

export function JobTable({
  jobs,
  onDelete,
}: {
  jobs: Job[]
  onDelete?: (jobId: string) => Promise<void> | void
}) {
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const handleDelete = async (e: React.MouseEvent, jobId: string) => {
    e.preventDefault()
    e.stopPropagation()
    if (!onDelete) return
    if (!window.confirm('确定要删除这个任务吗？相关素材也会一并删除。')) return
    setDeletingId(jobId)
    try {
      await onDelete(jobId)
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>任务 ID</TableHead>
          <TableHead className="min-w-28">平台</TableHead>
          <TableHead>状态</TableHead>
          <TableHead>已处理 SKU</TableHead>
          <TableHead>创建时间</TableHead>
          <TableHead className="w-36 text-right">操作</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {jobs.map((job) => {
          const { status } = job
          const canDelete = TERMINAL.has(status)
          const hasFailures = status === 'failed' || status === 'partial_fail'
          const detailHref = `/tasks?jobId=${job.job_id}`
          const statusBadge = (
            <Badge variant={STATUS_VARIANTS[status] ?? 'secondary'}>
              {STATUS_LABELS[status] ?? status}
            </Badge>
          )
          const platforms = job.platforms?.map((platform) => (
            PLATFORM_OPTIONS.find((option) => option.value === platform)?.label ?? platform
          ))
          return (
            <TableRow key={job.job_id}>
              <TableCell>
                <Link
                  href={detailHref}
                  className="font-mono text-xs text-primary hover:underline"
                >
                  {job.job_id}
                </Link>
              </TableCell>
              <TableCell className="max-w-64">
                {platforms?.join('、') || '—'}
              </TableCell>
              <TableCell>
                {hasFailures ? (
                  <Link href={detailHref} title="处理失败" aria-label={`${STATUS_LABELS[status]}，处理失败`} className="inline-flex rounded-full hover:opacity-85 focus-visible:outline focus-visible:outline-primary">
                    {statusBadge}
                  </Link>
                ) : statusBadge}
              </TableCell>
              <TableCell>
                {job.done_skus}/{job.total_skus}
              </TableCell>
              <TableCell>{job.created_at ? new Date(job.created_at).toLocaleString() : '—'}</TableCell>
              <TableCell>
                <div className="flex items-start justify-end gap-2">
                  {hasFailures && (
                    <Button asChild variant="outline" size="sm">
                      <Link href={detailHref}>处理失败</Link>
                    </Button>
                  )}
                  {onDelete && canDelete && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-8 w-8 p-0 text-muted-foreground hover:text-destructive"
                      onClick={(e) => handleDelete(e, job.job_id)}
                      disabled={deletingId === job.job_id}
                      title="删除任务"
                      aria-label="删除任务"
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  )}
                </div>
              </TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}
