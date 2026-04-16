import { useState, useEffect } from 'react'

/* ── Cluster badge ────────────────────────────────────────────────────────── */
const CLUSTER_STYLES = {
  Forum:      { bg: 'rgba(0,255,136,0.10)', border: '#00ff88', color: '#00ff88',  glyph: '◈' },
  Blog:       { bg: 'rgba(74,244,255,0.10)', border: '#4af4ff', color: '#4af4ff', glyph: '✍' },
  Academic:   { bg: 'rgba(176,136,255,0.12)', border: '#b088ff', color: '#b088ff', glyph: '∂' },
  Commercial: { bg: 'rgba(255,85,85,0.10)',  border: '#ff5555', color: '#ff5555', glyph: '$' },
  News:       { bg: 'rgba(255,204,0,0.10)',  border: '#ffcc00', color: '#ffcc00', glyph: '⊞' },
  Wiki:       { bg: 'rgba(160,160,200,0.10)', border: '#a0a0c8', color: '#a0a0c8', glyph: 'W' },
  Docs:       { bg: 'rgba(100,200,255,0.10)', border: '#64c8ff', color: '#64c8ff', glyph: '{ }' },
  Unknown:    { bg: 'rgba(80,80,100,0.10)',  border: '#505060', color: '#505060', glyph: '?' },
}

function ClusterBadge({ cluster }) {
  const s = CLUSTER_STYLES[cluster] || CLUSTER_STYLES.Unknown
  return (
    <span
      className="cluster-badge"
      style={{ background: s.bg, borderColor: s.border, color: s.color }}
      title={`Content type: ${cluster}`}
    >
      <span className="cluster-glyph">{s.glyph}</span>
      {cluster}
    </span>
  )
}

/* ── Score bar sub-component ─────────────────────────────────────────────── */
function ScoreBar({ label, value, invert = false, color, description }) {
  const display = invert ? 1 - value : value
  const pct = Math.round(display * 100)
  return (
    <div className="score-row" title={description}>
      <div className="score-row-label">{label}</div>
      <div className="score-bar-track">
        <div className="score-bar-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      <div className="score-row-num" style={{ color }}>{pct}</div>
    </div>
  )
}

/* ── Score badge (top-right) ─────────────────────────────────────────────── */
function ScoreBadge({ score }) {
  const pct = Math.round(score * 100)
  const color = pct >= 65 ? '#00ff88' : pct >= 40 ? '#ffcc00' : '#ff5555'
  return (
    <div className="score-badge" style={{ borderColor: color }}>
      <div className="score-badge-num" style={{ color }}>{pct}</div>
      <div className="score-badge-label">score</div>
    </div>
  )
}

/* ── Anti-SEO detail sub-panel ───────────────────────────────────────────── */
function AntiSeoDetail({ detail }) {
  if (!detail) return null
  const items = [
    { k: 'sentence_uniformity',    label: 'Sent. uniformity' },
    { k: 'paragraph_similarity',   label: 'Para similarity'  },
    { k: 'generic_phrase_density', label: 'Generic phrases'  },
    { k: 'heading_density',        label: 'Heading density'  },
    { k: 'connective_flood',       label: 'Connective flood' },
  ]
  return (
    <div className="antiseo-detail">
      <div className="antiseo-title">AI/SEO SIGNAL DETAIL</div>
      {items.map(({ k, label }) => (
        <div key={k} className="antiseo-row">
          <span className="antiseo-label">{label}</span>
          <div className="antiseo-bar-track">
            <div
              className="antiseo-bar-fill"
              style={{ width: `${Math.round((detail[k] || 0) * 100)}%` }}
            />
          </div>
          <span className="antiseo-num">{Math.round((detail[k] || 0) * 100)}</span>
        </div>
      ))}
    </div>
  )
}

