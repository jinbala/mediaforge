'use client'

import { useCallback, useRef, useState, type ClipboardEvent, type DragEvent } from 'react'
import { ClipboardPaste, ExternalLink, ImageIcon, Loader2, UploadCloud } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { ManualProductLink } from '@/lib/product-share'

const extensions: Record<string, string> = {
  'image/jpeg': 'jpg',
  'image/png': 'png',
  'image/webp': 'webp',
}

export function ProductImageImport({ product, importing, onSubmit }: {
  product: ManualProductLink
  importing: boolean
  onSubmit: (sku: string, description: string, image: File) => Promise<void>
}) {
  const [title, setTitle] = useState('')
  const [photo, setPhoto] = useState<File | null>(null)
  const [decodedPhoto, setDecodedPhoto] = useState<File | null>(null)
  const [error, setError] = useState('')
  const input = useRef<HTMLInputElement>(null)
  const dropArea = useRef<HTMLDivElement>(null)
  const photoSelection = useRef(0)

  const attachPhoto = useCallback((element: HTMLImageElement | null) => {
    if (!element || !photo) return
    const url = URL.createObjectURL(photo)
    element.src = url
    return () => URL.revokeObjectURL(url)
  }, [photo])

  function selectPhoto(file?: File) {
    if (!file || importing) return
    photoSelection.current += 1
    const extension = extensions[file.type]
    if (!extension || file.size === 0 || file.size > 15 * 1024 * 1024) {
      setError('请选择 15 MB 以内的 JPG、PNG 或 WebP 图片。')
      return
    }
    setError('')
    setPhoto(new File([file], `${product.sku}.${extension}`, { type: file.type }))
  }

  async function pasteFromClipboard() {
    const selection = ++photoSelection.current
    let blob: Blob | undefined
    try {
      const items = await navigator.clipboard.read()
      for (const item of items) {
        const type = item.types.find((type) => Boolean(extensions[type]))
        if (type) {
          blob = await item.getType(type)
          break
        }
      }
    } catch {
      blob = undefined
    }
    if (!dropArea.current || selection !== photoSelection.current) return
    if (blob) {
      selectPhoto(new File([blob], product.sku, { type: blob.type }))
    } else {
      dropArea.current.focus()
      setError('请在商品页复制图片，再回到这里按 Ctrl+V，或选择本地图片。')
    }
  }

  function pastePhoto(event: ClipboardEvent<HTMLElement>) {
    const file = Array.from(event.clipboardData.files).find((item) => item.type.startsWith('image/'))
    if (file) {
      event.preventDefault()
      selectPhoto(file)
    }
  }

  function dropPhoto(event: DragEvent<HTMLElement>) {
    event.preventDefault()
    selectPhoto(event.dataTransfer.files[0])
  }

  return (
    <div className="space-y-3 rounded-lg border p-3" onPaste={pastePhoto}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-medium">{product.platform}商品</p>
        <Button asChild size="sm" variant="ghost">
          <a href={product.url} target="_blank" rel="noopener noreferrer">
            <ExternalLink className="mr-1.5 h-4 w-4" />打开商品页
          </a>
        </Button>
      </div>
      <div ref={dropArea} tabIndex={0} aria-label="粘贴商品图片" className="flex flex-wrap items-center gap-3 rounded-md border border-dashed bg-muted/20 p-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        onDragOver={(event) => event.preventDefault()} onDrop={dropPhoto}>
        {photo && (
          <img ref={attachPhoto} alt={product.platform + '商品主图'} className="h-20 w-20 shrink-0 rounded bg-muted object-contain"
            onLoad={() => setDecodedPhoto(photo)} onError={() => {
              setPhoto(null)
              setError('这张图片无法打开，请重新复制或选择图片。')
            }} />
        )}
        <div className="min-w-0 flex-1 basis-36">
          <p className="text-sm font-medium">{photo ? '商品主图' : '添加商品主图'}</p>
          <p className="mt-1 text-xs text-muted-foreground">复制、拖入或上传图片</p>
        </div>
        <input type="file" ref={input} className="hidden" aria-label={product.platform + '商品图片文件'} accept="image/jpeg,image/png,image/webp"
          onChange={(event) => {
            selectPhoto(event.currentTarget.files?.[0])
            event.currentTarget.value = ''
          }} />
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" disabled={importing} onClick={() => void pasteFromClipboard()}>
            <ClipboardPaste aria-hidden="true" />粘贴图片
          </Button>
          <Button size="sm" variant="ghost" disabled={importing} onClick={() => input.current?.click()}>
            <ImageIcon aria-hidden="true" />{photo ? '换图' : '选择图片'}
          </Button>
        </div>
      </div>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <label className="min-w-0 flex-1 space-y-1.5 text-sm">
          <span>商品名称（选填）</span>
          <input type="text" maxLength={200} value={title} disabled={importing} onChange={(event) => setTitle(event.currentTarget.value)}
            placeholder="例如 高腰阔腿牛仔裤" className="block h-9 w-full rounded-md border bg-background px-3 text-sm" />
        </label>
        <Button disabled={!photo || decodedPhoto !== photo || importing} onClick={() => {
          if (photo) {
            photoSelection.current += 1
            const description = title.trim() || product.platform + '商品'
            void onSubmit(product.sku, description + '\n' + product.url, photo)
          }
        }}>
          {importing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <UploadCloud className="mr-2 h-4 w-4" />}
          {importing ? '提交中…' : '添加到商品库'}
        </Button>
      </div>
      {error && <p className="text-sm text-destructive" role="alert">{error}</p>}
    </div>
  )
}
