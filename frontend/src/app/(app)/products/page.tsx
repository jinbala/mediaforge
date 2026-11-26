'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  AlertTriangle,
  ArrowRight,
  ChevronLeft,
  ChevronRight,
  EyeOff,
  ImageIcon,
  LayoutGrid,
  Loader2,
  RefreshCw,
  RotateCcw,
  Search,
  Sparkles,
  Trash2,
  UploadCloud,
  X,
} from 'lucide-react'

import { api } from '@/lib/api'
import { manualProductLink } from '@/lib/product-share'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { ProductImportDialog } from '@/components/ProductImportDialog'
import { ProductPhotoGallery } from '@/components/ProductPhotoGallery'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

type Product = {
  document_id: string
  sku_code: string
  visibility: 'global' | 'private'
  hidden: boolean
  source_type: string
  category: string
  style: string
  color: string
  material: string
  season: string
  description: string
  sales_count: number
  price: string | null
  image_url: string
  gallery_image_urls?: string[]
  index_status: string
  index_error: string | null
  indexed_at: string | null
  updated_at: string
}

type Summary = { global_count: number; private_count: number; indexing_count: number; failed_count: number }
type Filters = { categories: string[]; styles: string[]; colors: string[] }

const SCOPES = [['all', '全部'], ['global', '公共参考'], ['private', '企业私有'], ['hidden', '已隐藏']] as const
const ACTIONS = {
  delete: { label: '删除商品', verb: '删除', icon: Trash2, description: '该商品将从商品库和参考搜索中移除。' },
  hide: { label: '隐藏商品', verb: '隐藏', icon: EyeOff, description: '该商品将从当前企业的商品库中移除，可以在「已隐藏」中恢复。' },
  restore: { label: '恢复商品', verb: '恢复', icon: RotateCcw, description: '该商品会重新显示在当前企业的公共参考中。' },
}

function productAction(product: Product) {
  if (product.visibility === 'private') return 'delete'
  return product.hidden ? 'restore' : 'hide'
}

const PAGE_SIZE = 24
const EMPTY_SUMMARY: Summary = { global_count: 0, private_count: 0, indexing_count: 0, failed_count: 0 }
const EMPTY_FILTERS: Filters = { categories: [], styles: [], colors: [] }

function indexLabel(status: string) {
  if (status === 'indexed') return '可检索'
  if (status === 'failed') return '索引失败'
  if (status === 'indexing') return '索引中'
  return '等待索引'
}

function ProductDescription({ product }: { product: Product }) {
  const separator = product.description.lastIndexOf('\n')
  const link = separator >= 0 ? manualProductLink(product.description.slice(separator + 1)) : null
  const source = link?.sku === product.sku_code ? link : null
  const description = source ? product.description.slice(0, separator) : product.description
  return (
    <div className="mt-2 space-y-2 text-sm leading-6">
      <p className="break-words">{description || '暂无描述'}</p>
      {source && <a className="inline-block text-primary underline underline-offset-4" href={source.url} target="_blank" rel="noopener noreferrer">打开{source.platform}来源商品</a>}
    </div>
  )
}

