import React from 'react';
import { NavLink } from 'react-router-dom';

const NAV_ITEMS = [
  {
    id: 'observe',
    path: '/observe',
    label: 'OBSERVE',
    color: 'var(--stage-4)',
    icon: (
      <svg width="18" height="18" viewBox="0 0 18 18" fill="none" xmlns="http://www.w3.org/2000/svg">
        <circle cx="9" cy="9" r="4" stroke="currentColor" strokeWidth="1.5" fill="none"/>
        <circle cx="9" cy="9" r="1.5" fill="currentColor"/>
        <line x1="9" y1="1" x2="9" y2="4" stroke="currentColor" strokeWidth="1.5"/>
        <line x1="9" y1="14" x2="9" y2="17" stroke="currentColor" strokeWidth="1.5"/>
        <line x1="1" y1="9" x2="4" y2="9" stroke="currentColor" strokeWidth="1.5"/>
        <line x1="14" y1="9" x2="17" y2="9" stroke="currentColor" strokeWidth="1.5"/>
      </svg>
    ),
  },
  {
    id: 'corpus',
    path: '/corpus',
    label: 'CORPUS',
    color: 'var(--stage-5)',
    icon: (
      <svg width="18" height="18" viewBox="0 0 18 18" fill="none" xmlns="http://www.w3.org/2000/svg">
        <rect x="2" y="2" width="6" height="6" stroke="currentColor" strokeWidth="1.5" fill="none"/>
        <rect x="10" y="2" width="6" height="6" stroke="currentColor" strokeWidth="1.5" fill="none"/>
        <rect x="2" y="10" width="6" height="6" stroke="currentColor" strokeWidth="1.5" fill="none"/>
        <rect x="10" y="10" width="6" height="6" stroke="currentColor" strokeWidth="1.5" fill="none"/>
      </svg>
    ),
  },
  {
    id: 'sources',
    path: '/sources',
    label: 'SOURCES',
    color: 'var(--stage-3)',
    icon: (
      <svg width="18" height="18" viewBox="0 0 18 18" fill="none" xmlns="http://www.w3.org/2000/svg">
        <circle cx="4" cy="9" r="2.5" stroke="currentColor" strokeWidth="1.5" fill="none"/>
        <circle cx="14" cy="4" r="2.5" stroke="currentColor" strokeWidth="1.5" fill="none"/>
        <circle cx="14" cy="14" r="2.5" stroke="currentColor" strokeWidth="1.5" fill="none"/>
        <line x1="6.2" y1="7.8" x2="11.8" y2="5.2" stroke="currentColor" strokeWidth="1.5"/>
        <line x1="6.2" y1="10.2" x2="11.8" y2="12.8" stroke="currentColor" strokeWidth="1.5"/>
      </svg>
    ),
  },
  {
    id: 'adjust',
    path: '/adjust',
    label: 'ADJUST',
    color: 'var(--stage-1)',
    icon: (
      <svg width="18" height="18" viewBox="0 0 18 18" fill="none" xmlns="http://www.w3.org/2000/svg">
        <line x1="2" y1="5" x2="16" y2="5" stroke="currentColor" strokeWidth="1.5"/>
        <line x1="2" y1="9" x2="16" y2="9" stroke="currentColor" strokeWidth="1.5"/>
        <line x1="2" y1="13" x2="16" y2="13" stroke="currentColor" strokeWidth="1.5"/>
        <circle cx="6" cy="5" r="2" fill="var(--bg-secondary)" stroke="currentColor" strokeWidth="1.5"/>
        <circle cx="11" cy="9" r="2" fill="var(--bg-secondary)" stroke="currentColor" strokeWidth="1.5"/>
        <circle cx="7" cy="13" r="2" fill="var(--bg-secondary)" stroke="currentColor" strokeWidth="1.5"/>
      </svg>
    ),
  },
  {
    id: 'document',
    path: '/document',
    label: 'DOCUMENT',
    color: 'var(--stage-6)',
    icon: (
      <svg width="18" height="18" viewBox="0 0 18 18" fill="none" xmlns="http://www.w3.org/2000/svg">
        <rect x="3" y="1" width="12" height="16" stroke="currentColor" strokeWidth="1.5" fill="none"/>
        <line x1="6" y1="6" x2="12" y2="6" stroke="currentColor" strokeWidth="1.5"/>
        <line x1="6" y1="9" x2="12" y2="9" stroke="currentColor" strokeWidth="1.5"/>
        <line x1="6" y1="12" x2="10" y2="12" stroke="currentColor" strokeWidth="1.5"/>
      </svg>
    ),
  },
  {
    id: 'health',
    path: '/health',
    label: 'HEALTH',
    color: 'var(--stage-2)',
    icon: (
      <svg width="18" height="18" viewBox="0 0 18 18" fill="none" xmlns="http://www.w3.org/2000/svg">
        <polyline points="1,9 4,9 6,4 8,14 10,7 12,11 14,9 17,9" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinejoin="round" strokeLinecap="round"/>
      </svg>
    ),
  },
];

