export const THEMES = [
  { id: 'midnight', name: 'Midnight Neon', colors: ['#0B0D14', '#10121B', '#151824', '#A78BFA', '#67E8F9'] },
  { id: 'cyber-lime', name: 'Cyber Lime', colors: ['#111310', '#171A16', '#1D211B', '#C6F36B', '#B8A1FF'] },
  { id: 'obsidian-teal', name: 'Obsidian Teal', colors: ['#0C1518', '#101E21', '#16262A', '#5FE1D0', '#7CB4FF'] },
  { id: 'cosmic-plum', name: 'Cosmic Plum', colors: ['#160F1D', '#1D1425', '#271B30', '#E879F9', '#A78BFA'] },
  { id: 'graphite-ember', name: 'Graphite Ember', colors: ['#121212', '#191817', '#211F1C', '#F0A56A', '#D890A9'] },
];

export function ThemeSelector({ value, onChange }) {
  return <section className="theme-section" aria-labelledby="theme-section-title">
    <div className="theme-section-heading"><div><span className="section-kicker">MAKE IT YOURS</span><h2 id="theme-section-title">Appearance</h2><p>Choose a color palette for your workspace. It applies everywhere and is saved automatically.</p></div><span className="theme-current">{THEMES.find(theme => theme.id === value)?.name}</span></div>
    <div className="theme-grid" role="radiogroup" aria-label="Color themes">
      {THEMES.map(theme => <ThemeCard key={theme.id} theme={theme} selected={value === theme.id} onSelect={() => onChange(theme.id)} />)}
    </div>
  </section>;
}

function ThemeCard({ theme, selected, onSelect }) {
  return <button type="button" role="radio" aria-checked={selected} className={`theme-card ${selected ? 'selected' : ''}`} onClick={onSelect}>
    <span className="theme-preview" style={{ '--preview-bg': theme.colors[0], '--preview-sidebar': theme.colors[1], '--preview-surface': theme.colors[2], '--preview-accent': theme.colors[3], '--preview-secondary': theme.colors[4] }} aria-hidden="true">
      <i className="preview-sidebar"><b/><b/><b/></i><i className="preview-main"><b/><b/><em/><b/></i><i className="preview-dot"/>
    </span>
    <span className="theme-card-info"><span><b>{theme.name}</b><small>{theme.id === 'midnight' ? 'Futuristic · deep navy' : theme.id === 'cyber-lime' ? 'Energetic · charcoal' : theme.id === 'obsidian-teal' ? 'Calm · dark teal' : theme.id === 'cosmic-plum' ? 'Expressive · deep plum' : 'Minimal · graphite and ember'}</small></span><span className="theme-check" aria-hidden="true">✓</span></span>
    <span className="theme-swatches" aria-hidden="true">{theme.colors.map(color => <i key={color} style={{ backgroundColor: color }}/>)}</span>
  </button>;
}