/* ── Thumbs vote component ───────────────────────────────────────────────── */
function VoteButtons({ url, query }) {
  const storageKey = `vote:${url}:${query}`
  const [vote, setVote] = useState(() => {
    try { return parseInt(localStorage.getItem(storageKey) || '0', 10) } catch { return 0 }
  })
  const [sending, setSending] = useState(false)

  const cast = async (value) => {
    // Clicking the active button toggles it off (neutral); otherwise set new vote
    const next = vote === value ? 0 : value
    setSending(true)
    try {
      // Only POST when there's a definite direction — 0 means "remove vote"
      // Backend uses INSERT OR REPLACE so sending the new value is enough.
      // When toggling off, send the opposite to overwrite then ignore ranking.
      const payload = next !== 0 ? next : (value === 1 ? -1 : 1)
      await fetch('/api/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url, query, feedback: payload }),
      })
      setVote(next)
      try { localStorage.setItem(storageKey, String(next)) } catch {}
    } catch { /* ignore network errors */ }
    finally { setSending(false) }
  }

  return (
    <div className="vote-buttons" aria-label="Rate this result">
      <button
        className={`vote-btn vote-btn--up${vote === 1 ? ' vote-btn--active' : ''}`}
        onClick={() => cast(1)}
        disabled={sending}
        title="This result was helpful"
        aria-pressed={vote === 1}
      >
        👍
      </button>
      <button
        className={`vote-btn vote-btn--down${vote === -1 ? ' vote-btn--active' : ''}`}
        onClick={() => cast(-1)}
        disabled={sending}
        title="This result was not helpful"
        aria-pressed={vote === -1}
      >
        👎
      </button>
    </div>
  )
}

