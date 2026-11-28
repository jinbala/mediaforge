import type { AmazonImageReferences, AmazonImageRole, OutputType } from '@/types/job'

export const AMAZON_IMAGE_ROLES = {
  main: { label: '主图', direction: '参考商品在画面中的位置和正面机位，使用纯白背景，完整展示自有商品。', output: 'main_image' },
  scene: { label: '场景图', direction: '参考取景、光线和使用姿态，重新搭建场景，用自有商品呈现。', output: 'detail_page' },
  feature: { label: '卖点图', direction: '参考卖点的取景范围和留白，突出自有商品原图可以证实的结构或版型。', output: 'detail_page' },
  closeup: { label: '细节特写', direction: '参考裁切和侧光，对准自有商品可见的纹理、缝线或其他局部。', output: 'detail_page' },
} satisfies Record<AmazonImageRole, { label: string; direction: string; output: OutputType }>

export const amazonImageRoles = Object.keys(AMAZON_IMAGE_ROLES) as AmazonImageRole[]
export const AMAZON_REFERENCE_DIRECTION_LIMIT = 500

export function amazonReferenceUrl(value: unknown): string | null {
  if (typeof value !== 'string' || value.length > 2000) return null
  try {
    const url = new URL(value)
    if (url.protocol === 'https:' && !url.username && !url.password && !url.port
      && ['m.media-amazon.com', 'images-na.ssl-images-amazon.com'].includes(url.hostname)
      && /^\/images\/I\/[^/]+$/.test(url.pathname)) return url.href
  } catch {
    // 历史报告可能保存了无效地址，跳过这张图。
  }
  return null
}

export function cleanAmazonImageReferences(value: unknown): AmazonImageReferences {
  const references: AmazonImageReferences = {}
  if (!value || typeof value !== 'object') return references
  const entries = value as AmazonImageReferences
  for (const role of amazonImageRoles) {
    const entry = entries[role]
    const imageUrl = amazonReferenceUrl(entry?.image_url)
    if (imageUrl) references[role] = {
      image_url: imageUrl,
      direction: typeof entry?.direction === 'string' ? entry.direction.slice(0, AMAZON_REFERENCE_DIRECTION_LIMIT) : '',
    }
  }
  return references
}
