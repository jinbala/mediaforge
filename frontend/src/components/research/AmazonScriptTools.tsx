import { useRef, useState, type FormEvent } from 'react'
import { Button } from '@/components/ui/button'
import { AmazonScriptInputs, readScriptInputs } from '@/components/research/AmazonScriptInputs'
import { AmazonScriptResults } from '@/components/research/AmazonScriptResults'
import { api } from '@/lib/api'
import { amazonTime, downloadAmazonFile } from '@/lib/amazon-research'
import {
  mergeScriptRetry, readScriptHistory, saveScriptHistory, scriptStatus, scriptTools,
  type ScriptInput, type ScriptResult, type ScriptTool,
} from '@/lib/amazon-script-tools'

export function AmazonScriptTools({ historyKey, keyword, source }: { historyKey: string | null; keyword: string; source: string }) {
  const [tool, setTool] = useState<ScriptTool>('keywords')
  const [report, setReport] = useState<ScriptResult | null>(null)
  const [history, setHistory] = useState<ScriptResult[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const sending = useRef(false)

  async function run(body: ScriptInput, previous?: ScriptResult) {
    if (sending.current) return
    sending.current = true
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const response = await api.post<ScriptResult>('/v1/research/amazon/tools', body)
      const result = previous ? mergeScriptRetry(previous, response) : response
      setReport(result)
      if (historyKey) {
        try {
          saveScriptHistory(historyKey, result)
          const saved = readScriptHistory(historyKey)
          setHistory((open) => open === null ? null : saved)
        } catch { setNotice('本浏览器未能保存报告，仍可下载。') }
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '执行失败，请稍后重试。')
    } finally {
      sending.current = false
      setBusy(false)
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    try { void run(readScriptInputs(tool, new FormData(event.currentTarget))) }
    catch (caught) { setError(caught instanceof Error ? caught.message : '请核对表单。') }
  }

  function retry(current: ScriptResult) {
    if (current.tool === 'keywords') {
      return run({ tool: 'keywords', keyword: current.keyword, queries: current.samples.filter((row) => row.status === 'failed').map((row) => row.query) }, current)
    }
    if (current.tool === 'ppc') return run({ tool: 'ppc', sources: current.competitors.filter((row) => row.status === 'failed').map((row) => row.asin) }, current)
    if (current.tool === 'listing') return run({ tool: 'listing', source: current.listing.asin })
    if (current.tool === 'hs') return run({ tool: 'hs', query: current.query })
  }

  return <details className="rounded-xl border bg-card p-4 sm:p-5" id="amazon-script-tools">
    <summary className="cursor-pointer font-semibold">调研工具 · 采样、费用与归类</summary>
    <div className="mt-4 space-y-4">
      <p className="text-sm text-muted-foreground">首批支持 Amazon 美国站和美国进口。结果支持 JSON 下载；工具历史仅保存在当前浏览器、当前企业与账号下。</p>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3" role="group" aria-label="选择调研工具">
        {(Object.keys(scriptTools) as ScriptTool[]).map((item) => <Button key={item} type="button" aria-pressed={item === tool} disabled={busy} variant={item === tool ? 'default' : 'outline'} onClick={() => { setTool(item); setError('') }}>
          {scriptTools[item].label}<span className="text-xs opacity-80">{scriptTools[item].req}</span>
        </Button>)}
      </div>
      <form className="space-y-3" onSubmit={submit} autoComplete="off" aria-busy={busy}>
        <fieldset key={tool} disabled={busy} className="space-y-3">
          <AmazonScriptInputs tool={tool} keyword={keyword} source={source} />
          <div className="flex flex-wrap gap-2">
            <Button type="submit">{busy ? '处理中…' : `执行${scriptTools[tool].label}`}</Button>
            {historyKey && <Button type="button" variant="outline" aria-expanded={history !== null} onClick={() => {
              if (history !== null) return setHistory(null)
              try { setHistory(readScriptHistory(historyKey)); setNotice('') }
              catch { setHistory([]); setNotice('暂时无法读取本浏览器的工具历史。') }
            }}>工具历史</Button>}
          </div>
        </fieldset>
      </form>
      {error && <p role="alert" className="break-words rounded-md border p-3 text-sm text-destructive">{error}</p>}
      {notice && <p role="status" className="text-sm text-muted-foreground">{notice}</p>}
      {history !== null && <section aria-label="工具历史" className="space-y-2 rounded-md border p-3">
        {history.length === 0 && <p className="text-sm text-muted-foreground">暂无本浏览器的工具报告。</p>}
        {history.map((item) => <Button key={item.report_id} type="button" disabled={busy} variant="ghost" className="h-auto min-h-9 whitespace-normal text-left" onClick={() => { setTool(item.tool); setReport(item); setError(''); setHistory(null) }}>
          {scriptTools[item.tool].label} · {scriptStatus[item.status]} · {amazonTime(item.collected_at)}
        </Button>)}
      </section>}
      {report && <section className="space-y-3 border-t pt-4" aria-label="工具报告" aria-live="polite">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="space-y-1">
            <h2 className="font-semibold">{scriptTools[report.tool].req} · {scriptTools[report.tool].label} · {scriptStatus[report.status]}</h2>
            <p className="break-all text-xs text-muted-foreground">{report.site} · {amazonTime(report.collected_at)} · 报告 {report.report_id}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            {(report.status === 'partial_fail' || report.status === 'failed') && <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void retry(report)}>重试失败项</Button>}
            <Button type="button" size="sm" variant="outline" onClick={() => downloadAmazonFile(JSON.stringify({ req: scriptTools[report.tool].req, ...report }, null, 2) + '\n', `${scriptTools[report.tool].req}-${report.report_id}.json`, 'application/json')}>下载 JSON</Button>
          </div>
        </div>
        <AmazonScriptResults report={report} />
        <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">{report.warnings.map((text) => <li key={text}>{text}</li>)}</ul>
      </section>}
    </div>
  </details>
}
