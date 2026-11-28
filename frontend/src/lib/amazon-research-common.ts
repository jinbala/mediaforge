import { api } from './api'
import { amazonRunLabels, amazonStepLabels, type AmazonRunSummary } from './amazon-research'

export type AmazonResearchContext = {
  run_id: string
  asin?: string | null
  direction?: string | null
}

export type AmazonReportSection = {
  id: string
  title: string
  status: string
  sample_count?: number | null
}

export type AmazonResearchReportIndex = {
  version: string
  run_id: string
  mode: AmazonRunSummary['mode']
  site: string
  sections: AmazonReportSection[]
}

type ComparisonRun = Pick<AmazonRunSummary, 'id' | 'source' | 'mode' | 'title' | 'created_at' | 'updated_at' | 'status'>
type ComparisonValue = number | string | boolean | null

export type AmazonResearchComparison = {
  previous: ComparisonRun | null
  current: ComparisonRun
  metrics: { label: string; before: ComparisonValue; after: ComparisonValue; unit?: string | null; basis?: string | null }[]
  notes: string[]
}

function runPath(id: string) {
  return '/v1/research/amazon/runs/' + encodeURIComponent(id)
}

export const getAmazonResearchReports = (id: string, signal: AbortSignal) =>
  api.get<AmazonResearchReportIndex>(runPath(id) + '/reports', { signal })

export const getAmazonResearchReport = (id: string, section: string, signal: AbortSignal) =>
  api.get<{ markdown: string }>(runPath(id) + '/reports/' + encodeURIComponent(section), { signal })

export const getAmazonResearchEvidence = (id: string, signal: AbortSignal) =>
  api.get<unknown>(runPath(id) + '/evidence', { signal })

export const getAmazonResearchComparison = (id: string, signal: AbortSignal) =>
  api.get<AmazonResearchComparison>(runPath(id) + '/comparison', { signal })

const statusLabels: Record<string, string> = {
  ...amazonRunLabels,
  ...amazonStepLabels,
  ready: '就绪', complete: '完整', partial: '部分', unknown: '未知', unavailable: '不可用',
}

export function amazonWorkspaceStatus(status: string) {
  return Object.hasOwn(statusLabels, status) ? statusLabels[status] : status
}

export function amazonComparisonValue(value: ComparisonValue, unit?: string | null) {
  if (value == null) return '未知'
  if (typeof value === 'boolean') return value ? '是' : '否'
  return unit ? `${value} ${unit}` : String(value)
}
