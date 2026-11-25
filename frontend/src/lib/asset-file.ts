const FILE_PREFIX = '/api/v1/files/'

export function assetApiPath(file: string): string | null {
  if (!file.startsWith(FILE_PREFIX)) return null
  const url = new URL(file, 'https://mediaforge.invalid')
  return url.pathname.startsWith(FILE_PREFIX) ? url.pathname.slice('/api'.length) : null
}

export function assetPreviewHref(file: string): string {
  return assetApiPath(file) ? `/asset?${new URLSearchParams({ file })}` : file
}
