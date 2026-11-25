'use client'

import { useEffect, useRef, useState } from 'react'
import { ClipboardPaste, ExternalLink, Loader2, LogIn, RefreshCw, UploadCloud } from 'lucide-react'
import { ProductImageImport } from '@/components/ProductImageImport'
import { Button } from '@/components/ui/button'
import { api, type ProductLinkPreview } from '@/lib/api'
import { canUseProductBrowser, readProductInBrowser } from '@/lib/product-browser'
import { douyinProductLink, manualProductLink } from '@/lib/product-share'

type ReadResult = { url: string } & ({ preview: ProductLinkPreview } | { error: string })

function imageFile(preview: ProductLinkPreview) {
  const [header, base64] = preview.image_data.split(',', 2)
  const mime = header.slice('data:'.length, -';base64'.length)
  const extension = mime.slice('image/'.length)
  const data = Uint8Array.from(atob(base64), (char) => char.charCodeAt(0))
  return new File([data], `${preview.product_id}.${extension}`, { type: mime })
}

async function readPreview(url: string, platform: string, local: boolean, signal: AbortSignal) {
  if (platform === '拼多多') return readProductInBrowser(url, signal)
  if (!local) return api.previewProductLink(url, signal)
  try {
    return platform === '17网' ? await readProductInBrowser(url, signal) : await api.previewProductLink(url, signal)
  } catch {
    signal.throwIfAborted()
    return platform === '17网' ? api.previewProductLink(url, signal) : readProductInBrowser(url, signal)
  }
}

