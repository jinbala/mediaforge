'use client'

import { useState } from 'react'
import { AlertCircle, Loader2, RotateCcw } from 'lucide-react'

import { RetryUngeneratedSkuButton } from '@/components/JobTable'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Textarea } from '@/components/ui/textarea'
import { api, ApiError } from '@/lib/api'
import { assetPreviewHref } from '@/lib/asset-file'
import { PLATFORM_OPTIONS } from '@/lib/sku-options'
import { assetOutputLabel } from '@/lib/task-assets'
import type { Asset, JobItem } from '@/types/job'

function failureMessage(error: string | null) {
  const text = error?.trim()
  if (!text) return { reason: '未记录具体原因。', issue: '' }

  const jsonStart = text.indexOf('{')
  if (jsonStart >= 0) {
    try {
      const report = JSON.parse(text.slice(jsonStart))
      const reason = typeof report?.reason === 'string' ? report.reason : ''
      const issue = Array.isArray(report?.issues)
        ? report.issues.find((value: unknown) => typeof value === 'string' && value.trim())
        : ''
      if (reason || issue) return { reason: reason || '未通过质检', issue: issue || '' }
    } catch {
      // Older errors may contain plain text or an incomplete provider response.
    }
  }
  return { reason: text, issue: '' }
}

export function TaskFailureSummary({
  jobId, assets, items, disabled, onUpdate,
}: {
  jobId: string
  assets: Asset[]
  items: JobItem[]
  disabled: boolean
  onUpdate: (message?: string) => Promise<void>
}) {
  const [selection, setSelection] = useState<{ ids: string[]; label: string } | null>(null)
  const [instruction, setInstruction] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const failedAssets = assets.filter((asset) => asset.status === 'failed')
  const failedSkus = new Set(items.filter((item) =>
    item.status === 'failed' || item.status === 'partial_fail',
  ).map((item) => item.sku_id))
  const repairable = failedAssets.filter((asset) =>
    failedSkus.has(asset.sku_id) && asset.platform &&
    ['main_image', 'detail_page', 'pdd_white_background'].includes(asset.output_type),
  )
  const repairableIds = new Set(repairable.map((asset) => asset.asset_id))
  const representedSkus = new Set(failedAssets.map((asset) => asset.sku_id))
  const itemFailures = items.filter((item) =>
    (item.status === 'failed' || item.status === 'partial_fail') && !representedSkus.has(item.sku_id),
  )
  const succeeded = items.length
    ? items.reduce((count, item) => count + item.completed_assets, 0)
    : assets.filter((asset) => asset.status === 'success').length
  const failed = items.length
    ? items.reduce((count, item) => count + item.failed_assets, 0)
    : failedAssets.length
  const rows = [
    ...failedAssets.map((asset) => {
      const platform = asset.platform?.split(':')[0]
      const platformLabel = PLATFORM_OPTIONS.find((option) => option.value === platform)?.label || platform
      return {
        id: asset.asset_id,
        label: [assetOutputLabel(asset), `SKU ${asset.sku_id}`, platformLabel].filter(Boolean).join(' · '),
        href: asset.file_path ? assetPreviewHref(asset.file_path) : null,
        ...failureMessage(asset.error_msg),
      }
    }),
    ...itemFailures.map((item) => ({ id: item.sku_id, label: `SKU ${item.sku_id}`, href: null, ...failureMessage(item.error_msg) })),
  ]

  const openRepair = (ids: string[], label: string) => {
    setInstruction('')
    setError(null)
    setSelection({ ids, label })
  }
  const submitRepair = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!selection || disabled || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      const result = await api.post<{ queued_images: number; unqueued_images: number }>(
        `/v1/tasks/${jobId}/repair-images`,
        { asset_ids: selection.ids, instruction: instruction.trim() },
      )
      setSelection(null)
      const message = result.unqueued_images
        ? `已提交 ${result.queued_images} 张，另有 ${result.unqueued_images} 张提交失败，可稍后重试。`
        : `已提交 ${result.queued_images} 张补生成，完成后会更新这里的结果。`
      await onUpdate(message)
    } catch (err) {
      setError(err instanceof Error ? err.message : '提交补生成失败')
      if (err instanceof ApiError && err.status === 409) await onUpdate()
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Card id="failure-actions" className="scroll-mt-4 border-destructive/35">
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3 space-y-0">
        <div className="space-y-1">
          <CardTitle className="flex items-center gap-2 text-lg">
            <AlertCircle aria-hidden="true" className="h-5 w-5 text-destructive" />处理失败
          </CardTitle>
          {succeeded + failed > 0 && <p className="text-sm text-muted-foreground">成功 {succeeded} · 失败 {failed}</p>}
        </div>
        <div className="flex flex-wrap items-start gap-2">
          {(!items.length || itemFailures.some((item) => !assets.some((asset) => asset.sku_id === item.sku_id))) && (
            <RetryUngeneratedSkuButton jobId={jobId} disabled={disabled || submitting} legacy={!items.length} />
          )}
          {repairable.length > 1 && (
            <Button size="sm" disabled={disabled || submitting} onClick={() =>
              openRepair(repairable.map((asset) => asset.asset_id), `补生成 ${repairable.length} 张失败图`)
            }>
              <RotateCcw className="mr-2 h-4 w-4" />补生成全部失败图（{repairable.length}）
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {rows.length ? (
          <ul className="divide-y rounded-md border bg-muted/20">
            {rows.map(({ id, label, href, reason, issue }) => (
              <li key={id} className="flex flex-wrap items-start justify-between gap-3 p-3 text-sm">
                <div className="min-w-0 flex-1 space-y-1">
                  <p className="break-words font-medium">{label}</p>
                  <p className="whitespace-pre-wrap break-words text-destructive">{reason}</p>
                  {issue && <p className="break-words text-muted-foreground">{issue}</p>}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {href && <Button asChild variant="ghost" size="sm">
                    <a href={href} target="_blank" rel="noreferrer">查看失败图</a>
                  </Button>}
                  {repairableIds.has(id) && <Button
                    variant="outline"
                    size="sm"
                    aria-label={`补生成 ${label}`}
                    disabled={disabled || submitting}
                    onClick={() => openRepair([id], `补生成 ${label}`)}
                  >补生成</Button>}
                </div>
              </li>
            ))}
          </ul>
        ) : <p className="text-sm text-muted-foreground">这项任务没有保存具体的失败原因。</p>}
      </CardContent>
      <Dialog open={selection !== null} onOpenChange={(open) => { if (!open && !submitting) setSelection(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{selection?.label}</DialogTitle>
            <DialogDescription>已通过质检的图片会保留，补生成结果会回到此任务。</DialogDescription>
          </DialogHeader>
          <form onSubmit={submitRepair} className="space-y-4">
            <div className="space-y-2">
              <label className="text-sm font-medium" htmlFor="repair-instruction">补充要求（选填）</label>
              <Textarea
                id="repair-instruction"
                value={instruction}
                maxLength={500}
                disabled={submitting}
                onChange={(event) => setInstruction(event.target.value)}
                placeholder="例如：镜头拉近，只展示束脚裤脚口与走线"
              />
            </div>
            {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
            <DialogFooter>
              <Button type="button" variant="outline" disabled={submitting} onClick={() => setSelection(null)}>取消</Button>
              <Button type="submit" disabled={disabled || submitting}>
                {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}确认补生成
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </Card>
  )
}
