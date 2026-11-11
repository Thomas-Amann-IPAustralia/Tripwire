// Shared presentation primitives for the "warm character" design system.
// Styling lives in styles/globals.css (tw-* classes); these components only
// assemble markup so every section reads the same way.

import React from 'react';

// ── Icons ────────────────────────────────────────────────────────────────────
// Simple 24px stroke icons, drawn to match the lookbook's line weight.

const ICON_PATHS = {
  inbox:    <path d="M4 4h16l2 11v5H2v-5zM2 15h6l2 3h4l2-3h6" />,
  activity: <path d="M2 12h5l3-7 4 14 3-7h5" />,
  grid:     <><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></>,
  source:   <><rect x="4" y="3" width="16" height="18" rx="2" /><path d="M8 8h8M8 12h8M8 16h5" /></>,
  sliders:  <><path d="M4 7h3M11 7h9M4 17h9M17 17h3" /><circle cx="9" cy="7" r="2" /><circle cx="15" cy="17" r="2" /></>,
  book:     <path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2zM4 21V5M8 7h7" />,
  pulse:    <path d="M3 12h4l2-5 4 10 2-5h6" />,
  search:   <><circle cx="10.5" cy="10.5" r="7" /><path d="m16 16 5 5" /></>,
  chevron:  <path d="m9 6 6 6-6 6" />,
  chevronDown: <path d="m6 9 6 6 6-6" />,
  check:    <path d="m5 12 5 5L20 6" />,
  info:     <><circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7h.01" /></>,
  help:     <><circle cx="12" cy="12" r="9" /><path d="M9 9a3 3 0 1 1 4 3c-1 .5-1 1-1 2M12 17h.01" /></>,
  refresh:  <path d="M20 7V3l-3 3a9 9 0 1 0 3 9M20 3v5h-5" />,
  filter:   <path d="M3 5h18l-7 8v6l-4 2v-8z" />,
  close:    <path d="M6 6l12 12M18 6 6 18" />,
  external: <path d="M13 5h6v6M19 5l-9 9M19 14v5H5V5h5" />,
  clock:    <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  arrow:    <path d="M4 12h15m-6-6 6 6-6 6" />,
  arrowLeft:<path d="M20 12H5m6-6-6 6 6 6" />,
  download: <path d="M12 3v12m-4-4 4 4 4-4M4 17v4h16v-4" />,
  plus:     <path d="M12 5v14M5 12h14" />,
  sun:      <><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></>,
  moon:     <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" />,
  panel:    <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M9 4v16" /></>,
  alert:    <><path d="M12 3 2 20h20z" /><path d="M12 10v4M12 17h.01" /></>,
  layers:   <path d="m12 3 9 5-9 5-9-5zM3 13l9 5 9-5" />,
  database: <><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" /></>,
  sparkle:  <path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6" />,
  file:     <path d="M7 3h7l4 4v14H7zM14 3v5h5M10 13h5M10 17h5" />,
  mail:     <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="m3 7 9 6 9-6" /></>,
  code:     <path d="m8 7-5 5 5 5M16 7l5 5-5 5" />,
  copy:     <><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3" /></>,
  play:     <path d="M7 5v14l11-7z" />,
  reset:    <path d="M4 4v6h6M4.5 15a8 8 0 1 0 1.9-8.4L4 10" />,
};

export function Icon({ name, size, style, className, title }) {
  const content = ICON_PATHS[name];
  if (!content) return null;
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden={title ? undefined : true}
      role={title ? 'img' : undefined}
      className={className}
      style={style}
    >
      {title && <title>{title}</title>}
      {content}
    </svg>
  );
}

// ── Page scaffolding ─────────────────────────────────────────────────────────

export function PageHeader({ eyebrow, title, description, actions, children }) {
  return (
    <header className="tw-page-header">
      <div style={{ minWidth: 0, flex: '1 1 420px' }}>
        {eyebrow && <div className="tw-eyebrow">{eyebrow}</div>}
        <h1 className="tw-h1">{title}</h1>
        {description && <p className="tw-lede">{description}</p>}
        {children}
      </div>
      {actions && <div className="tw-header-actions">{actions}</div>}
    </header>
  );
}

