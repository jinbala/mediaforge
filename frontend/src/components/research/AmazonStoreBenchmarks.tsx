import {
  amazonBenchmarkBasis, amazonBenchmarkCoverage, amazonBenchmarkScope, amazonListingPresentation,
  amazonTime, type AmazonStoreCompetition, type AmazonMarketListing,
} from '@/lib/amazon-research'

function ListingEvidence({ item }: { item: AmazonMarketListing }) {
  return (
    <li className="space-y-1 rounded border p-2">
      <a href={item.url} target="_blank" rel="noreferrer" className="text-primary hover:underline">
        {item.brand ?? '品牌未知'} · {item.title ?? item.asin} · {item.asin}
      </a>
      <p>价格 {item.price ?? '未知'} {item.price_currency ?? ''} · 评分 {item.rating ?? '未知'} · 评分数 {item.rating_count ?? '未知'}</p>
      <p>{amazonListingPresentation(item)} · 采集 {amazonTime(item.collected_at)}</p>
      <p>公开卖点 · {item.highlights[0]?.slice(0, 150) ?? '未知'}</p>
      {item.store_url ? (
        <a href={item.store_url} target="_blank" rel="noreferrer" className="text-primary hover:underline">查看品牌店铺</a>
      ) : <p>公开店铺链接未知。</p>}
    </li>
  )
}

export function AmazonStoreBenchmarks({ competition }: { competition: AmazonStoreCompetition }) {
  return (
    <section aria-label="竞品与品牌定位" className="space-y-3 rounded-lg border bg-card p-4">
      <h3 className="font-medium">竞品与品牌定位</h3>
      <p className="text-sm">{amazonBenchmarkCoverage(competition)}</p>
      <p className="text-xs text-muted-foreground">{amazonBenchmarkScope}</p>
      {!competition.pairs.length && <p className="text-sm">当前无可复核的对标配对。</p>}
      {(competition.searches.length > 0 || Object.values(competition.listings).some((item) => item.problem)) && (
        <details className="rounded-md border p-3 text-sm">
          <summary className="cursor-pointer">搜索来源与读取情况</summary>
          <ul className="mt-2 space-y-1 text-xs">
            {competition.searches.map((search) => (
              <li key={search.target_asin}>
                <a href={search.url} target="_blank" rel="noreferrer" className="text-primary hover:underline">{search.query}</a>
                {' · ' + search.candidates.length + ' 张候选卡片 · ' + amazonTime(search.collected_at)}
                {search.problem && ' · ' + search.problem}
              </li>
            ))}
            {Object.values(competition.listings).filter((item) => item.problem).map((item) => (
              <li key={item.asin}>
                <a href={item.url} target="_blank" rel="noreferrer" className="text-primary hover:underline">{item.asin}</a>
                {' · ' + item.problem + ' · ' + amazonTime(item.collected_at)}
              </li>
            ))}
          </ul>
        </details>
      )}
      {competition.pairs.map((pair) => {
        const target = competition.listings[pair.target_asin]
        const rival = competition.listings[pair.competitor_asin]
        return (
          <details key={target.asin + '/' + rival.asin} className="rounded-md border p-3 text-sm">
            <summary className="cursor-pointer font-medium">{target.brand} {target.asin} / {rival.brand} {rival.asin}</summary>
            <p className="mt-2 text-xs">{amazonBenchmarkBasis(pair)}</p>
            <p className="my-2 text-xs">
              受众依据 {pair.audience ?? '待验证'} · 标价差（目标款减竞品）{pair.price_gap_cents === null ? '未知' : 'USD ' + (pair.price_gap_cents / 100).toFixed(2)}
            </p>
            <ul className="grid gap-2 text-xs md:grid-cols-2">
              <ListingEvidence item={target} /><ListingEvidence item={rival} />
            </ul>
            <ul className="mt-2 space-y-1 text-xs">
              {Object.entries(pair.swot).map(([label, text]) => <li key={label}>{label} · {text}</li>)}
            </ul>
          </details>
        )
      })}
    </section>
  )
}