/* ── Main result card ────────────────────────────────────────────────────── */
export default function ResultCard({ result, rank, query = '' }) {
  const [open, setOpen] = useState(false)
  const scores  = result.scores || {}
  const weights = result.weights_used || {}
  const isHighObscurity  = (scores.obscurity || 0) >= 0.70
  const isCrawled        = result.source === 'crawled' || result.source === 'micro_crawl'
  const isSeedExpanded   = result.source === 'seed_expanded'
  const isDomainExplored = result.source === 'domain_explored'
  const isDiscovered     = isCrawled || isSeedExpanded || isDomainExplored
  const isHighSpam       = (scores.ai_spam || 0) >= 0.50
  const isExploration    = result.is_exploration === true
  // "Live" = freshly fetched from an external API this request (not saved, not discovered)
  const isLive           = !result.from_cache && !isDiscovered

  const displayUrl = result.url?.length > 70
    ? result.url.slice(0, 70) + '…'
    : result.url

  return (
    <article
      className={`result-card${isHighObscurity ? ' result-card--obscure' : ''}${isHighSpam ? ' result-card--spam' : ''}`}
    >
      {/* ── Header ── */}
      <div className="card-header">
        <span className="card-rank" aria-label={`Result ${rank}`}>#{rank}</span>

        <div className="card-title-block">
          <a
            href={result.url}
            target="_blank"
            rel="noopener noreferrer"
            className="card-title"
          >
            {result.title || '(no title)'}
          </a>
          <div className="card-url">{displayUrl}</div>
        </div>

        <ScoreBadge score={scores.final || 0} />
      </div>

      {/* ── Snippet ── */}
      {result.snippet && (
        <p className="card-snippet">{result.snippet}</p>
      )}

      {/* ── Vote buttons ── */}
      <VoteButtons url={result.url} query={query} />

      {/* ── Meta pills ── */}
      <div className="card-meta">
        {result.cluster && <ClusterBadge cluster={result.cluster} />}

        {isCrawled && (
          <span className="meta-pill crawled" title={`Discovered via: ${result.discovered_via || 'micro-crawl'}`}>
            ⟁ crawled
          </span>
        )}
        {isSeedExpanded && (
          <span className="meta-pill seed-expanded" title={`Seed-expanded from: ${result.discovered_via || ''}`}>
            🌱 seeded
          </span>
        )}
        {isDomainExplored && (
          <span className="meta-pill domain-explored" title={`Domain exploration: ${result.discovered_via || ''}`}>
            🔍 explored
          </span>
        )}
        {isExploration && (
          <span className="meta-pill exploration" title="Anti-echo-chamber: injected from low-trust or unknown domain to break filter bubbles">
            ◈ anti-bias
          </span>
        )}
        {result.from_cache && !isDiscovered && (
          <span className="meta-pill saved" title="Loaded from local memory — previously discovered high-value page">
            💾 saved
          </span>
        )}
        {isLive && (
          <span className="meta-pill live" title="Freshly fetched from external API this request">
            ⚡ live
          </span>
        )}
        {result.wayback_used && (
          <span className="meta-pill wayback" title="Content retrieved from Wayback Machine archive">
            ◷ archived
          </span>
        )}

        {result.domain && (
          <span className="meta-pill domain">{result.domain}</span>
        )}
        {result.source && !isDiscovered && result.source !== 'cache' && (
          <span className="meta-pill source">{result.source.toUpperCase()}</span>
        )}
        {result.word_count > 0 && (
          <span className="meta-pill words">{result.word_count.toLocaleString()} words</span>
        )}
        {result.publish_date && (
          <span className="meta-pill date">{result.publish_date}</span>
        )}
        {result.author && (
          <span className="meta-pill author">{result.author}</span>
        )}

        {/* Trust boost badge (link graph authority) */}
        {result.trust_boost > 0 && (
          <span
            className="meta-pill trust-boost"
            title={`Link-graph authority boost: +${Math.round(result.trust_boost * 100)} — multiple trusted pages link here`}
          >
            ↑ linked ×{Math.round(result.trust_boost * 100)}
          </span>
        )}

        {/* Obscurity highlight badge */}
        {isHighObscurity && (
          <span className="meta-pill obscure-hi" title="High obscurity — independent/niche source">
            ◆ obscure
          </span>
        )}
        {/* Spam warning */}
        {isHighSpam && (
          <span className="meta-pill spam-warn" title="High AI/SEO spam score detected">
            ⚠ AI/SEO
          </span>
        )}
      </div>

      {/* ── Score breakdown toggle ── */}
      <button
        className="breakdown-toggle"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        {open ? '▲ hide breakdown' : '▼ score breakdown'}
      </button>

      {open && (
        <div className="score-breakdown">
          <div className="breakdown-title">TRANSPARENCY REPORT</div>

          <ScoreBar
            label="Info Density"
            value={scores.info_density || 0}
            color="#00ff88"
            description="Information richness: word count, lexical diversity, sentence variance, technical vocab."
          />
          <ScoreBar
            label="Obscurity"
            value={scores.obscurity || 0}
            color="#4af4ff"
            description="Source independence: domain type, URL depth, rarity in result set, cross-source frequency."
          />
          <ScoreBar
            label="Commercial Clean"
            value={scores.commercial_bias || 0}
            invert={true}
            color="#ff5555"
            description="Inverted commercial bias. Higher bar = cleaner (fewer affiliates, SEO patterns, price signals)."
          />
          <ScoreBar
            label="Freshness"
            value={scores.freshness || 0}
            color="#ffcc00"
            description="Recency based on publish date (τ = 365d). Unknown date → 50% neutral."
          />
          <ScoreBar
            label="Diversity"
            value={scores.diversity || 0}
            color="#b088ff"
            description="Domain uniqueness in this result set. Penalises repeated domains."
          />
          <ScoreBar
            label="AI/SEO Clean"
            value={scores.ai_spam || 0}
            invert={true}
            color="#ff9944"
            description="Inverted AI-spam score. Higher bar = more authentic writing. Lower = likely templated/AI content."
          />

          {/* AI/SEO sub-signal detail */}
          <AntiSeoDetail detail={result.anti_seo_detail} />

          {/* Active weights */}
          {Object.keys(weights).length > 0 && (
            <div className="weights-row">
              <span className="weights-label">Active weights:</span>
              {Object.entries(weights).map(([k, v]) => (
                <span key={k} className="weight-chip">
                  {k.replace(/_/g, ' ')}&nbsp;{v}
                </span>
              ))}
            </div>
          )}

          {/* Raw score numbers */}
          <div className="raw-scores">
            {Object.entries(scores).map(([k, v]) => (
              <span key={k} className="raw-chip">
                {k.replace(/_/g, ' ')}: {v}
              </span>
            ))}
          </div>
        </div>
      )}
    </article>
  )
}
