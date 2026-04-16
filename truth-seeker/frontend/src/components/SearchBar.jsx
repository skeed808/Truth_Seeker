export default function SearchBar({ value, onChange, onSearch, loading }) {
  const handleKey = (e) => {
    if (e.key === 'Enter' && !loading) onSearch()
  }

  return (
    <div className="search-bar-wrap">
      <div className="search-bar">
        <span className="search-prefix" aria-hidden="true">$</span>
        <input
          type="text"
          className="search-input"
          placeholder="what truth are you looking for?"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKey}
          disabled={loading}
          autoFocus
          spellCheck={false}
          aria-label="Search query"
        />
        <button
          className="search-btn"
          onClick={onSearch}
          disabled={loading || !value.trim()}
          aria-label="Search"
        >
          {loading ? <span className="btn-spinner" /> : 'SEEK'}
        </button>
      </div>
    </div>
  )
}
