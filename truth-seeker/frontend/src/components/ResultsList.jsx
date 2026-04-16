import ResultCard from './ResultCard'

/* ── Intent badge ─────────────────────────────────────────────────────────── */
const INTENT_META = {
  freshness_sensitive: {
    label: 'FRESH',
    color: '#ffcc00',
    bg: 'rgba(255,204,0,0.10)',
    border: 'rgba(255,204,0,0.28)',
    title: 'Freshness-sensitive query — recent results weighted higher',
  },
  deep_research: {
    label: 'RESEARCH',
    color: '#b088ff',
    bg: 'rgba(176,136,255,0.10)',
    border: 'rgba(176,136,255,0.28)',
    title: 'Deep research query — obscure, information-rich sources preferred',
  },
  navigational: {
    label: 'NAVIGATE',
    color: '#4af4ff',
    bg: 'rgba(74,244,255,0.10)',
    border: 'rgba(74,244,255,0.28)',
    title: 'Navigational query — direct destination matches preferred',
  },
}

function IntentBadge({ intent }) {
  const m = INTENT_META[intent]
  if (!m) return null
  return (
    <span
      className="intent-badge"
      style={{ color: m.color, background: m.bg, borderColor: m.border }}
      title={m.title}
    >
      ⊙ {m.label}
    </span>
  )
}

/* ── Trust distribution bar ───────────────────────────────────────────────── */
function TrustBar({ distribution }) {
  const { high = 0, medium = 0, low = 0 } = distribution
  const total = high + medium + low
  if (total === 0) return null
  return (
    <span
      className="trust-bar-wrap"
      title={`Domain trust distribution — high: ${high} · medium: ${medium} · low: ${low}`}
    >
      <span className="trust-bar-label">TRUST</span>
      <span className="trust-bar">
        {high   > 0 && <span className="trust-seg trust-seg--high"   style={{ flex: high }}   />}
        {medium > 0 && <span className="trust-seg trust-seg--med"    style={{ flex: medium }} />}
        {low    > 0 && <span className="trust-seg trust-seg--low"    style={{ flex: low }}    />}
      </span>
      <span className="trust-bar-counts">
        <span style={{ color: '#00ff88' }}>{high}</span>
        <span style={{ color: '#ffcc00' }}>{medium}</span>
        <span style={{ color: '#ff5555' }}>{low}</span>
      </span>
    </span>
  )
}

export default function ResultsList({ results }) {
  if (!results?.results?.length) {
    return (
      <div className="no-results">
        <p>No results matched your filters.</p>
        <p className="no-results-hint">Try loosening the hard filters in the sidebar.</p>
      </div>
    )
  }

  const {
    query, total, sources_used, results: items,
    deep_crawl_pages,
    seed_expanded_pages,
    domain_explored_pages,
    cache_hits,
    query_variants,
    saved_pages_total,
    intent,
    exploration_results_count,
    trust_distribution,
  } = results

  return (
    <div className="results-list">
      <div className="results-meta-bar">
        <span className="results-count">{total} results</span>
        <span className="results-query">for &ldquo;{query}&rdquo;</span>

        {intent && intent !== 'informational' && (
          <IntentBadge intent={intent} />
        )}

        {sources_used && (
          <span className="results-sources">
            {Object.entries(sources_used).map(([src, n]) => (
              <span key={src} className="source-badge">{src.toUpperCase()} {n}</span>
            ))}
            {deep_crawl_pages > 0 && (
              <span className="source-badge source-badge--crawl">⟁ CRAWLED {deep_crawl_pages}</span>
            )}
            {seed_expanded_pages > 0 && (
              <span className="source-badge source-badge--seed">🌱 SEEDED {seed_expanded_pages}</span>
            )}
            {domain_explored_pages > 0 && (
              <span className="source-badge source-badge--explore">🔍 EXPLORED {domain_explored_pages}</span>
            )}
          </span>
        )}

        {/* Pipeline intelligence stats */}
        <span className="pipeline-stats">
          {cache_hits > 0 && (
            <span className="pipeline-stat pipeline-stat--cache" title="Results loaded from local cache">
              💾 {cache_hits} cached
            </span>
          )}
          {query_variants > 0 && (
            <span className="pipeline-stat pipeline-stat--variants" title="Additional query variants searched">
              ⟳ {query_variants} variant{query_variants > 1 ? 's' : ''}
            </span>
          )}
          {exploration_results_count > 0 && (
            <span className="pipeline-stat pipeline-stat--explore-count" title="Anti-echo-chamber results injected from low-trust/unknown domains">
              ◈ {exploration_results_count} explored
            </span>
          )}
          {trust_distribution && (
            <TrustBar distribution={trust_distribution} />
          )}
          {saved_pages_total > 0 && (
            <span className="pipeline-stat pipeline-stat--saved" title="Total high-value pages in local memory">
              💾 {saved_pages_total.toLocaleString()} saved
            </span>
          )}
        </span>
      </div>

      {items.map((result, i) => (
        <ResultCard key={result.url || i} result={result} rank={i + 1} query={query} />
      ))}
    </div>
  )
}
