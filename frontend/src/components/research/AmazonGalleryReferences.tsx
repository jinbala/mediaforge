'use client'

import { useState } from 'react'
import Image from 'next/image'
import { ArrowRight, ExternalLink, ImageOff } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import {
  AMAZON_IMAGE_ROLES, AMAZON_REFERENCE_DIRECTION_LIMIT, amazonImageRoles, amazonReferenceUrl,
} from '@/lib/amazon-image-references'
import type { AmazonImageReferences, AmazonImageRole } from '@/types/job'

export function AmazonGalleryReferences({ images, busy, references, onChange, onCreate }: {
  images: (string | null | undefined)[]
  busy: boolean
  references: AmazonImageReferences
  onChange: (references: AmazonImageReferences) => void
  onCreate: () => void
}) {
  const [unavailable, setUnavailable] = useState<string[]>([])
  const gallery = new Map<string, string>()
  for (const image of images) {
    const url = amazonReferenceUrl(image)
    if (!url) continue
    const id = new URL(url).pathname.split('/').at(-1)?.split('.')[0] ?? url
    if (!gallery.has(id)) gallery.set(id, url)
  }
  const selected = amazonImageRoles.filter((role) => references[role])

  function assign(imageUrl: string, nextRole: AmazonImageRole | '') {
    const next = { ...references }
    const previous = amazonImageRoles.find((role) => next[role]?.image_url === imageUrl)
    if (previous) delete next[previous]
    // 同一图位只使用一张参考图，改选时直接替换该图位的图片。
    if (nextRole) next[nextRole] = { image_url: imageUrl, direction: AMAZON_IMAGE_ROLES[nextRole].direction }
    onChange(next)
  }

  return (
    <section id="amazon-image-references" className="scroll-mt-4 space-y-4 rounded-lg border bg-card p-4" aria-label="逐张借鉴商品图">
      <div className="space-y-1">
        <h3 className="font-medium">逐张借鉴商品图</h3>
        <p className="text-sm text-muted-foreground">给参考图选择图位，再按实际要借鉴的构图、场景或细节编辑要求。生成时请上传自己的商品原图。</p>
      </div>
      {gallery.size < 2 && <p className="text-sm text-muted-foreground">{gallery.size ? '目前仅有 1 张商品图。' : '目前没有可用商品图。'} 可以重新调研读取公开图组。</p>}
      {gallery.size > 0 && (
        <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          {[...gallery.values()].slice(0, 24).map((url, index) => {
            const role = selected.find((item) => references[item]?.image_url === url) ?? ''
            const failed = unavailable.includes(url)
            return <li key={url} className={`min-w-0 overflow-hidden rounded-md border bg-background ${role ? 'border-primary/70' : ''}`}>
              <a href={url} target="_blank" rel="noreferrer" aria-label={`打开参考原图 ${index + 1}`} className="group relative grid aspect-[4/5] place-items-center bg-white">
                {failed ? <span className="grid justify-items-center gap-1 text-xs text-slate-600"><ImageOff size={18} /> 图片加载失败</span> : (
                  <Image src={url} alt={`Amazon 商品参考图 ${index + 1}`} width={240} height={300} className="h-full w-full object-contain" unoptimized referrerPolicy="no-referrer" onError={() => setUnavailable((current) => [...current, url])} />
                )}
                <ExternalLink className="absolute right-2 top-2 size-3.5 text-slate-700" aria-hidden="true" />
              </a>
              <div className="p-2">
                <select aria-label={`参考图 ${index + 1} 的用途`} value={role} onChange={(event) => assign(url, event.target.value as AmazonImageRole | '')}
                  className="h-8 w-full rounded border bg-background px-1.5 text-xs text-foreground">
                  <option value="">选择用途</option>
                  {amazonImageRoles.map((item) => <option key={item} value={item}>{AMAZON_IMAGE_ROLES[item].label}</option>)}
                </select>
              </div>
            </li>
          })}
        </ul>
      )}
      {selected.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2">
          {selected.map((role) => {
            const reference = references[role]!
            return <label key={role} className="space-y-1.5 text-sm">
              <span>{AMAZON_IMAGE_ROLES[role].label}借鉴要求</span>
              <Textarea className="min-h-16" maxLength={AMAZON_REFERENCE_DIRECTION_LIMIT} value={reference.direction}
                onChange={(event) => onChange({ ...references, [role]: { ...reference, direction: event.target.value } })} />
            </label>
          })}
        </div>
      )}
      {gallery.size > 0 && <Button type="button" disabled={busy || !selected.length} onClick={onCreate}>
        带入图片创作{selected.length ? ` · ${selected.length} 张参考图` : ''} <ArrowRight aria-hidden="true" />
      </Button>}
    </section>
  )
}
