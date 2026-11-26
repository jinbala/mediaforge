'use client'

import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { ArrowRightLeft, Check, Loader2, Pencil, Plus, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { api } from '@/lib/api'

type ProviderFormat = 'openai' | 'openrouter'
type Connection = {
  provider_id: string
  name: string
  is_active: boolean
  api_format: ProviderFormat
  base_url: string
  model_alias: string
  model_id: string
}
type ProviderSettings = {
  connection: Connection | null
  profiles: Connection[]
  models: { alias: string; model_id: string }[]
}

const PATH = '/v1/image-provider'

export function ImageProviderSettings() {
  const [saved, setSaved] = useState<ProviderSettings | null>(null)
  const [editing, setEditing] = useState<Connection | 'new' | null>(null)
  const [name, setName] = useState('')
  const [format, setFormat] = useState<ProviderFormat>('openai')
  const [baseUrl, setBaseUrl] = useState('')
  const [model, setModel] = useState('fast')
  const [modelId, setModelId] = useState('')
  const [key, setKey] = useState('')
  const [modelIds, setModelIds] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [working, setWorking] = useState(false)
  const [readingModels, setReadingModels] = useState(false)
  const [removing, setRemoving] = useState<Connection | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  function openEditor(connection: Connection | null) {
    setEditing(connection ?? 'new')
    setName(connection?.name ?? '')
    setFormat(connection?.api_format ?? 'openai')
    setBaseUrl(connection?.base_url ?? '')
    setModel(connection?.model_alias ?? 'fast')
    setModelId(connection?.model_id ?? '')
    setKey('')
    setModelIds([])
    setError('')
    setNotice('')
  }

  function closeEditor() {
    setEditing(null)
    setKey('')
    setModelIds([])
  }

  useEffect(() => {
    let active = true
    api.get<ProviderSettings>(PATH)
      .then((settings) => { if (active) setSaved(settings) })
      .catch(() => { if (active) setError('图片生成设置读取失败，请刷新页面。') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])

  function connectionInput() {
    return { api_format: format, base_url: baseUrl.trim(), api_key: key.trim() || undefined }
  }

  async function readModels() {
    setReadingModels(true)
    setError('')
    setNotice('')
    try {
      const { model_ids } = await api.post<{ model_ids: string[] }>(`${PATH}/models`, {
        ...connectionInput(),
        provider_id: editing && editing !== 'new' ? editing.provider_id : undefined,
      })
      setModelIds(model_ids)
      setNotice(model_ids.length ? `已读取 ${model_ids.length} 个模型，可在模型 ID 输入框中选择。` : '服务没有返回模型列表，可直接填写模型 ID。')
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '模型列表读取失败。')
    } finally {
      setReadingModels(false)
    }
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!editing) return
    setWorking(true)
    setError('')
    setNotice('')
    try {
      const body = {
        ...connectionInput(),
        name: name.trim(),
        model_alias: model,
        model_id: modelId.trim(),
      }
      const settings = editing === 'new'
        ? await api.post<ProviderSettings>(`${PATH}/profiles`, body)
        : await api.put<ProviderSettings>(`${PATH}/profiles/${editing.provider_id}`, body)
      setSaved(settings)
      closeEditor()
      const isActive = editing === 'new' ? settings.profiles.length === 1 && settings.connection
        : settings.connection?.provider_id === editing.provider_id
      setNotice(isActive
        ? `已保存「${body.name}」，当前正在使用此配置。`
        : `已保存「${body.name}」。`)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '图片生成设置保存失败。')
    } finally {
      setWorking(false)
    }
  }

  async function switchProvider(connection: Connection | null) {
    setWorking(true)
    setError('')
    setNotice('')
    try {
      if (connection) {
        setSaved(await api.put<ProviderSettings>(`${PATH}/profiles/${connection.provider_id}/activate`, {}))
        setNotice(`已切换到「${connection.name}」。`)
      } else {
        await api.delete(PATH)
        setSaved(await api.get<ProviderSettings>(PATH))
        setNotice('已切换到系统配置。')
      }
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '切换图片生成服务失败。')
    } finally {
      setWorking(false)
    }
  }

  async function removeProvider() {
    if (!removing) return
    setWorking(true)
    setError('')
    setNotice('')
    try {
      setSaved(await api.delete<ProviderSettings>(`${PATH}/profiles/${removing.provider_id}`))
      if (editing && editing !== 'new' && editing.provider_id === removing.provider_id) closeEditor()
      setNotice(`已删除「${removing.name}」。`)
      setRemoving(null)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '删除图片服务配置失败。')
      setRemoving(null)
    } finally {
      setWorking(false)
    }
  }

  const editingSaved = editing !== null && editing !== 'new'
  const busy = working || readingModels
  const selected = saved?.models.find((item) => item.alias === model)?.model_id
  const presets = saved?.models.filter((item, index, models) => item.alias === model || (
    item.model_id !== selected && models.findIndex((option) => option.model_id === item.model_id) === index
  ))
  const presetId = format === 'openai' ? selected?.replace(/^openai\//, '') : selected
  const defaultAddress = format === 'openai' ? 'https://api.openai.com/v1' : 'https://openrouter.ai/api/v1'

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3 space-y-0">
        <div className="space-y-1.5">
          <CardTitle className="text-lg">图片生成服务</CardTitle>
          <CardDescription>保存多个出图接口和模型，按需切换当前使用的配置。</CardDescription>
        </div>
        {saved && <Button type="button" onClick={() => openEditor(null)} disabled={busy || editing === 'new'}>
          <Plus className="mr-2 h-4 w-4" />添加配置
        </Button>}
      </CardHeader>
      <CardContent className="space-y-4">
        {loading && <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> 正在读取设置
        </p>}
        {saved && <ul className="divide-y divide-border rounded-md border" aria-label="图片生成配置">
          {saved.profiles.map((profile) => (
            <li key={profile.provider_id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
              <div className="min-w-0 space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="break-all text-sm font-medium">{profile.name}</span>
                  {profile.is_active && <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary">
                    <Check className="h-3 w-3" />当前使用
                  </span>}
                </div>
                <p className="break-all text-xs text-muted-foreground">{profile.model_id} · {profile.base_url}</p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {!profile.is_active && <Button type="button" variant="outline" size="sm" disabled={busy}
                  onClick={() => switchProvider(profile)} aria-label={`切换使用 ${profile.name}`}>
                  <ArrowRightLeft className="mr-1.5 h-3.5 w-3.5" />切换使用
                </Button>}
                <Button type="button" variant="ghost" size="icon" disabled={busy} onClick={() => openEditor(profile)}
                  aria-label={`编辑 ${profile.name}`} title={`编辑 ${profile.name}`}>
                  <Pencil className="h-4 w-4" />
                </Button>
                <Button type="button" variant="ghost" size="icon" disabled={busy} onClick={() => setRemoving(profile)}
                  aria-label={`删除 ${profile.name}`} title={`删除 ${profile.name}`}>
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            </li>
          ))}
          <li className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
            <div className="flex items-center gap-2 text-sm font-medium">
              系统配置
              {!saved.connection && <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary">
                <Check className="h-3 w-3" />当前使用
              </span>}
            </div>
            {saved.connection && <Button type="button" variant="outline" size="sm" disabled={busy}
              onClick={() => switchProvider(null)} aria-label="切换使用系统配置">
              <ArrowRightLeft className="mr-1.5 h-3.5 w-3.5" />切换使用
            </Button>}
          </li>
        </ul>}
        {saved && editing && <form onSubmit={save} className="space-y-5 rounded-md border p-4">
          <h3 className="text-sm font-semibold">{editing === 'new' ? '添加图片服务配置' : '编辑图片服务配置'}</h3>
          <fieldset disabled={busy} className="grid gap-x-6 gap-y-4 lg:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="image-provider-name">配置名称</Label>
              <Input id="image-provider-name" value={name} placeholder="例如 CheapRouter 主账号"
                required maxLength={80} onChange={(event) => setName(event.target.value)} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="image-provider-format">接口协议</Label>
              <Select value={format} onValueChange={(value: ProviderFormat) => { setFormat(value); setModelIds([]) }} disabled={busy}>
                <SelectTrigger id="image-provider-format"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="openai">OpenAI 兼容（图片生成与编辑）</SelectItem>
                  <SelectItem value="openrouter">OpenRouter 兼容（Images API）</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="image-provider-url">中转 API 地址</Label>
              <Input id="image-provider-url" type="url" placeholder="https://你的中转域名/v1" pattern="https://.+"
                value={baseUrl} maxLength={1024} onChange={(event) => { setBaseUrl(event.target.value); setModelIds([]) }} />
              <p className="text-xs text-muted-foreground">填写公网 HTTPS API 根地址。留空使用 {defaultAddress}。</p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="image-provider-model">默认图片模型</Label>
              <Select value={model} onValueChange={(value) => { setModel(value); setModelId('') }} disabled={busy}>
                <SelectTrigger id="image-provider-model"><SelectValue /></SelectTrigger>
                <SelectContent>{presets?.map((item) => (
                  <SelectItem value={item.alias} key={item.alias}>{item.model_id}</SelectItem>
                ))}</SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="image-provider-model-id">模型 ID（可选）</Label>
              <div className="flex flex-wrap gap-2 sm:flex-nowrap">
                <Input id="image-provider-model-id" value={modelId} placeholder={presetId}
                  list={modelIds.length ? 'image-provider-model-ids' : undefined}
                  maxLength={200} onChange={(event) => setModelId(event.target.value)} />
                <Button type="button" variant="outline" onClick={readModels} disabled={busy || (!editingSaved && !key.trim())}>
                  {readingModels && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}读取模型列表
                </Button>
              </div>
              <datalist id="image-provider-model-ids">{modelIds.map((id) => <option key={id} value={id} />)}</datalist>
              <p className="text-xs text-muted-foreground">可从服务返回的列表选择出图模型，也可填写完整模型 ID。</p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="image-provider-key">API Key</Label>
              <Input id="image-provider-key" type="password" autoComplete="new-password" value={key}
                placeholder={editingSaved ? '已保存，留空保留现有密钥' : '输入图片生成服务的 API Key'}
                required={!editingSaved} maxLength={4096} onChange={(event) => { setKey(event.target.value); setModelIds([]) }} />
            </div>
          </fieldset>
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={busy || !selected || !name.trim()}>
              {working && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}保存图片服务配置
            </Button>
            <Button type="button" variant="outline" disabled={busy} onClick={closeEditor}>取消</Button>
          </div>
        </form>}
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        {notice && <p role="status" className="text-sm text-muted-foreground">{notice}</p>}
        <Dialog open={Boolean(removing)} onOpenChange={(open) => { if (!open && !working) setRemoving(null) }}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>删除「{removing?.name}」？</DialogTitle>
              <DialogDescription>
                将移除此配置及保存的 API Key。{removing?.is_active && '删除后会切换到系统配置。'}
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" disabled={working} onClick={() => setRemoving(null)}>取消</Button>
              <Button variant="destructive" disabled={working} onClick={removeProvider}>确认删除</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </CardContent>
    </Card>
  )
}
