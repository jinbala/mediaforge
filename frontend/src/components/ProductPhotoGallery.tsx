'use client'

import { useState } from 'react'
import { cn } from '@/lib/utils'

export function ProductPhotoGallery({ images, imageClassName }: {
  images: string[]
  imageClassName?: string
}) {
  const [selected, setSelected] = useState<string>()
  const photos = [...new Set(images.filter(Boolean))]
  const active = selected && photos.includes(selected) ? selected : photos[0]

  if (!active) return null

  return (
    <div className="space-y-2">
      <div className={cn('flex aspect-square items-center justify-center overflow-hidden rounded-md bg-muted', imageClassName)}>
        <img src={active} alt={active === photos[0] ? '商品主图' : `商品轮播图 ${photos.indexOf(active) + 1}`} className="h-full w-full object-contain" />
      </div>
      {photos.length > 1 && (
        <div className="flex gap-2 overflow-x-auto px-1 pb-1" role="group" aria-label={`商品图片，共 ${photos.length} 张`}>
          {photos.map((photo, index) => (
            <button key={photo} type="button" aria-pressed={photo === active}
              aria-label={index === 0 ? '查看主图' : `查看第 ${index + 1} 张图`}
              className={cn('size-14 shrink-0 overflow-hidden rounded border-2 bg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary', photo === active ? 'border-primary' : 'border-transparent hover:border-muted-foreground')}
              onClick={() => setSelected(photo)}>
              <img src={photo} alt="" className="h-full w-full object-cover" />
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
