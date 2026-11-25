'use client'

import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from 'react'
import { AlertTriangle, Check, CheckCircle2, Download, FileSpreadsheet, FolderOpen, ImageIcon, Link2, Loader2, Plus, UploadCloud } from 'lucide-react'
import { ProductLinkImport } from '@/components/ProductLinkImport'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { api, type ProductImportPreview } from '@/lib/api'
import { cn } from '@/lib/utils'

const IMAGE_ACCEPT = 'image/*,.jpg,.jpeg,.png,.webp'

function fileKey(file: File) {
  return JSON.stringify([file.webkitRelativePath || file.name, file.size, file.lastModified])
}

function isImage(file: File) {
  return file.size > 0 && (file.type.startsWith('image/') || /\.(jpe?g|png|webp|gif|bmp|avif)$/i.test(file.name))
}

function imageStem(name: string) {
  return name.replace(/\.[^.]+$/, '').toLowerCase()
}

function LocalThumbnail({ file }: { file: File }) {
  const attachImage = useCallback((image: HTMLImageElement | null) => {
    if (!image) return
    const objectUrl = URL.createObjectURL(file)
    image.src = objectUrl
    return () => URL.revokeObjectURL(objectUrl)
  }, [file])
  return <img ref={attachImage} alt="" loading="lazy" className="h-9 w-9 shrink-0 rounded object-cover" />
}

