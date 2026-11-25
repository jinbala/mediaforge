import { mkdir, writeFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { createServer, type ServerResponse } from 'node:http'
import { join } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { chromium, type BrowserContext, type Frame } from 'playwright-core'
import {
  isLoginPage,
  isPlatformPage,
  isProductPage,
  productLink,
  readGallery,
  rememberProductPage,
  type ProductLink,
} from './platforms.cjs'

const port = 8317
const host = '127.0.0.1'
const origins = new Set(['http://127.0.0.1:3101', 'http://localhost:3101', 'http://127.0.0.1:3100', 'http://localhost:3100'])
const localData = process.env.LOCALAPPDATA
if (!localData) throw new Error('LOCALAPPDATA is required to locate the MediaForge browser profile.')
const profile = join(localData, 'MediaForge', '17zwd-browser')
const connected = join(profile, 'mediaforge-connected')
const maxImageSize = 5 * 1024 * 1024
let context: BrowserContext | undefined
let headless = false
let busy = false

class ReadError extends Error {}

async function openBrowser(visible: boolean) {
  if (context && (!visible || !headless)) return context
  await context?.close()
  await mkdir(profile, { recursive: true })
  const browser = await chromium.launchPersistentContext(profile, {
    channel: 'msedge',
    headless: !visible,
    acceptDownloads: false,
    timeout: 12_000,
    args: ['--disable-sync', '--no-first-run'],
  })
  browser.on('close', () => { if (context === browser) context = undefined })
  context = browser
  headless = !visible
  return browser
}

async function downloadPhoto(sources: URL[], referer: string, signal: AbortSignal) {
  for (const url of sources) {
    try {
      const response = await fetch(url, {
        signal: AbortSignal.any([signal, AbortSignal.timeout(5000)]),
        redirect: 'error',
        headers: { Referer: referer },
      })
      const mime = response.headers.get('content-type')?.split(';')[0].trim()
      if (!response.ok || !mime || !['image/jpeg', 'image/png', 'image/webp'].includes(mime)) {
        await response.body?.cancel()
        continue
      }
      if (!response.body) continue
      const chunks: Uint8Array[] = []
      let size = 0
      for await (const chunk of response.body) {
        size += chunk.length
        if (size > maxImageSize) break
        chunks.push(chunk)
      }
      if (size > 0 && size <= maxImageSize) {
        return `data:${mime};base64,${Buffer.concat(chunks).toString('base64')}`
      }
    } catch {
      signal.throwIfAborted()
    }
  }
  throw new ReadError('商品页已打开，主图下载失败。请重试或补图。')
}

async function readProduct(product: ProductLink, login: boolean, signal: AbortSignal) {
  if (!login && product.platform === '17zwd' && !existsSync(connected)) throw new ReadError('请先登录 17 网。')
  const browser = await openBrowser(login)
  signal.throwIfAborted()
  const page = browser.pages().find(tab => tab.url() === 'about:blank') ?? await browser.newPage()
  const previousTabs = new Set(browser.pages().filter(tab => tab !== page))
  let sawLogin = false
  let retried = false
  const navigate = async (url: string) => {
    const response = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 15_000 })
    if (response) rememberProductPage(response.url(), product)
  }
  const onNavigate = (frame: Frame) => {
    if (frame !== page.mainFrame()) return
    rememberProductPage(frame.url(), product)
    if (isLoginPage(new URL(frame.url()), product)) sawLogin = true
  }
  page.on('framenavigated', onNavigate)
  try {
    if (product.platform !== '17zwd') await page.setViewportSize({ width: 430, height: 875 })
    if (login) await page.bringToFront()
    await navigate(product.url.href)
    const landed = new URL(page.url())
    if (login && product.platform === '17zwd' && isLoginPage(landed, product) &&
        landed.searchParams.get('redirectUrl') !== product.url.href) {
      landed.searchParams.set('redirectUrl', product.url.href)
      await navigate(landed.href)
    }
    const deadline = Date.now() + (login ? 180_000 : 7000)
    while (Date.now() < deadline) {
      signal.throwIfAborted()
      if (page.isClosed()) throw new ReadError(`${product.name}窗口已关闭，请重新打开。`)
      const opened = login ? browser.pages().filter(tab => !previousTabs.has(tab)) : [page]
      for (const tab of opened) {
        const source = tab.url()
        if (!isProductPage(source, product)) continue
        const result = await readGallery(tab, product).catch(() => null)
        if (!result) continue
        const imageData = await downloadPhoto(result.photos, new URL(source).origin + new URL(source).pathname, signal)
        if (login && product.platform === '17zwd') await writeFile(connected, '')
        return { product_id: product.sku ?? `DY-${product.id}`, title: result.title, image_data: imageData }
      }
      const current = new URL(page.url())
      if (isLoginPage(current, product)) {
        if (!login) throw new ReadError(`${product.name}要求登录后查看该商品。`)
      } else if (login && sawLogin && !retried && isPlatformPage(current, product.platform) && !isProductPage(current.href, product)) {
        retried = true
        await navigate(product.url.href)
      }
      await delay(500, undefined, { signal })
    }
    throw new ReadError(`${product.name}暂未显示商品主图，可以重新读取或补图。`)
  } finally {
    page.off('framenavigated', onNavigate)
    if (!login && !page.isClosed()) {
      if (browser.pages().length === 1) await page.goto('about:blank').catch(() => {})
      else await page.close().catch(() => {})
    }
  }
}

function reply(response: ServerResponse, status: number, body?: unknown) {
  if (response.destroyed) return
  response.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
  response.end(JSON.stringify(body))
}

const server = createServer(async (request, response) => {
  if (request.headers.host !== `${host}:${port}`) return reply(response, 403, {})
  if (request.method === 'GET' && request.url === '/health') {
    return reply(response, 200, { service: 'mediaforge-zwd-login', platforms: ['17zwd', 'pdd', 'douyin'] })
  }
  const origin = request.headers.origin
  if (!origin || !origins.has(origin) || !request.url || !['/login', '/preview'].includes(request.url)) {
    return reply(response, 403, {})
  }
  response.setHeader('Access-Control-Allow-Origin', origin)
  response.setHeader('Vary', 'Origin')
  if (request.method === 'OPTIONS') {
    response.setHeader('Access-Control-Allow-Methods', 'POST')
    response.setHeader('Access-Control-Allow-Headers', 'content-type')
    return reply(response, 204)
  }
  if (request.method !== 'POST' || request.headers['content-type'] !== 'application/json') {
    return reply(response, 405, {})
  }
  if (busy) return reply(response, 409, { error: '正在处理另一条商品链接，请稍后重试。' })
  busy = true
  const controller = new AbortController()
  response.on('close', () => controller.abort())
  try {
    let body = ''
    for await (const chunk of request) {
      body += chunk
      if (body.length > 48 * 1024) throw new ReadError('商品链接过长。')
    }
    const payload: { url?: unknown } = JSON.parse(body)
    const product = productLink(payload.url)
    if (!product) throw new ReadError('请粘贴拼多多、抖音商城或 17 网的商品链接。')
    const preview = await readProduct(product, request.url === '/login', controller.signal)
    reply(response, 200, preview)
  } catch (error) {
    reply(response, 422, { error: error instanceof ReadError ? error.message : '无法读取商品页，请重试或补图。' })
  } finally {
    busy = false
  }
})

server.listen(port, host)