export default function ProductsPage() {
  const router = useRouter()
  const [products, setProducts] = useState<Product[]>([])
  const [summary, setSummary] = useState<Summary>(EMPTY_SUMMARY)
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS)
  const [scope, setScope] = useState<typeof SCOPES[number][0]>('all')
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('all')
  const [style, setStyle] = useState('all')
  const [color, setColor] = useState('all')
  const [indexStatus, setIndexStatus] = useState('all')
  const [sort, setSort] = useState('created_desc')
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const loadRequest = useRef(0)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [selectedProduct, setSelectedProduct] = useState<Product | null>(null)
  const [pendingProduct, setPendingProduct] = useState<Product | null>(null)
  const [saving, setSaving] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [importOpen, setImportOpen] = useState(false)

  useEffect(() => {
    const timer = window.setTimeout(() => { setQuery(search.trim()); setPage(1) }, 300)
    return () => window.clearTimeout(timer)
  }, [search])

  const loadProducts = useCallback(async (quiet = false) => {
    const request = ++loadRequest.current
    if (!quiet) setLoading(true)
    setLoadError(null)
    try {
      const params = new URLSearchParams({ scope, page: String(page), page_size: String(PAGE_SIZE), sort })
      if (query) params.set('q', query)
      if (category !== 'all') params.set('category', category)
      if (style !== 'all') params.set('style', style)
      if (color !== 'all') params.set('color', color)
      if (indexStatus !== 'all') params.set('index_status', indexStatus)
      const [list, totals, available] = await Promise.all([
        api.get<{ products: Product[]; total: number }>(`/v1/products?${params.toString()}`),
        api.get<Summary>('/v1/products/summary'),
        api.get<Filters>(`/v1/products/filters?scope=${scope}`),
      ])
      if (request !== loadRequest.current) return
      const lastPage = Math.max(1, Math.ceil(list.total / PAGE_SIZE))
      if (page > lastPage) setPage(lastPage)
      setProducts(list.products); setTotal(list.total); setSummary(totals); setFilters(available)
    } catch (error) {
      if (request === loadRequest.current) setLoadError(error instanceof Error ? error.message : '商品数据加载失败')
    } finally {
      if (request === loadRequest.current) setLoading(false)
    }
  }, [scope, page, query, category, style, color, indexStatus, sort])

  useEffect(() => { void loadProducts() }, [loadProducts])
  useEffect(() => {
    if (summary.indexing_count === 0) return
    const timer = window.setInterval(() => void loadProducts(true), 3000)
    return () => window.clearInterval(timer)
  }, [loadProducts, summary.indexing_count])

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const createWith = (product: Product, purpose: 'source' | 'reference') => {
    const key = purpose === 'source' ? 'sourceSku' : 'refSku'
    router.push(`/batch?${key}=${encodeURIComponent(product.sku_code)}`)
  }

  const pendingAction = pendingProduct ? ACTIONS[productAction(pendingProduct)] : null
  const detailAction = selectedProduct ? ACTIONS[productAction(selectedProduct)] : null
  const DetailActionIcon = detailAction?.icon

  function confirmAction(product: Product) {
    setActionError(null)
    setPendingProduct(product)
  }

  async function saveProductAction() {
    if (!pendingProduct || saving) return
    const { document_id } = pendingProduct
    const action = productAction(pendingProduct)
    const encodedId = encodeURIComponent(document_id)
    setSaving(true)
    setActionError(null)
    try {
      if (action === 'delete') {
        await api.delete<void>(`/v1/products/${encodedId}`)
      } else {
        await api.put<void>(`/v1/products/visibility/${encodedId}`, { hidden: action === 'hide' })
      }
      setSelectedProduct((current) => current?.document_id === document_id ? null : current)
      setPendingProduct(null)
      await loadProducts(true)
    } catch (error) {
      setActionError(error instanceof Error ? error.message : `商品${ACTIONS[action].verb}失败，请重试。`)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="mx-auto max-w-[1480px] space-y-4">
      <header className="flex flex-col gap-4 border-b pb-4 lg:flex-row lg:items-end lg:justify-between">
        <div><p className="text-xs font-semibold uppercase tracking-[0.22em] text-primary">Bestseller library</p><h1 className="mt-1 text-2xl font-semibold tracking-tight">爆款商品库</h1><p className="mt-1 text-sm text-muted-foreground">先看图、再判断，用合适的爆款作为新品视觉参考。</p></div>
        <div className="flex gap-2"><Button variant="outline" onClick={() => void loadProducts()}><RefreshCw className={`mr-2 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />刷新</Button><Button onClick={() => setImportOpen(true)}><UploadCloud className="mr-2 h-4 w-4" />导入商品</Button></div>
      </header>

      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-lg border bg-card/60 px-4 py-3 text-sm">
        <span><b className="tabular-nums">{summary.global_count}</b> 公共参考</span><span className="text-border">/</span><span><b className="tabular-nums">{summary.private_count}</b> 企业私有</span><span className="text-border">/</span><span className={summary.indexing_count ? 'text-amber-500' : 'text-muted-foreground'}>{summary.indexing_count} 正在索引</span>{summary.failed_count > 0 && <button onClick={() => { setIndexStatus('failed'); setPage(1) }} className="ml-auto flex items-center gap-1 text-amber-500"><AlertTriangle className="h-4 w-4" />{summary.failed_count} 个索引失败</button>}
      </div>

      <section className="space-y-3">
        <div className="flex flex-col gap-3 xl:flex-row xl:items-center">
          <div className="relative min-w-0 flex-1"><Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" /><Input value={search} onChange={(event) => setSearch(event.target.value)} className="pl-9" placeholder="搜索 SKU、品类、风格或颜色" /></div>
          <div className="flex flex-wrap gap-2">
            <Select value={category} onValueChange={(value) => { setCategory(value); setPage(1) }}><SelectTrigger className="w-[130px]"><SelectValue placeholder="品类" /></SelectTrigger><SelectContent><SelectItem value="all">全部品类</SelectItem>{filters.categories.map((value) => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select>
            <Select value={style} onValueChange={(value) => { setStyle(value); setPage(1) }}><SelectTrigger className="w-[130px]"><SelectValue placeholder="风格" /></SelectTrigger><SelectContent><SelectItem value="all">全部风格</SelectItem>{filters.styles.map((value) => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select>
            <Select value={color} onValueChange={(value) => { setColor(value); setPage(1) }}><SelectTrigger className="w-[120px]"><SelectValue placeholder="颜色" /></SelectTrigger><SelectContent><SelectItem value="all">全部颜色</SelectItem>{filters.colors.map((value) => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select>
            <Select value={indexStatus} onValueChange={(value) => { setIndexStatus(value); setPage(1) }}><SelectTrigger className="w-[125px]"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">全部状态</SelectItem><SelectItem value="indexed">可检索</SelectItem><SelectItem value="indexing">索引中</SelectItem><SelectItem value="failed">索引失败</SelectItem></SelectContent></Select>
            <Select value={sort} onValueChange={(value) => { setSort(value); setPage(1) }}>
              <SelectTrigger className="w-[130px]" aria-label="商品排序"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="created_desc">最近添加</SelectItem>
                <SelectItem value="sales_desc">销量优先</SelectItem>
                <SelectItem value="updated_desc">最近更新</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {SCOPES.map(([value, label]) => <Button key={value} size="sm" variant={scope === value ? 'default' : 'outline'} onClick={() => { setScope(value); setPage(1) }}>{label}</Button>)}
          <span className="ml-auto flex items-center gap-1 text-xs text-muted-foreground"><LayoutGrid className="h-3.5 w-3.5" />共 {total} 个商品</span>
        </div>
      </section>

      {loadError ? <div className="rounded-lg border border-destructive/40 p-10 text-center text-sm text-destructive">{loadError}</div> : loading ? <div className="flex min-h-80 items-center justify-center"><Loader2 className="h-5 w-5 animate-spin text-primary" /></div> : products.length === 0 ? (
        <div className="rounded-xl border border-dashed p-16 text-center">
          <ImageIcon className="mx-auto h-7 w-7 text-muted-foreground" />
          <p className="mt-3 font-medium">{scope === 'hidden' ? '没有找到已隐藏的商品' : '没有找到匹配的爆款商品'}</p>
          {scope !== 'hidden' && <p className="mt-1 text-sm text-muted-foreground">调整筛选条件，或导入企业自己的爆款参考。</p>}
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6">
          {products.map((product) => {
            const action = productAction(product)
            const { label, icon: ActionIcon } = ACTIONS[action]
            return (
              <div key={product.document_id} className="relative min-w-0 transition hover:-translate-y-0.5">
                <button onClick={() => setSelectedProduct(product)} className="group flex h-full w-full flex-col overflow-hidden rounded-lg border bg-card text-left hover:border-primary/50 hover:shadow-lg hover:shadow-black/10">
                  <div className="relative aspect-[4/5] w-full overflow-hidden bg-muted">{product.image_url ? <img src={product.image_url} alt={product.sku_code} className="h-full w-full object-cover transition duration-300 group-hover:scale-[1.025]" /> : <div className="flex h-full items-center justify-center"><ImageIcon className="h-6 w-6 text-muted-foreground" /></div>}<div className="absolute left-2 top-2 flex gap-1"><Badge variant={product.visibility === 'global' ? 'secondary' : 'outline'} className="bg-background/85 backdrop-blur">{product.visibility === 'global' ? '公共' : '企业'}</Badge>{product.index_status !== 'indexed' && <Badge variant="outline" className="border-amber-500/40 bg-background/85 text-amber-500">{indexLabel(product.index_status)}</Badge>}</div></div>
                  <div className="w-full p-3"><p className="truncate font-mono text-xs font-semibold">{product.sku_code}</p><p className="mt-1 truncate text-xs text-muted-foreground">{[product.category, product.style, product.color].filter(Boolean).join(' · ') || '未填写商品属性'}</p><p className="mt-2 text-xs tabular-nums"><span className="font-medium">{product.sales_count.toLocaleString()}</span><span className="ml-1 text-muted-foreground">销量</span></p></div>
                </button>
                <Button variant="outline" size="icon" className={`absolute right-2 top-2 h-8 w-8 bg-background/85 backdrop-blur ${action === 'delete' ? 'hover:text-destructive' : ''}`}
                  aria-label={`${label} ${product.sku_code}`} title={label} disabled={saving} onClick={() => confirmAction(product)}>
                  <ActionIcon aria-hidden="true" />
                </Button>
              </div>
            )
          })}
        </div>
      )}

      <div className="flex items-center justify-between border-t pt-3 text-sm text-muted-foreground"><span>第 {page} / {pageCount} 页</span><div className="flex gap-2"><Button variant="outline" size="sm" disabled={page <= 1 || loading} onClick={() => setPage((value) => value - 1)}><ChevronLeft className="mr-1 h-4 w-4" />上一页</Button><Button variant="outline" size="sm" disabled={page >= pageCount || loading} onClick={() => setPage((value) => value + 1)}>下一页<ChevronRight className="ml-1 h-4 w-4" /></Button></div></div>

      {selectedProduct && <div className="fixed inset-0 z-50 flex justify-end"><button aria-label="关闭详情" className="absolute inset-0 bg-black/55" onClick={() => setSelectedProduct(null)} /><aside className="relative z-10 flex h-full w-full max-w-md flex-col border-l bg-background shadow-2xl"><div className="flex items-center justify-between border-b p-4"><div><p className="text-xs uppercase tracking-wider text-primary">商品详情</p><h2 className="mt-1 font-mono font-semibold">{selectedProduct.sku_code}</h2></div><Button variant="ghost" size="icon" onClick={() => setSelectedProduct(null)}><X className="h-5 w-5" /></Button></div><div className="min-h-0 flex-1 overflow-y-auto">
        <ProductPhotoGallery key={selectedProduct.document_id} images={[selectedProduct.image_url, ...(selectedProduct.gallery_image_urls ?? [])]} />
        <div className="space-y-5 p-5"><div className="flex flex-wrap gap-2"><Badge>{selectedProduct.visibility === 'global' ? '公共参考' : '企业私有'}</Badge><Badge variant="outline">{indexLabel(selectedProduct.index_status)}</Badge></div><div className="grid grid-cols-2 gap-4 text-sm">{[['品类', selectedProduct.category], ['风格', selectedProduct.style], ['颜色', selectedProduct.color], ['材料', selectedProduct.material], ['季节', selectedProduct.season], ['销量', selectedProduct.sales_count.toLocaleString()]].map(([label, value]) => <div key={label}><p className="text-xs text-muted-foreground">{label}</p><p className="mt-1">{value || '—'}</p></div>)}</div><div><p className="text-xs text-muted-foreground">商品描述</p><ProductDescription product={selectedProduct} /></div>{selectedProduct.index_error && <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"><AlertTriangle className="mb-2 h-4 w-4" />{selectedProduct.index_error}</div>}</div></div>
      <div className="space-y-2 border-t p-4">
        {!selectedProduct.hidden && <>
          <Button className="w-full" onClick={() => createWith(selectedProduct, 'reference')}><Sparkles className="mr-2 h-4 w-4" />以此为爆款参考生成</Button>
          <Button className="w-full" variant="outline" onClick={() => createWith(selectedProduct, 'source')}>作为新品原图生成<ArrowRight className="ml-2 h-4 w-4" /></Button>
          <p className="text-center text-[11px] text-muted-foreground">参考商品只影响构图、灯光与氛围，不会替换新品。</p>
        </>}
        {detailAction && DetailActionIcon && (
          <Button className={`w-full ${selectedProduct.visibility === 'private' ? 'text-destructive hover:text-destructive' : ''}`} variant="ghost" disabled={saving}
            onClick={() => confirmAction(selectedProduct)}>
            <DetailActionIcon aria-hidden="true" />{detailAction.label}
          </Button>
        )}
      </div></aside></div>}

      <Dialog open={Boolean(pendingProduct)} onOpenChange={(open) => {
        if (!open && !saving) setPendingProduct(null)
      }}>
        <DialogContent className="max-w-sm rounded-lg" onEscapeKeyDown={(event) => {
          if (saving) event.preventDefault()
        }}>
          <DialogHeader>
            <DialogTitle>{pendingAction?.label}</DialogTitle>
            <DialogDescription className="break-words">
              确定{pendingAction?.verb} {pendingProduct?.sku_code}？{pendingAction?.description}
            </DialogDescription>
          </DialogHeader>
          {actionError && <p role="alert" className="text-sm text-destructive">{actionError}</p>}
          <DialogFooter className="gap-2">
            <Button variant="outline" disabled={saving} onClick={() => setPendingProduct(null)} autoFocus>取消</Button>
            <Button variant={pendingAction === ACTIONS.delete ? 'destructive' : 'default'} disabled={saving} onClick={() => void saveProductAction()}>
              {saving && <Loader2 className="animate-spin" aria-hidden="true" />}
              {saving ? `${pendingAction?.verb}中…` : `确认${pendingAction?.verb}`}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ProductImportDialog
        open={importOpen}
        onOpenChange={setImportOpen}
        onImported={() => {
          if (page === 1) void loadProducts(true)
          else setPage(1)
        }}
      />
    </div>
  )
}
