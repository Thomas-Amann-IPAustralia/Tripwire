import React from 'react';
import { NavLink } from 'react-router-dom';
import { Icon } from './ui.jsx';

// Navigation is grouped by what people come to Tripwire to do. Routes are
// unchanged from earlier versions so existing bookmarks keep working.
export const NAV_GROUPS = [
  {
    label: 'Review',
    items: [
      { id: 'consider', path: '/consider', label: 'Assessments',     meta: 'Stage 8', icon: 'inbox' },
    ],
  },
  {
    label: 'Monitor',
    items: [
      { id: 'observe',  path: '/observe',  label: 'Pipeline activity', icon: 'activity' },
      { id: 'sources',  path: '/sources',  label: 'Monitored sources', icon: 'source' },
      { id: 'corpus',   path: '/corpus',   label: 'IPFR corpus',       icon: 'grid' },
    ],
  },
  {
    label: 'Administration',
    items: [
      { id: 'health',   path: '/health',   label: 'System health',    icon: 'pulse' },
      { id: 'adjust',   path: '/adjust',   label: 'Configuration',    icon: 'sliders' },
      { id: 'document', path: '/document', label: 'How Tripwire works', icon: 'book' },
    ],
  },
];

export const NAV_ITEMS = NAV_GROUPS.flatMap(g => g.items);

export default function NavRail({ collapsed, onToggle }) {
  return (
    <nav className={`tw-sidebar${collapsed ? ' is-collapsed' : ''}`} aria-label="Primary navigation">
      <div className="tw-brand">
        <div className="tw-brand-logo" aria-hidden="true">T</div>
        <div className="tw-brand-text">
          <div className="tw-brand-name">Tripwire</div>
          <div className="tw-brand-sub">IP First Response monitoring</div>
        </div>
      </div>

      <div className="tw-nav">
        {NAV_GROUPS.map(group => (
          <React.Fragment key={group.label}>
            <div className="tw-nav-label">{group.label}</div>
            {group.items.map(item => (
              <NavLink
                key={item.id}
                to={item.path}
                className={({ isActive }) => `tw-nav-link${isActive ? ' active' : ''}`}
                title={collapsed ? item.label : undefined}
              >
                <Icon name={item.icon} />
                <span className="tw-nav-text">{item.label}</span>
                {item.meta && <span className="tw-nav-meta">{item.meta}</span>}
              </NavLink>
            ))}
          </React.Fragment>
        ))}
      </div>

      <div className="tw-sidebar-foot">
        <button
          type="button"
          className="tw-icon-btn"
          onClick={onToggle}
          aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
          title={collapsed ? 'Expand navigation' : 'Collapse navigation'}
        >
          <Icon name="panel" />
        </button>
        <div className="tw-sidebar-foot-text">
          <strong>IP Australia</strong>
          Content monitoring workspace
        </div>
      </div>
    </nav>
  );
}
