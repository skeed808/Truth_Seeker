import { useState, useCallback, useRef } from 'react'
import SearchBar from './components/SearchBar'
import FilterPanel from './components/FilterPanel'
import ResultsList from './components/ResultsList'

const DEFAULT_PREFS = {
  underground_bias: 0.5,
  freshness_bias: 0.5,
  exclude_corporate: false,
  forums_only: false,
  long_form_only: false,
  // v2 toggles
  deep_crawl: false,
  deseo_mode: false,
  forums_priority: false,
  // v3 toggles
  use_cache: true,
  seed_expand: false,
  domain_explore: false,
}

async function doSearch(query, prefs) {
  const resp = await fetch('/api/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, ...prefs, max_results: 20 }),
  })
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}))
    throw new Error(err.detail || `HTTP ${resp.status}`)
  }
  return resp.json()
}

export default function App() {
  const [query, setQuery] = useState('')
  const [prefs, setPrefs] = useState(DEFAULT_PREFS)
  const [results, setResults] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [lastQuery, setLastQuery] = useState('')
  const abortRef = useRef(null)

  const handleSearch = useCallback(async (q, p) => {
    const searchQuery = q ?? query
    const searchPrefs = p ?? prefs
    if (!searchQuery.trim()) return

    setLoading(true)
    setError(null)

    try {
      const data = await doSearch(searchQuery, searchPrefs)
      setResults(data)
      setLastQuery(searchQuery)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [query, prefs])

  const handlePrefsChange = useCallback((newPrefs) => {
    setPrefs(newPrefs)
  }, [])

  const handleRerank = useCallback(() => {
    if (lastQuery) handleSearch(lastQuery, prefs)
  }, [lastQuery, prefs, handleSearch])

  return (
    <div className="app">
      <header className="header">
        <div className="logo-row">
          <span className="logo-glyph" aria-hidden="true">⟁</span>
          <h1 className="logo-text">
            truth<span className="accent">seeker</span>
          </h1>
        </div>
        <p className="tagline">
          signal over noise &nbsp;·&nbsp; obscure over popular &nbsp;·&nbsp; information over commerce
        </p>
      </header>

      <SearchBar
        value={query}
        onChange={setQuery}
        onSearch={() => handleSearch(query, prefs)}
        loading={loading}
      />

      <div className="layout">
        <aside className="sidebar">
          <FilterPanel
            prefs={prefs}
            onChange={handlePrefsChange}
            onRerank={handleRerank}
            hasResults={!!results}
          />
        </aside>

        <main className="results-area">
          {error && (
            <div className="error-banner" role="alert">
              <span className="error-icon">⚠</span>
              <span>{error}</span>
            </div>
          )}

          {loading && (
            <div className="loading-state">
              <div className="spinner" aria-hidden="true" />
              <p>Searching the depths&hellip;</p>
              <p className="loading-sub">
                Fetching · Extracting · Ranking
                {prefs.deep_crawl && ' · Crawling deeper'}
                {prefs.deseo_mode && ' · De-SEO filtering'}
                {prefs.seed_expand && ' · Expanding seeds'}
                {prefs.domain_explore && ' · Exploring domains'}
              </p>
            </div>
          )}

          {!loading && results && (
            <ResultsList results={results} />
          )}

          {!loading && !results && !error && (
            <div className="empty-state">
              <div className="empty-glyph" aria-hidden="true">◈</div>
              <p>Enter a query to begin searching for truth.</p>
              <p className="empty-hint">
                Adjust the sidebar controls to tune the signal-to-noise ratio.
              </p>
            </div>
          )}
        </main>
      </div>
    </div>
  )
}