export function Card({ title, subtitle, actions, children, footer, bodyStyle, style, className = '', noBody = false, plainHead = false, clip = false, id }) {
  const hasHead = title || subtitle || actions;
  return (
    <section id={id} className={`tw-card${clip ? ' is-clip' : ''} ${className}`} style={style}>
      {hasHead && (
        <div className={`tw-card-head${plainHead ? ' is-plain' : ''}`}>
          <div style={{ minWidth: 0 }}>
            {title && <h2 className="tw-card-title">{title}</h2>}
            {subtitle && <p className="tw-card-sub">{subtitle}</p>}
          </div>
          {actions && <div className="tw-row" style={{ flexShrink: 0 }}>{actions}</div>}
        </div>
      )}
      {noBody ? children : <div className="tw-card-body" style={bodyStyle}>{children}</div>}
      {footer && <div className="tw-card-foot">{footer}</div>}
    </section>
  );
}

export function MetricCard({ label, value, context, tone, icon, small = false, title }) {
  const toneClass = tone === 'warn' ? ' is-warn' : tone === 'alert' ? ' is-alert' : '';
  const valueColor =
    tone === 'warn' ? 'var(--warn)'
    : tone === 'alert' ? 'var(--alert)'
    : tone === 'ok' ? 'var(--ok)'
    : undefined;
  return (
    <div className={`tw-metric${toneClass}`} title={title}>
      <div className="tw-metric-label">
        <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</span>
        {icon && <Icon name={icon} size={17} style={{ color: 'var(--accent)', opacity: 0.75 }} />}
      </div>
      <div className={`tw-metric-value${small ? ' is-sm' : ''}`} style={{ color: valueColor }}>
        {value ?? '—'}
      </div>
      {context && <div className="tw-metric-context">{context}</div>}
    </div>
  );
}

// ── Badges ───────────────────────────────────────────────────────────────────

export function Badge({ tone = 'neutral', dot = false, large = false, children, title, style }) {
  const toneClass = tone && tone !== 'neutral' ? ` is-${tone}` : '';
  return (
    <span className={`tw-badge${toneClass}${large ? ' is-lg' : ''}`} title={title} style={style}>
      {dot && <span className="tw-dot" />}
      {children}
    </span>
  );
}

export const VERDICTS = {
  CHANGE_REQUIRED: { tone: 'alert', label: 'Change required', color: 'var(--alert)' },
  UNCERTAIN:       { tone: 'warn',  label: 'Uncertain',       color: 'var(--warn)' },
  NO_CHANGE:       { tone: 'ok',    label: 'No change',       color: 'var(--ok)' },
  ERROR:           { tone: 'error', label: 'Error',           color: 'var(--error)' },
};