export function ProductLinkImport({ importing, onImportingChange, onAccepted }: {
  importing: boolean
  onImportingChange: (importing: boolean) => void
  onAccepted: (count: number) => void
}) {
  const [text, setText] = useState('')
  const [readResult, setReadResult] = useState<ReadResult | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [clipboardNotice, setClipboardNotice] = useState('')
  const [error, setError] = useState('')
  const [loggingIn, setLoggingIn] = useState(false)
  const loginRequest = useRef<AbortController | null>(null)
  const field = useRef<HTMLTextAreaElement>(null)
  const manualProduct = manualProductLink(text)
  const platform = manualProduct?.platform || '抖音商城'
  const local = canUseProductBrowser()
  const shareUrl = manualProduct?.url ?? douyinProductLink(text)
  const previewUrl = platform === '拼多多' && !local ? null : shareUrl
  const result = readResult?.url === previewUrl ? readResult : null
  const preview = result && 'preview' in result ? result.preview : null
  const readError = result && 'error' in result ? result.error : null
  const reading = Boolean(previewUrl && !result)
  const loginLabel = platform === '17网' ? '登录 17 网' : platform === '抖音商城' ? '登录抖音' : '登录拼多多'

  useEffect(() => {
    if (!previewUrl) return
    const request = new AbortController()
    const timer = window.setTimeout(async () => {
      try {
        const preview = await readPreview(previewUrl, platform, local, request.signal)
        if (!request.signal.aborted) setReadResult({ url: previewUrl, preview })
      } catch (cause) {
        if (!request.signal.aborted) {
          const error = cause instanceof Error ? cause.message : '商品主图读取失败，请重试。'
          setReadResult({ url: previewUrl, error })
        }
      }
    }, 450)
    return () => {
      window.clearTimeout(timer)
      request.abort()
    }
  }, [previewUrl, attempt, platform, local])

  useEffect(() => () => loginRequest.current?.abort(), [])

  async function loginToPlatform() {
    if (!previewUrl || importing || loginRequest.current) return
    const request = new AbortController()
    loginRequest.current = request
    setLoggingIn(true)
    setError('')
    try {
      const preview = await readProductInBrowser(previewUrl, request.signal, true)
      if (!request.signal.aborted) setReadResult({ url: previewUrl, preview })
    } catch (cause) {
      if (!request.signal.aborted) {
        setReadResult({ url: previewUrl, error: cause instanceof Error ? cause.message : '登录后读取商品失败，请重试。' })
      }
    } finally {
      if (loginRequest.current === request) {
        loginRequest.current = null
        if (!request.signal.aborted) setLoggingIn(false)
      }
    }
  }

  function cancelLogin() {
    loginRequest.current?.abort()
    loginRequest.current = null
    setLoggingIn(false)
  }

  function updateText(value: string) {
    const nextUrl = manualProductLink(value)?.url ?? douyinProductLink(value)
    if (nextUrl !== shareUrl) setReadResult(null)
    setText(value)
    setClipboardNotice('')
    setError('')
  }

  async function pasteShare() {
    const previous = field.current?.value
    let value: string
    try {
      value = await navigator.clipboard.readText()
    } catch {
      value = ''
    }
    const input = field.current
    if (!input || input.disabled || input.value !== previous) return
    input.focus()
    if (value.trim()) updateText(value.slice(0, input.maxLength))
    else setClipboardNotice('请先复制商品分享内容，再按 Ctrl+V 粘贴。')
  }

  async function submit(productId: string, description: string, image: File) {
    if (reading || loggingIn || importing) return
    onImportingChange(true)
    setError('')
    try {
      const quotedDescription = `"${description.replace(/"/g, '""')}"`
      const csv = `product_id,category,style,color,description\r\n${productId},,,,${quotedDescription}\r\n`
      const catalog = new File([csv], 'product-link.csv', { type: 'text/csv' })
      const result = await api.ingestProducts(catalog, new Map([[image, image.name]]), {
        [productId]: image.name,
      })
      onAccepted(result.accepted)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '商品导入失败，请重试。')
    } finally {
      onImportingChange(false)
    }
  }

  return (
    <section className="min-h-0 space-y-4 overflow-y-auto px-5 py-4 sm:px-6">
      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <label htmlFor="product-share-link" className="text-sm font-medium">商品分享链接</label>
          <Button size="sm" variant="outline" disabled={importing || loggingIn} onClick={() => void pasteShare()}>
            <ClipboardPaste aria-hidden="true" />粘贴分享内容
          </Button>
        </div>
        <textarea ref={field} id="product-share-link" rows={2} maxLength={16000} autoFocus
          placeholder="粘贴商品链接或整段分享文案"
          className="block w-full resize-y rounded-md border bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          value={text} disabled={importing || loggingIn} onChange={(event) => updateText(event.currentTarget.value)} />
        {clipboardNotice && <p className="text-sm text-muted-foreground" role="status">{clipboardNotice}</p>}
        {text.trim() && !manualProduct && !previewUrl && (
          <p className="text-sm text-muted-foreground" role="status">请从抖音商城、拼多多或 17 网复制完整的商品链接。</p>
        )}
      </div>
      {reading && (
        <p className="flex items-center gap-2 rounded-md border bg-muted/30 p-3 text-sm text-muted-foreground" role="status">
          <Loader2 className="h-4 w-4 shrink-0 animate-spin text-primary" aria-hidden="true" />正在读取{platform}商品主图…
        </p>
      )}
      {loggingIn && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3">
          <p className="flex min-w-0 flex-1 items-center gap-2 text-sm text-muted-foreground" role="status">
            <Loader2 className="h-4 w-4 shrink-0 animate-spin text-primary" aria-hidden="true" />
            请在打开的{platform}窗口按页面提示登录，主图读取后会显示在这里。
          </p>
          <Button size="sm" variant="outline" onClick={cancelLogin}>取消等待</Button>
        </div>
      )}
      {readError && !loggingIn && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-destructive/40 p-3">
          <p className="min-w-0 flex-1 text-sm text-destructive" role="alert">{readError}</p>
          {local && (
            <Button size="sm" disabled={importing} onClick={() => void loginToPlatform()}>
              <LogIn aria-hidden="true" />{loginLabel}
            </Button>
          )}
          <Button size="sm" variant="outline" disabled={importing} onClick={() => {
            setReadResult(null)
            setAttempt((value) => value + 1)
          }}><RefreshCw aria-hidden="true" />重新读取</Button>
        </div>
      )}
      {manualProduct && !loggingIn && (!previewUrl || readError) && (
        <ProductImageImport key={manualProduct.url} product={manualProduct} importing={importing} onSubmit={submit} />
      )}
      {preview && (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border p-3">
          <img src={preview.image_data} alt="商品主图" className="aspect-square w-28 shrink-0 rounded-md bg-muted object-contain" />
          <div className="min-w-0 flex-1 basis-36 space-y-1">
            <p className="text-xs text-muted-foreground">{platform}</p>
            <p className="break-words text-sm font-medium">{preview.title}</p>
            <p className="break-all font-mono text-xs text-muted-foreground">{preview.product_id}</p>
            {manualProduct && (
              <a href={manualProduct.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-xs text-primary hover:underline">
                <ExternalLink className="h-3 w-3" aria-hidden="true" />打开商品页
              </a>
            )}
          </div>
          <Button className="ml-auto" disabled={importing} onClick={() => {
            const description = manualProduct ? preview.title + '\n' + manualProduct.url : preview.title
            void submit(preview.product_id, description, imageFile(preview))
          }}>
            {importing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <UploadCloud className="mr-2 h-4 w-4" />}
            {importing ? '提交中…' : '添加到商品库'}
          </Button>
        </div>
      )}
      {error && <p className="text-sm text-destructive" role="alert">{error}</p>}
    </section>
  )
}
