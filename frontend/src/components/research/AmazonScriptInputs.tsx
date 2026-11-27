import { useState, type ComponentProps } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { fbaCategories, type Charge, type Duty, type ScriptInput, type ScriptTool } from '@/lib/amazon-script-tools'

const selectClass = 'h-9 w-full rounded-md border bg-background px-2 text-sm'
const amounts = { type: 'number', step: '0.01', min: 0 } as const

function Field({ label, ...props }: ComponentProps<typeof Input> & { label: string }) {
  return <label className="block space-y-1 text-sm"><span>{label}</span><Input {...props} /></label>
}

function RateFields({ prefix }: { prefix: string }) {
  return <div className="grid gap-2 sm:grid-cols-2">
    <Field label="来源 HTTPS 链接" type="url" name={`${prefix}-url`} required />
    <Field label="费率／汇率版本" name={`${prefix}-version`} required maxLength={240} />
    <Field label="有效期起" type="date" name={`${prefix}-from`} required />
    <Field label="有效期止（含）" type="date" name={`${prefix}-to`} required />
  </div>
}

function OptionalRate({ title, prefix, children }: { title: string; prefix: string; children: React.ReactNode }) {
  const [enabled, setEnabled] = useState(false)
  return <div className="space-y-2 rounded-md border p-3">
    <label className="flex items-center gap-2 text-sm"><input type="checkbox" name={`${prefix}-enabled`} checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />{title}</label>
    {enabled && <>{children}<RateFields prefix={prefix} /></>}
  </div>
}

function RateRows({ duty = false }: { duty?: boolean }) {
  const [rows, setRows] = useState<{ id: number; basis: Duty['basis'] }[]>([])
  return <div className="space-y-2">
    {rows.map(({ id, basis }) => {
      const prefix = `row-${id}`
      return <fieldset className="space-y-2 rounded-md border p-3" key={id}>
        <legend className="px-1 text-sm">{duty ? '税费明细' : '单件服务费'}</legend>
        <input type="hidden" name="rows" value={id} />
        <Field label="费用名称" name={`${prefix}-name`} maxLength={240} required />
        {duty && <label className="block text-sm">计费方式<select className={selectClass} name={`${prefix}-basis`} value={basis} onChange={(event) => setRows(rows.map((row) => row.id === id ? { ...row, basis: event.target.value as Duty['basis'] } : row))}>
          <option value="customs_value">按海关计税价格</option><option value="per_unit">按件 USD</option><option value="fixed">固定金额 USD／票</option>
        </select></label>}
        {duty && basis === 'customs_value' ? <Field key="rate" label="从价税率（百分数，例如 7.5 表示 7.5%）" type="number" min="0" max="1000" step="0.0001" name={`${prefix}-rate`} required />
          : <Field key="amount" label="金额（USD）" {...amounts} name={`${prefix}-amount`} required />}
        <RateFields prefix={prefix} />
        <Button type="button" size="sm" variant="ghost" onClick={() => setRows(rows.filter((row) => row.id !== id))}>移除本项</Button>
      </fieldset>
    })}
    <Button type="button" size="sm" variant="outline" disabled={rows.length >= (duty ? 15 : 10)} onClick={() => setRows([...rows, { id: (rows.at(-1)?.id ?? 0) + 1, basis: 'customs_value' }])}>{duty ? '添加税费（含已确认零税率）' : '添加适用服务费'}</Button>
  </div>
}

