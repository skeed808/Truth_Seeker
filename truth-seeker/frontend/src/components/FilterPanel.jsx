function Slider({ label, leftLabel, rightLabel, value, onChange }) {
  const pct = Math.round(value * 100)
  return (
    <div className="filter-group">
      <div className="slider-header">
        <span className="filter-label">{label}</span>
        <span className="slider-pct">{pct}%</span>
      </div>
      <div className="slider-track-labels">
        <span>{leftLabel}</span>
        <span>{rightLabel}</span>
      </div>
      <input
        type="range"
        min="0"
        max="1"
        step="0.05"
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="slider"
        aria-label={label}
      />
    </div>
  )
}

function Toggle({ label, description, checked, onChange, accent }) {
  return (
    <label className={`toggle-row${accent ? ' toggle-row--accent' : ''}`} title={description}>
      <span className="toggle-label">{label}</span>
      <div className="toggle-switch-wrap">
        <input
          type="checkbox"
          className="toggle-input"
          checked={checked}
          onChange={(e) => onChange(e.target.checked)}
        />
        <span className="toggle-switch" aria-hidden="true" />
      </div>
    </label>
  )
}

export default function FilterPanel({ prefs, onChange, onRerank, hasResults }) {
  const set = (key) => (val) => onChange({ ...prefs, [key]: val })

  return (
    <div className="filter-panel">
      <h2 className="filter-title">RANKING CONTROLS</h2>

      <Slider
        label="SOURCE BIAS"
        leftLabel="Mainstream"
        rightLabel="Underground"
        value={prefs.underground_bias}
        onChange={set('underground_bias')}
      />

      <Slider
        label="TIME BIAS"
        leftLabel="Archived"
        rightLabel="Recent"
        value={prefs.freshness_bias}
        onChange={set('freshness_bias')}
      />

      <div className="divider" />

      {/* ── Hard filters ── */}
      <div className="filter-group">
        <h3 className="filter-section-title">HARD FILTERS</h3>
        <Toggle
          label="Exclude corporate"
          description="Filter out results from known large corporate domains (CNN, Forbes, Amazon, etc.)"
          checked={prefs.exclude_corporate}
          onChange={set('exclude_corporate')}
        />
        <Toggle
          label="Forums only"
          description="Only show results from community forums, discussion boards, and Q&A sites"
          checked={prefs.forums_only}
          onChange={set('forums_only')}
        />
        <Toggle
          label="Long-form only"
          description="Filter out pages with fewer than 800 words — removes thin listicles and stubs"
          checked={prefs.long_form_only}
          onChange={set('long_form_only')}
        />
      </div>

      <div className="divider" />

      {/* ── v2 Deep search modes ── */}
      <div className="filter-group">
        <h3 className="filter-section-title">DEEP SEARCH</h3>

        <Toggle
          label="⟁ Deep Crawl"
          description="Follow internal links 1 level deeper from top results — surfaces content search engines can't see directly. Slower."
          checked={prefs.deep_crawl}
          onChange={set('deep_crawl')}
          accent={true}
        />
        <Toggle
          label="✦ De-SEO Mode"
          description="Aggressively penalise AI-generated and SEO-templated content. Raises the ai_spam and commercial_bias penalty weights."
          checked={prefs.deseo_mode}
          onChange={set('deseo_mode')}
          accent={true}
        />
        <Toggle
          label="◈ Forums Priority"
          description="Boost forum and community discussion results. Also fetches Reddit as an additional source."
          checked={prefs.forums_priority}
          onChange={set('forums_priority')}
          accent={true}
        />
      </div>

      <div className="divider" />

      {/* ── v3 Self-sufficiency toggles ── */}
      <div className="filter-group">
        <h3 className="filter-section-title">DISCOVERY MODE</h3>

        <Toggle
          label="💾 Use Cache"
          description="Load results from local cache and save new ones. Speeds up repeated queries and reduces external API usage."
          checked={prefs.use_cache}
          onChange={set('use_cache')}
          accent={true}
        />
        <Toggle
          label="🌱 Seed Expand"
          description="Follow outbound cross-domain links found on result pages to discover content not indexed by search engines."
          checked={prefs.seed_expand}
          onChange={set('seed_expand')}
          accent={true}
        />
        <Toggle
          label="🔍 Domain Explore"
          description="Crawl 3–5 additional internal pages from high-scoring domains to build a deeper local index."
          checked={prefs.domain_explore}
          onChange={set('domain_explore')}
          accent={true}
        />
      </div>

      {hasResults && (
        <button className="rerank-btn" onClick={onRerank}>
          ⟳ RERANK RESULTS
        </button>
      )}

      <div className="filter-legend">
        <div className="legend-title">SCORE DIMENSIONS</div>
        <ul className="legend-list">
          <li><span className="legend-dot green"  />Info Density</li>
          <li><span className="legend-dot blue"   />Obscurity</li>
          <li><span className="legend-dot red"    />−Commercial Bias</li>
          <li><span className="legend-dot yellow" />Freshness</li>
          <li><span className="legend-dot purple" />Diversity</li>
          <li><span className="legend-dot orange" />−AI Spam</li>
        </ul>
      </div>
    </div>
  )
}