const styles = {
  rail: (expanded) => ({
    width: expanded ? '200px' : '56px',
    minWidth: expanded ? '200px' : '56px',
    height: '100vh',
    background: 'var(--bg-secondary)',
    borderRight: '1px solid var(--rule)',
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
    transition: 'width 200ms ease, min-width 200ms ease',
    position: 'relative',
    zIndex: 10,
    flexShrink: 0,
  }),
  toggle: {
    width: '56px',
    height: '48px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    cursor: 'pointer',
    background: 'none',
    border: 'none',
    color: 'var(--text-tertiary)',
    flexShrink: 0,
    borderBottom: '1px solid var(--rule)',
  },
  nav: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    paddingTop: '8px',
  },
  link: (isActive, color, expanded) => ({
    display: 'flex',
    alignItems: 'center',
    height: '44px',
    padding: '0',
    textDecoration: 'none',
    color: isActive ? 'var(--text-primary)' : 'var(--text-tertiary)',
    borderLeft: isActive ? `3px solid ${color}` : '3px solid transparent',
    background: isActive ? 'var(--bg-accent)' : 'transparent',
    transition: 'color 150ms ease, background 150ms ease',
    overflow: 'hidden',
    whiteSpace: 'nowrap',
  }),
  iconWrap: {
    width: '53px',
    height: '44px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
  },
  label: (expanded) => ({
    fontFamily: 'var(--font-display)',
    fontSize: '13px',
    letterSpacing: '0.15em',
    opacity: expanded ? 1 : 0,
    transition: 'opacity 150ms ease',
    pointerEvents: expanded ? 'auto' : 'none',
  }),
};

function HamburgerIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" xmlns="http://www.w3.org/2000/svg">
      <line x1="2" y1="5" x2="16" y2="5" stroke="currentColor" strokeWidth="1.5"/>
      <line x1="2" y1="9" x2="16" y2="9" stroke="currentColor" strokeWidth="1.5"/>
      <line x1="2" y1="13" x2="16" y2="13" stroke="currentColor" strokeWidth="1.5"/>
    </svg>
  );
}

export default function NavRail({ expanded, onToggle }) {
  return (
    <>
      <style>{`
        @media (max-width: 768px) {
          .nav-rail { width: 56px !important; min-width: 56px !important; }
          .nav-label { display: none !important; }
        }
        .nav-link:hover {
          color: var(--text-primary) !important;
          background: var(--bg-accent) !important;
        }
      `}</style>
      <nav style={styles.rail(expanded)} className="nav-rail">
        <button style={styles.toggle} onClick={onToggle} aria-label="Toggle navigation">
          <HamburgerIcon />
        </button>
        <div style={styles.nav}>
          {NAV_ITEMS.map(item => (
            <NavLink
              key={item.id}
              to={item.path}
              className="nav-link"
              style={({ isActive }) => styles.link(isActive, item.color, expanded)}
            >
              <span style={styles.iconWrap}>{item.icon}</span>
              <span style={styles.label(expanded)} className="nav-label">{item.label}</span>
            </NavLink>
          ))}
        </div>
      </nav>
    </>
  );
}