function FbaFields() {
  return <>
    <p className="text-sm text-muted-foreground">按单件测算。使用英寸、磅、美元；内置配送表覆盖非服装、非危险品小号标准件（≤ 6 oz）。其他档位可填写有日期和版本的卖家后台报价。</p>
    <div className="grid gap-3 sm:grid-cols-3">
      {[1, 2, 3].map((i) => <Field key={i} label={`包装边长 ${i}（in）`} name={`dim${i}`} type="number" min="0.0001" max="1000" step="0.0001" required />)}
      <Field label="实际称重（lb）" name="weight" type="number" min="0.0001" max="1000" step="0.0001" required />
      <Field label="成交总价（USD）" name="price" {...amounts} min="0.01" required />
      <label className="block text-sm">计费类目<select className={selectClass} name="category">{Object.entries(fbaCategories).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <Field label="预计配送日" name="ship-date" type="date" required />
      <Field label="库存起始日" name="storage-start" type="date" required />
      <Field label="库存天数" name="storage-days" type="number" min="0" max="730" required />
      <label className="block text-sm">销售计划<select name="selling-plan" className={selectClass}><option value="professional">专业</option><option value="individual">个人</option></select></label>
      <label className="flex items-center gap-2 text-sm"><input name="dangerous" type="checkbox" />危险品（按后台分类）</label>
    </div>
    <OptionalRate title="使用已确认的单件配送报价（含适用附加费）" prefix="override">
      <Field label="单件配送报价（USD）" name="override-amount" {...amounts} required />
    </OptionalRate>
    <RateRows />
  </>
}

function LandedFields() {
  const [currency, setCurrency] = useState('USD')
  return <>
    <p className="text-sm text-muted-foreground">目的地：美国。货值、运费和保险使用所填原币种；海关计税价格及其余费用用 USD。缺失费用显示待确认，确认不适用才填 0。</p>
    <div className="grid gap-3 sm:grid-cols-3">
      <Field label="原产地（两位国家代码）" name="origin" pattern="[A-Za-z]{2}" maxLength={2} required />
      <Field label="已确认的 HTSUS（8 或 10 位）" name="hs" maxLength={18} required />
      <Field label="预计进口日" name="as-of" type="date" required />
      <Field label="原币种（三位代码）" name="currency" pattern="[A-Z]{3}" maxLength={3} value={currency} onChange={(event) => setCurrency(event.target.value.toUpperCase())} required />
      <Field label="货值（原币种／整票）" name="goods" {...amounts} required />
      <Field label="国际运费（原币种／整票）" name="freight" {...amounts} required />
      <Field label="保险（原币种／整票）" name="insurance" {...amounts} />
      <Field label="报关与口岸费用（USD／整票）" name="brokerage" {...amounts} />
      <Field label="境内运输（USD／整票）" name="inland" {...amounts} />
      <Field label="已确认的海关计税价格（USD）" name="customs" {...amounts} />
      <Field label="数量（件）" name="quantity" type="number" min="1" max="1000000" required />
    </div>
    {currency !== 'USD' && <OptionalRate title="提供已确认的换汇依据" prefix="fx"><Field label="每单位原币种折合 USD" name="fx-amount" type="number" step="0.00000001" min="0.00000001" required /></OptionalRate>}
    <RateRows duty />
    <label className="flex items-center gap-2 text-sm"><input type="checkbox" name="confirmed" />已确认所列税费涵盖本票适用项目（含一般税、附加税与口岸费用）</label>
  </>
}

export function AmazonScriptInputs({ tool, keyword, source }: { tool: ScriptTool; keyword: string; source: string }) {
  switch (tool) {
    case 'keywords': return <Field label="种子词" name="keyword" maxLength={100} defaultValue={keyword} required />
    case 'listing': return <Field label="ASIN 或美国站商品链接" name="source" maxLength={2000} defaultValue={source} required />
    case 'ppc': return <label className="block space-y-1 text-sm"><span>竞品 ASIN 或美国站商品链接（每行一个，最多 6 个）</span><Textarea name="sources" maxLength={12005} required /></label>
    case 'fba': return <FbaFields />
    case 'landed': return <LandedFields />
    case 'hs': return <Field label="商品英文描述／关键词，或已知 HS / HTSUS 编码" name="query" maxLength={240} required />
  }
}

export function readScriptInputs(tool: ScriptTool, data: FormData): ScriptInput {
  const text = (name: string) => String(data.get(name) ?? '').trim()
  const source = (prefix: string) => ({
    source_url: text(`${prefix}-url`), version: text(`${prefix}-version`),
    effective_from: text(`${prefix}-from`), effective_to: text(`${prefix}-to`),
  })
  const rows = data.getAll('rows').map((id) => `row-${id}`)
  switch (tool) {
    case 'keywords': return { tool, keyword: text('keyword') }
    case 'listing': return { tool, source: text('source') }
    case 'ppc': {
      const sources = text('sources').split(/\r?\n/).map((line) => line.trim()).filter(Boolean)
      if (!sources.length || sources.length > 6) throw new Error('请输入 1 至 6 个竞品。')
      return { tool, sources }
    }
    case 'hs': return { tool, query: text('query') }
    case 'fba': return {
      tool, category: text('category') as keyof typeof fbaCategories,
      dimensions_in: [text('dim1'), text('dim2'), text('dim3')], weight_lb: text('weight'),
      price_usd: text('price'), ship_date: text('ship-date'), storage_start: text('storage-start'),
      storage_days: Number(text('storage-days')), dangerous_goods: data.has('dangerous'),
      selling_plan: text('selling-plan') === 'individual' ? 'individual' : 'professional',
      fulfillment_override: data.has('override-enabled') ? { name: '配送费', amount: text('override-amount'), source: source('override') } : undefined,
      service_fees: rows.map((row): Charge => ({ name: text(`${row}-name`), amount: text(`${row}-amount`), source: source(row) })),
    }
    case 'landed': return {
      tool, origin: text('origin'), destination: 'US', hs_code: text('hs'), as_of: text('as-of'),
      currency: text('currency'), goods_value: text('goods'), freight: text('freight'), quantity: Number(text('quantity')),
      insurance: text('insurance') || undefined, brokerage_usd: text('brokerage') || undefined,
      inland_usd: text('inland') || undefined, customs_value_usd: text('customs') || undefined,
      usd_per_currency: text('fx-amount') || undefined, exchange_source: data.has('fx-enabled') ? source('fx') : undefined,
      duties_confirmed: data.has('confirmed'),
      duties: rows.map((row): Duty => {
        const basis = text(`${row}-basis`) as Duty['basis']
        return { name: text(`${row}-name`), basis, source: source(row),
          ...(basis === 'customs_value' ? { rate: (Number(text(`${row}-rate`)) / 100).toFixed(6) } : { amount: text(`${row}-amount`) }) }
      }),
    }
  }
}
