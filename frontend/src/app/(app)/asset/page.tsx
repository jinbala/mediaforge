'use client'

import { Suspense, useEffect, useState } from 'react'
import { useSearchParams } from 'next/navigation'

import { Loading } from '@/components/Loading'
import { Button } from '@/components/ui/button'
import { api, ApiError } from '@/lib/api'
import { assetApiPath } from '@/lib/asset-file'

function AssetPreview({ path }: { path: string }) {
  const [media, setMedia] = useState<{ url: string; type: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const name = path.split('/').pop() || '素材'

  useEffect(() => {
    const controller = new AbortController()
    let url: string | undefined
    api.getFile(path, controller.signal).then((blob) => {
      if (controller.signal.aborted) return
      if (!blob.type.startsWith('image/') && !blob.type.startsWith('video/')) {
        setError('该素材格式无法预览')
        return
      }
      url = URL.createObjectURL(blob)
      setMedia({ url, type: blob.type })
    }).catch((err: unknown) => {
      if (controller.signal.aborted) return
      setError(err instanceof ApiError && err.status === 401
        ? '登录已过期，请重新登录'
        : '无法加载素材，请返回任务详情后重试')
    })
    return () => {
      controller.abort()
      if (url) URL.revokeObjectURL(url)
    }
  }, [path])

  if (error) return <p role="alert" className="text-destructive">{error}</p>
  if (!media) return <Loading />

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <h1 className="truncate text-xl font-semibold">素材预览</h1>
        <Button variant="outline" asChild><a href={media.url} download={name}>下载</a></Button>
      </div>
      <div className="flex justify-center rounded-lg bg-muted p-4">
        {media.type.startsWith('video/')
          ? <video src={media.url} controls className="max-h-[75vh] max-w-full" />
          : <img src={media.url} alt="生成素材" className="max-h-[75vh] max-w-full object-contain" />}
      </div>
    </div>
  )
}

function AssetView() {
  const searchParams = useSearchParams()
  const path = assetApiPath(searchParams.get('file') ?? '')
  if (!path) return <p role="alert" className="text-destructive">素材链接无效</p>
  return <AssetPreview key={path} path={path} />
}

export default function AssetPage() {
  return <Suspense fallback={<Loading />}><AssetView /></Suspense>
}