export function humanise(value) {
  if (value == null || value === '') return '—';
  const s = String(value).replace(/_/g, ' ').toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function VerdictBadge({ verdict, large = false }) {
  const cfg = VERDICTS[verdict];
  if (!cfg) return <Badge tone="muted" large={large}>{humanise(verdict)}</Badge>;
  return <Badge tone={cfg.tone} dot large={large}>{cfg.label}</Badge>;
}

const OUTCOMES = {
  ok:        { tone: 'ok',    label: 'OK' },
  success:   { tone: 'ok',    label: 'Success' },
  completed: { tone: 'ok',    label: 'Completed' },
  partial:   { tone: 'warn',  label: 'Partial' },
  error:     { tone: 'error', label: 'Error' },
  failed:    { tone: 'error', label: 'Failed' },
  no_change: { tone: 'muted', label: 'No change' },
  skipped:   { tone: 'muted', label: 'Skipped' },
  running:   { tone: 'info',  label: 'Running' },
  idle:      { tone: 'muted', label: 'Idle' },
};

export function OutcomeBadge({ outcome }) {
  const key = String(outcome ?? '').toLowerCase();
  const cfg = OUTCOMES[key];
  if (!cfg) return <Badge tone="muted">{humanise(outcome)}</Badge>;
  return <Badge tone={cfg.tone} dot>{cfg.label}</Badge>;
}

// ── Controls ─────────────────────────────────────────────────────────────────

export function Button({ variant, size, icon, iconRight, children, className = '', ...rest }) {
  const cls = ['tw-btn'];
  if (variant) cls.push(`is-${variant}`);
  if (size === 'sm') cls.push('is-sm');
  if (!children && icon) cls.push('is-icon');
  if (className) cls.push(className);
  return (
    <button type="button" className={cls.join(' ')} {...rest}>
      {icon && <Icon name={icon} />}
      {children}
      {iconRight && <Icon name={iconRight} />}
    </button>
  );
}

export function Segmented({ options, value, onChange, ariaLabel }) {
  return (
    <div className="tw-segment" role="group" aria-label={ariaLabel}>
      {options.map(opt => {
        const o = typeof opt === 'object' ? opt : { value: opt, label: opt };
        const active = value === o.value;
        return (
          <button
            key={o.value}
            type="button"
            className={active ? 'active' : ''}
            aria-pressed={active}
            onClick={() => onChange(o.value)}
            title={o.title}
          >
            {o.dot && <span className="tw-chip-dot" style={{ background: o.dot }} />}
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

export function SearchInput({ value, onChange, placeholder, width = 260, autoFocus, style }) {
  return (
    <label className="tw-search" style={{ width, ...style }}>
      <Icon name="search" />
      <input
        className="tw-input"
        type="search"
        value={value}
        placeholder={placeholder}
        autoFocus={autoFocus}
        onChange={e => onChange(e.target.value)}
        aria-label={placeholder}
      />
    </label>
  );
}

export function Caret({ open }) {
  return (
    <span className={`tw-caret${open ? ' is-open' : ''}`} aria-hidden="true">
      <Icon name="chevron" />
    </span>
  );
}

export function Meter({ value, color, style, label }) {
  const pct = Math.max(0, Math.min(1, Number(value) || 0)) * 100;
  return (
    <div className="tw-meter" style={style} role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)} aria-label={label}>
      <span style={{ width: `${pct}%`, background: color }} />
    </div>
  );
}

// ── States ───────────────────────────────────────────────────────────────────

export function EmptyState({ icon = 'inbox', title, children, compact = false }) {
  return (
    <div className="tw-empty" style={compact ? { padding: '24px 16px' } : undefined}>
      <div className="tw-empty-icon"><Icon name={icon} /></div>
      {title && <div className="tw-empty-title">{title}</div>}
      {children && <div className="tw-empty-text">{children}</div>}
    </div>
  );
}

export function Loading({ children = 'Loading…', style }) {
  return (
    <div className="tw-loading" role="status" style={style}>
      <span className="tw-spinner" />
      {children}
    </div>
  );
}

export function Callout({ tone, icon = 'info', title, children, style }) {
  return (
    <div className={`tw-callout${tone ? ` is-${tone}` : ''}`} style={style}>
      <Icon name={icon} />
      <div style={{ minWidth: 0 }}>
        {title && <strong style={{ display: 'block', color: 'var(--ink)', marginBottom: 2 }}>{title}</strong>}
        {children}
      </div>
    </div>
  );
}

// Shared chart styling (recharts) so every chart reads the same in both themes.
export const chartAxisTick = { fontFamily: 'var(--font-sans)', fontSize: 11, fill: 'var(--muted)' };
export const chartTooltipStyle = {
  background: 'var(--surface)',
  border: '1px solid var(--line)',
  borderRadius: 8,
  boxShadow: 'var(--shadow-lg)',
  fontFamily: 'var(--font-sans)',
  fontSize: 12,
  color: 'var(--body)',
  padding: '8px 11px',
};
