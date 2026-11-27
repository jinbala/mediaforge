import { useRef, useState, type FormEvent } from 'react'
import { AmazonScriptInputs, readScriptInputs } from '@/components/research/AmazonScriptInputs'
import { AmazonScriptResults } from '@/components/research/AmazonScriptResults'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { downloadAmazonFile } from '@/lib/amazon-research'
import { api } from '@/lib/api'
import type { ScriptResult } from '@/lib/amazon-script-tools'

type Field = { key: string; label: string; kind: 'text' | 'lines' | 'number' | 'choice'; choices: string[]; required: boolean }
type Definition = { req: string; title: string; note: string; fields: Field[] }
type SkillReport = { req: string; title: string; created_at: string; site: 'US'; limitations: string[];
  confirmed_inputs: Record<string, string>; sections: { title: string; rows: string[][]; columns: string[] }[];
  fba: Extract<ScriptResult, { tool: 'fba' }> | null }

const selectStyle = 'h-9 w-full rounded-md border bg-background px-2 text-sm'
const endpoint = '/v1/research/amazon/skills'

export function AmazonSkills() {
  const [skills, setSkills] = useState<Definition[]>([])
  const [selected, setSelected] = useState('')
  const [report, setReport] = useState<SkillReport | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const sending = useRef(false)
  const definition = skills.find((item) => item.req === selected)

  async function load() {
    if (sending.current || skills.length) return
    sending.current = true
    setBusy(true)
    setError('')
    try {
      const items = await api.get<Definition[]>(endpoint)
      setSkills(items)
      setSelected(items[0]?.req ?? '')
    } catch (caught) { setError(caught instanceof Error ? caught.message : '读取技能失败。') }
    finally { sending.current = false; setBusy(false) }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!definition || sending.current) return
    sending.current = true
    setBusy(true)
    setError('')
    try {
      const data = new FormData(event.currentTarget)
      const values = Object.fromEntries(definition.fields.map((field) => [field.key, String(data.get('skill-' + field.key) ?? '')]))
      // 059 与 042 共用整个计费表单和请求转换，避免日期、单位或服务费字段发生偏差。
      const fba = selected === 'REQ-059' ? readScriptInputs('fba', data) : undefined
      setReport(await api.post<SkillReport>(endpoint + '/' + selected, { values, fba }))
    } catch (caught) { setError(caught instanceof Error ? caught.message : '生成技能报告失败。') }
    finally { sending.current = false; setBusy(false) }
  }

  return <details className="rounded-xl border bg-card p-4 sm:p-5" onToggle={(event) => { if (event.currentTarget.open) void load() }}>
    <summary className="cursor-pointer font-semibold">运营技能 · REQ-045 至 REQ-060</summary>
    <div className="mt-4 space-y-4">
      <p className="text-sm text-muted-foreground">只生成方案、分析或准备清单，不执行投放、促销、申请或库存操作。授权报表仅填写统计日期与必要汇总，不填写凭证。</p>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {!definition && <Button type="button" variant="outline" disabled={busy} onClick={() => void load()}>{busy ? '正在加载' : '加载技能'}</Button>}
      {definition && <form className="space-y-3" aria-busy={busy} onSubmit={(event) => void submit(event)}>
        <label className="block space-y-1 text-sm">技能<select aria-label="技能" className={selectStyle} value={selected} disabled={busy} onChange={(event) => { setSelected(event.target.value); setReport(null); setError('') }}>
          {skills.map((item) => <option value={item.req} key={item.req}>{item.req} · {item.title}</option>)}
        </select></label>
        <p className="text-sm text-muted-foreground">{definition.note}</p>
        <fieldset key={definition.req} disabled={busy} className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            {definition.fields.map((field) => <label key={field.key} className={'space-y-1 text-sm ' + (field.kind === 'lines' ? 'sm:col-span-2' : '')}>
              {field.label}
              {field.kind === 'lines' ? <Textarea name={'skill-' + field.key} maxLength={5000} required={field.required} /> : field.kind === 'choice' ?
                <select name={'skill-' + field.key} className={selectStyle} required={field.required}>{field.choices.map((value) => <option key={value}>{value}</option>)}</select> :
                <Input name={'skill-' + field.key} type={field.kind} step={field.kind === 'number' ? 'any' : undefined} min={field.kind === 'number' ? 0 : undefined} maxLength={400} required={field.required} />}
            </label>)}
          </div>
          {selected === 'REQ-059' && <AmazonScriptInputs tool="fba" source="" keyword="" />}
          <Button type="submit">{busy ? '生成中…' : '生成技能报告'}</Button>
        </fieldset>
      </form>}
      {report && <section className="space-y-3 border-t pt-3" aria-label="技能报告">
        <div className="flex flex-wrap justify-between gap-2"><h2 className="font-medium">{report.req} · {report.title}</h2>
          <Button type="button" variant="outline" size="sm" onClick={() => downloadAmazonFile(JSON.stringify(report, null, 2) + '\n', `${report.req}-report.json`, 'application/json')}>下载技能报告</Button></div>
        {report.sections.map((section) => <div className="overflow-x-auto rounded border" key={section.title}><table className="w-full text-left text-sm">
          <caption className="p-2 text-left font-medium">{section.title}</caption>
          <thead className="bg-muted"><tr>{section.columns.map((column) => <th scope="col" className="p-2" key={column}>{column}</th>)}</tr></thead>
          <tbody>{section.rows.map((row, index) => <tr className="border-t" key={index}>{row.map((value, cell) => <td className="min-w-24 whitespace-pre-wrap break-words p-2 align-top" key={cell}>{value}</td>)}</tr>)}</tbody>
        </table></div>)}
        {report.fba && <AmazonScriptResults report={report.fba} />}
        <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">{report.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
      </section>}
    </div>
  </details>
}
