import type { Asset } from '@/types/job'

export const OUTPUT_TYPE_LABEL: Record<string, string> = {
  main_image: '主图',
  detail_page: '详情图',
  pdd_white_background: '拼多多白底图',
  video: '视频',
}

const DETAIL_LABEL: Record<string, string> = {
  scene: '场景图',
  feature: '卖点图',
  closeup: '细节特写',
}

export function assetOutputLabel(asset: Asset) {
  const role = asset.platform?.split(':')[1]
  return (asset.output_type === 'detail_page' && role && DETAIL_LABEL[role])
    || OUTPUT_TYPE_LABEL[asset.output_type] || asset.output_type.replaceAll('_', ' ')
}

export function resolveTaskAssets(assets: Asset[]) {
  const results = new Map<string, Asset>()
  const attempts: Asset[] = []
  for (const asset of assets) {
    const key = JSON.stringify([asset.sku_id, asset.output_type, asset.platform])
    const previous = results.get(key)
    if (!previous) {
      results.set(key, asset)
    } else if (asset.status === 'success' || (
      previous.status !== 'success' && Date.parse(asset.created_at) >= Date.parse(previous.created_at)
    )) {
      results.set(key, asset)
      attempts.push(previous)
    } else {
      attempts.push(asset)
    }
  }
  return { results: [...results.values()], attempts }
}