export function ProductImportDialog({ open, onOpenChange, onImported }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onImported: () => void
}) {
  const catalogInput = useRef<HTMLInputElement>(null)
  const imagesInput = useRef<HTMLInputElement>(null)
  const folderInput = useRef<HTMLInputElement>(null)
  const manualInput = useRef<HTMLInputElement>(null)
  const previewRequest = useRef<AbortController | null>(null)
  const pendingManualSku = useRef<string | null>(null)

  const [mode, setMode] = useState<'table' | 'link'>('link')
  const [catalog, setCatalog] = useState<File | null>(null)
  const [preview, setPreview] = useState<ProductImportPreview | null>(null)
  const [images, setImages] = useState<File[]>([])
  const [overrides, setOverrides] = useState<Map<string, File>>(() => new Map())
  const [editingSku, setEditingSku] = useState<string | null>(null)
  const [issuesOnly, setIssuesOnly] = useState(false)
  const [dragging, setDragging] = useState<'catalog' | 'images' | null>(null)
  const [reading, setReading] = useState(false)
  const [importing, setImporting] = useState(false)
  const [accepted, setAccepted] = useState<number | null>(null)
  const [catalogError, setCatalogError] = useState('')
  const [imageNotice, setImageNotice] = useState('')
  const [importError, setImportError] = useState('')

  useEffect(() => () => previewRequest.current?.abort(), [])

  const rows = useMemo(() => {
    const byName = new Map<string, File[]>()
    const bySku = new Map<string, File[]>()
    for (const file of images) {
      const name = file.name.toLowerCase()
      const stem = imageStem(name)
      byName.set(name, [...(byName.get(name) || []), file])
      bySku.set(stem, [...(bySku.get(stem) || []), file])
    }
    return (preview?.rows || []).map((row) => {
      const override = overrides.get(row.product_id)
      const remote = row.has_image_url && !override
      const candidates = row.image_ref
        ? byName.get(row.image_name) || []
        : bySku.get(row.product_id.toLowerCase()) || []
      const file = override || (candidates.length === 1 && !remote ? candidates[0] : null)
      const status = override ? '手动指定' : remote ? '图片链接' : file ? '已匹配' : candidates.length > 1 ? '多张同名，请选图' : '缺图'
      return { ...row, file, ready: Boolean(file || remote), status }
    })
  }, [images, overrides, preview])

  const issues = rows.filter((row) => !row.ready)
  const matched = rows.length - issues.length
  const unusedImages = images.length - new Set(rows.flatMap((row) => row.file ? [row.file] : [])).size
  const canImport = Boolean(catalog && rows.length && !issues.length && !reading && !importing)

  async function readCatalog(file?: File) {
    if (!file) return
    previewRequest.current?.abort()
    setPreview(null)
    setCatalog(file)
    setCatalogError('')
    setImportError('')
    setEditingSku(null)
    setOverrides(new Map())
    setIssuesOnly(false)
    if (!/\.(csv|xlsx)$/i.test(file.name)) {
      setCatalogError(/\.xls$/i.test(file.name) ? '旧版 .xls 请另存为 .xlsx 或 CSV 后再导入。' : '请选择 .xlsx 或 .csv 表格。')
      setReading(false)
      return
    }

    const controller = new AbortController()
    previewRequest.current = controller
    setReading(true)
    try {
      const result = await api.previewProductImport(file, controller.signal)
      if (!controller.signal.aborted) setPreview(result)
    } catch (error) {
      if (!controller.signal.aborted) setCatalogError(error instanceof Error ? error.message : '表格读取失败，请重新选择。')
    } finally {
      if (!controller.signal.aborted) setReading(false)
    }
  }

  function addImages(files: File[]) {
    const valid = files.filter(isImage)
    const ignored = files.length - valid.length
    setImageNotice(ignored ? `已忽略 ${ignored} 个非图片或空文件。` : '')
    if (!valid.length) return
    setImages((previous) => {
      const next = new Map(previous.map((file) => [fileKey(file), file]))
      for (const file of valid) {
        const key = fileKey(file)
        if (!next.has(key)) next.set(key, file)
      }
      return Array.from(next.values())
    })
    setImportError('')
  }

  function setOverride(sku: string, file?: File) {
    setOverrides((previous) => {
      const next = new Map(previous)
      if (file) next.set(sku, file)
      else next.delete(sku)
      return next
    })
    setEditingSku(null)
    setImportError('')
  }

  function dropFiles(event: DragEvent, zone: 'catalog' | 'images') {
    event.preventDefault()
    setDragging(null)
    if (importing) return
    if (zone === 'catalog') {
      if (event.dataTransfer.files.length > 1) {
        setCatalogError('每次请选择一份商品表格。')
        return
      }
      void readCatalog(event.dataTransfer.files[0])
    } else addImages(Array.from(event.dataTransfer.files))
  }

  function dropZone(zone: 'catalog' | 'images') {
    return {
      onDragOver: (event: DragEvent) => { event.preventDefault(); setDragging(zone) },
      onDragLeave: (event: DragEvent) => {
        if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) setDragging(null)
      },
      onDrop: (event: DragEvent) => dropFiles(event, zone),
      className: cn('min-w-0 rounded-lg border border-dashed bg-card/50 p-4', dragging === zone && 'border-primary bg-primary/10'),
    }
  }

  async function submit() {
    if (!catalog || !canImport) return
    const uploads = new Map<File, string>()
    const assignments: [string, string][] = []
    for (const row of rows) {
      if (!row.file) continue
      let uploadName = uploads.get(row.file)
      if (!uploadName) {
        const extension = /\.[a-z0-9]+$/i.exec(row.file.name)?.[0] || '.jpg'
        uploadName = `image-${uploads.size + 1}${extension}`
        uploads.set(row.file, uploadName)
      }
      assignments.push([row.product_id, uploadName])
    }

    setImporting(true)
    setImportError('')
    try {
      const result = await api.ingestProducts(catalog, uploads, Object.fromEntries(assignments))
      setAccepted(result.accepted)
      setCatalog(null)
      setPreview(null)
      setImages([])
      setOverrides(new Map())
      setEditingSku(null)
      setIssuesOnly(false)
      setImageNotice('')
      setCatalogError('')
      onImported()
    } catch (error) {
      setImportError(error instanceof Error ? error.message : '导入失败，请重新提交。')
    } finally {
      setImporting(false)
    }
  }

  function changeOpen(next: boolean) {
    if (importing) return
    if (!next) {
      setAccepted(null)
      setDragging(null)
    }
    onOpenChange(next)
  }

  return (
    <Dialog open={open} onOpenChange={changeOpen}>
      <DialogContent className="flex max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] max-w-3xl flex-col gap-0 overflow-hidden rounded-lg p-0 transition-none">
        <DialogHeader className="shrink-0 border-b px-5 py-4 pr-12 text-left sm:px-6">
          <DialogTitle>导入企业商品</DialogTitle>
          <DialogDescription>
            {mode === 'link' ? '粘贴拼多多、抖音商城或 17 网链接，自动读取商品主图。' : '上传商品表格，核对每个 SKU 的图片后提交。'}
          </DialogDescription>
          {accepted === null && (
            <div className="flex flex-wrap gap-2 pt-2" aria-label="导入方式" role="group">
              <Button size="sm" variant={mode === 'link' ? 'secondary' : 'ghost'} aria-pressed={mode === 'link'} disabled={importing} onClick={() => setMode('link')}>
                <Link2 className="mr-1.5 h-4 w-4" />链接导入
              </Button>
              <Button size="sm" variant={mode === 'table' ? 'secondary' : 'ghost'} aria-pressed={mode === 'table'} disabled={importing} onClick={() => setMode('table')}>
                <FileSpreadsheet className="mr-1.5 h-4 w-4" />表格导入
              </Button>
            </div>
          )}
        </DialogHeader>

        {accepted !== null ? (
          <div className="px-6 py-10 text-center" role="status">
            <CheckCircle2 className="mx-auto h-9 w-9 text-emerald-500" />
            <p className="mt-3 text-lg font-semibold">已接收 {accepted} 个商品</p>
            <p className="mt-1 text-sm text-muted-foreground">后台正在保存图片并建立索引，进度可在商品库查看。</p>
          </div>
        ) : mode === 'link' ? (
          <ProductLinkImport importing={importing} onImportingChange={setImporting} onAccepted={(count) => {
            setAccepted(count)
            onImported()
          }} />
        ) : (
          <div className="min-h-0 space-y-4 overflow-y-auto px-5 py-4 sm:px-6">
            <div className="grid gap-3 sm:grid-cols-2">
              <div {...dropZone('catalog')}>
                <div className="flex items-center gap-2 text-sm font-medium">
                  <FileSpreadsheet className="h-4 w-4 text-primary" />商品表格
                </div>
                <p className="mt-1 text-xs text-muted-foreground">拖入一份 Excel（.xlsx）或 CSV。</p>
                <input ref={catalogInput} type="file" accept=".xlsx,.csv" className="hidden" aria-label="商品表格文件" onChange={(event) => {
                  const file = event.currentTarget.files?.[0]
                  event.currentTarget.value = ''
                  void readCatalog(file)
                }} />
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button variant="outline" size="sm" disabled={importing} onClick={() => catalogInput.current?.click()}>
                    {catalog ? '更换表格' : '选择表格'}
                  </Button>
                  <Button asChild variant="ghost" size="sm">
                    <a href="/templates/mediaforge-product-import-template.xlsx" download><Download className="mr-1 h-3.5 w-3.5" />Excel 模板</a>
                  </Button>
                  <Button asChild variant="ghost" size="sm">
                    <a href="/templates/mediaforge-product-import-template.csv" download>CSV 模板</a>
                  </Button>
                </div>
                {catalog && <p title={catalog.name} className="mt-2 truncate text-xs">{catalog.name}</p>}
                {reading && <p className="mt-2 flex items-center gap-2 text-xs text-muted-foreground" role="status"><Loader2 className="h-3.5 w-3.5 animate-spin" />正在读取表格</p>}
                {catalogError && <p className="mt-2 text-xs text-destructive" role="alert">{catalogError}</p>}
              </div>

              <div {...dropZone('images')}>
                <div className="flex items-center gap-2 text-sm font-medium"><ImageIcon className="h-4 w-4 text-primary" />商品图片</div>
                <p className="mt-1 text-xs text-muted-foreground">可拖入多张图片。表格没填文件名时，用 SKU 同名图片匹配。</p>
                <input ref={imagesInput} type="file" multiple accept={IMAGE_ACCEPT} className="hidden" aria-label="商品图片文件" onChange={(event) => {
                  addImages(Array.from(event.currentTarget.files || []))
                  event.currentTarget.value = ''
                }} />
                <input ref={(node) => { folderInput.current = node; node?.setAttribute('webkitdirectory', '') }} type="file" multiple className="hidden" aria-label="商品图片文件夹" onChange={(event) => {
                  addImages(Array.from(event.currentTarget.files || []))
                  event.currentTarget.value = ''
                }} />
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button variant="outline" size="sm" disabled={importing} onClick={() => imagesInput.current?.click()}><Plus className="mr-1 h-4 w-4" />{images.length ? '追加图片' : '选择图片'}</Button>
                  <Button variant="outline" size="sm" disabled={importing} onClick={() => folderInput.current?.click()}><FolderOpen className="mr-1.5 h-4 w-4" />选择文件夹</Button>
                </div>
                <div className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
                  <span>已选 {images.length} 张{rows.length > 0 && unusedImages > 0 ? ` · ${unusedImages} 张未使用` : ''}</span>
                  {images.length > 0 && <button type="button" className="underline underline-offset-2 hover:text-foreground" disabled={importing} onClick={() => {
                    setImages([]); setOverrides(new Map()); setImageNotice(''); setImportError('')
                  }}>清空图片</button>}
                </div>
                {imageNotice && <p className="mt-2 text-xs text-amber-500" role="status">{imageNotice}</p>}
              </div>
            </div>

            {rows.length > 0 && (
              <section aria-label="图片匹配预览" className="overflow-hidden rounded-lg border">
                <div className="flex flex-wrap items-center justify-between gap-2 border-b bg-muted/30 px-3 py-2">
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs sm:text-sm" aria-live="polite">
                    <span className="font-medium">{rows.length} 个商品</span>
                    <span className="text-emerald-500">已就绪 {matched}</span>
                    <span className={issues.length ? 'text-amber-500' : 'text-muted-foreground'}>待补图 {issues.length}</span>
                  </div>
                  <label className="flex cursor-pointer items-center gap-2 text-xs text-muted-foreground">
                    <input type="checkbox" className="accent-primary" checked={issuesOnly} onChange={(event) => setIssuesOnly(event.currentTarget.checked)} />只看待补图
                  </label>
                </div>
                <input ref={manualInput} type="file" accept={IMAGE_ACCEPT} className="hidden" aria-label="指定 SKU 图片" onChange={(event) => {
                  const file = event.currentTarget.files?.[0]
                  const sku = pendingManualSku.current
                  event.currentTarget.value = ''
                  if (file && sku) {
                    const existing = images.find((item) => fileKey(item) === fileKey(file))
                    addImages([file])
                    if (isImage(file)) setOverride(sku, existing || file)
                  }
                }} />
                <ul className="max-h-[min(40dvh,340px)] divide-y overflow-y-auto" aria-label="商品匹配结果">
                  {(issuesOnly ? issues : [...issues, ...rows.filter((row) => row.ready)]).map((row) => (
                    <li key={row.product_id} className="px-3 py-2">
                      <div className="flex items-center gap-2 sm:gap-3">
                        {row.file ? <LocalThumbnail file={row.file} /> : <span className="grid h-9 w-9 shrink-0 place-items-center rounded bg-muted text-muted-foreground"><ImageIcon className="h-4 w-4" /></span>}
                        <div className="min-w-0 flex-1">
                          <p title={row.product_id} className="truncate font-mono text-xs font-medium">{row.product_id}</p>
                          <p title={row.file?.name || row.image_ref} className="truncate text-xs text-muted-foreground">{row.file?.name || row.image_ref || `查找 ${row.product_id} 同名图片`}</p>
                        </div>
                        <span className={cn('flex max-w-24 items-center gap-1 text-xs sm:max-w-none', row.ready ? 'text-emerald-500' : 'text-amber-500')}>
                          {row.ready && <Check className="hidden h-3.5 w-3.5 sm:block" />}{row.status}
                        </span>
                        <Button variant="ghost" size="sm" className="shrink-0" disabled={importing} aria-label={`${row.ready ? '换图' : '补图'} ${row.product_id}`} aria-expanded={editingSku === row.product_id} onClick={() => setEditingSku(editingSku === row.product_id ? null : row.product_id)}>
                          {row.ready ? '换图' : '补图'}
                        </Button>
                      </div>
                      {editingSku === row.product_id && (
                        <div className="mt-2 flex flex-wrap items-center gap-2 pl-11 sm:pl-12">
                          {images.length > 0 && (
                            <select aria-label={`从已选图片中为 ${row.product_id} 选图`} value="" className="h-8 min-w-0 max-w-full rounded-md border bg-background px-2 text-xs" disabled={importing} onChange={(event) => {
                              if (event.currentTarget.value !== '') setOverride(row.product_id, images[Number(event.currentTarget.value)])
                            }}>
                              <option value="" disabled>从已选图片中选</option>
                              {images.map((file, index) => <option key={fileKey(file)} value={index}>{file.webkitRelativePath || file.name}</option>)}
                            </select>
                          )}
                          <Button variant="outline" size="sm" disabled={importing} onClick={() => {
                            pendingManualSku.current = row.product_id
                            manualInput.current?.click()
                          }}>从电脑选图</Button>
                          {overrides.has(row.product_id) && <Button variant="ghost" size="sm" disabled={importing} onClick={() => setOverride(row.product_id)}>恢复自动匹配</Button>}
                        </div>
                      )}
                    </li>
                  ))}
                  {issuesOnly && !issues.length && <li className="px-3 py-4 text-center text-sm text-muted-foreground">所有商品都已就绪。</li>}
                </ul>
              </section>
            )}

            {!!preview?.skipped && <p className="text-xs text-amber-500" role="status">有 {preview.skipped} 行 SKU 为空或重复，提交时会跳过。重复 SKU 保留第一行。</p>}
            {importError && <p className="flex items-start gap-2 text-sm text-destructive" role="alert"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />{importError}</p>}
          </div>
        )}

        <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-t px-5 py-3 sm:px-6">
          {accepted !== null ? (
            <div className="ml-auto flex gap-2">
              <Button variant="outline" onClick={() => changeOpen(false)}>完成</Button>
              <Button onClick={() => setAccepted(null)}>继续导入</Button>
            </div>
          )
            : mode === 'link' ? <Button className="ml-auto" variant="outline" disabled={importing} onClick={() => changeOpen(false)}>关闭</Button> : (
            <>
              <p className="text-xs text-muted-foreground" aria-live="polite">
                {importing ? '正在提交表格和已匹配的图片…' : issues.length ? `还有 ${issues.length} 个商品需要补图。` : rows.length ? `可以导入 ${matched} 个商品。` : '请选择商品表格开始。'}
              </p>
              <div className="ml-auto flex gap-2">
                <Button variant="outline" disabled={importing} onClick={() => changeOpen(false)}>关闭</Button>
                <Button disabled={!canImport} onClick={() => void submit()}>
                  {importing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <UploadCloud className="mr-2 h-4 w-4" />}
                  {importing ? '提交中' : rows.length && !issues.length ? `导入 ${matched} 个商品` : '确认导入'}
                </Button>
              </div>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
