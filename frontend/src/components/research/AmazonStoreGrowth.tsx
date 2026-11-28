import {
  amazonGrowthFindingGroups, amazonGrowthPriority, amazonGrowthSourcesLabel, amazonTime, revealAmazonSample,
  type AmazonStoreGrowth as Growth,
} from '@/lib/amazon-research'

export function AmazonStoreGrowth({ growth }: { growth: Growth }) {
  const titles = new Map([...growth.opportunities, ...growth.risks].map((row) => [row.key, row.title]))
  return (
    <section aria-label="增长机会与风险" className="space-y-3 rounded-lg border bg-card p-4">
      <h3 className="font-medium">增长机会与风险</h3>
      {amazonGrowthFindingGroups(growth).map(({ title, findings }) => (
        <div key={title} className="space-y-2">
          <h4 className="text-sm font-medium">{title}</h4>
          {findings.map((item) => (
            <details key={item.key} id={'growth-' + item.key} className="rounded border p-2 text-sm">
              <summary className="cursor-pointer font-medium">
                {item.title} · 优先级 {amazonGrowthPriority[item.priority]}
              </summary>
              <dl className="mt-2 space-y-1 text-xs">
                <div><dt className="inline font-medium">公开观察 · </dt><dd className="inline">{item.observed}</dd></div>
                <div><dt className="inline font-medium">待验证 · </dt><dd className="inline">{item.hypothesis}</dd></div>
                <div><dt className="inline font-medium">核查行动 · </dt><dd className="inline">{item.action}</dd></div>
              </dl>
              {item.sources.length > 0 && <ul className="mt-2 space-y-1 text-xs">
                {item.sources.map((source) => (
                  <li key={source.url + '/' + (source.asin ?? '')}>
                    <a href={source.url} target="_blank" rel="noreferrer" className="text-primary hover:underline">
                      {amazonGrowthSourcesLabel(source)}
                    </a>
                    {' · ' + (source.collected_at ? amazonTime(source.collected_at) : '采集时间未知')}
                  </li>
                ))}
              </ul>}
            </details>
          ))}
        </div>
      ))}
      <div className="space-y-1 border-t pt-2 text-sm">
        <h4 className="font-medium">30／60／90 天行动</h4>
        <ol className="space-y-2 text-xs">
          {growth.plan.map((phase) => (
            <li key={phase.day}>
              第 {phase.day} 天 · {phase.action}
              {' '}
              {phase.findings.map((key) => (
                <a key={key} href={'#growth-' + key} onClick={() => revealAmazonSample('growth-' + key)}
                  className="mr-2 text-primary hover:underline">{titles.get(key) ?? key}</a>
              ))}
            </li>
          ))}
        </ol>
      </div>
    </section>
  )
}
