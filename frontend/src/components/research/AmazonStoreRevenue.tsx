import {
  amazonRevenueBasis, amazonRevenueCoverage, amazonRevenueLabels, amazonRevenueRows, amazonRevenueRules,
  amazonRevenueSections, amazonRevenueSummary, type AmazonRun, type AmazonStoreRevenue as Revenue,
} from '@/lib/amazon-research'

export function AmazonStoreRevenue({ run, revenue }: { run: AmazonRun; revenue: Revenue }) {
  const sections = amazonRevenueSections(run, revenue)
  return (
    <section aria-label="销量与营收估算" className="space-y-3 rounded-lg border bg-card p-4">
      <h3 className="font-medium">销量与营收估算</h3>
      <p className="text-sm">{amazonRevenueCoverage(revenue)} 情景顺序为低／中／高。</p>
      <ul className="space-y-1 text-sm">
        {amazonRevenueRows(revenue).map(({ label, totals }) => (
          <li key={label}><strong>{label}</strong> · {amazonRevenueSummary(totals, revenue.annual_months)}</li>
        ))}
      </ul>
      <details className="rounded-md border p-3 text-sm">
        <summary className="cursor-pointer font-medium">估算依据与同款去重</summary>
        <ul className="mt-2 space-y-2 text-xs">
          {revenue.samples.map((sample) => (
            <li key={sample.family_asin} className="space-y-1">
              <p>
                {amazonRevenueLabels[sample.segment]} · {sample.listing ? (
                  <a href={sample.listing.url} target="_blank" rel="noreferrer" className="text-primary hover:underline">{sample.asin}</a>
                ) : sample.asin}
                {' · ' + amazonRevenueBasis(sample)}
              </p>
              <p>{amazonRevenueSummary(sample.monthly, revenue.annual_months)}</p>
              <p>同款 {sample.family_asin} · 已采 ASIN {sample.members.join('、')}</p>
              <p>来源栏目 {sections.filter(({ section }) => sample.sections.includes(section.key)).map(({ section }) => (
                <a key={section.key} href={section.final_url ?? section.url} target="_blank" rel="noreferrer" className="mr-2 text-primary hover:underline">
                  {section.title_zh ?? section.title}
                </a>
              ))}</p>
            </li>
          ))}
        </ul>
      </details>
      <details className="rounded-md border p-3 text-sm">
        <summary className="cursor-pointer font-medium">栏目贡献</summary>
        <ul className="mt-2 space-y-1 text-xs">
          {sections.map(({ section, monthly }) => (
            <li key={section.key}>
              <a href={section.final_url ?? section.url} target="_blank" rel="noreferrer" className="text-primary hover:underline">{section.title_zh ?? section.title}</a>
              {' · ' + amazonRevenueSummary(monthly, revenue.annual_months)}
            </li>
          ))}
        </ul>
      </details>
      {amazonRevenueRules(revenue).map((rule) => <p key={rule} className="text-xs text-muted-foreground">{rule}</p>)}
    </section>
  )
}
