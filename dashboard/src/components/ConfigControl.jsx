import React, { useState } from 'react';
import Tooltip from './Tooltip.jsx';
import { Icon, Badge } from './ui.jsx';

function Toggle({ checked, onChange, disabled, label }) {
  return (
    <button
      type="button"
      role="switch"
      className="tw-switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => !disabled && onChange(!checked)}
    />
  );
}

function NumberStepper({ value, min, max, step, disabled, onChange, label }) {
  const fmt = v => step < 1 ? parseFloat(v.toFixed(step < 0.01 ? 3 : 2)) : Math.round(v);
  const dec = () => onChange(Math.max(min, fmt((value ?? min) - step)));
  const inc = () => onChange(Math.min(max, fmt((value ?? min) + step)));
  const decimals = step < 0.01 ? 3 : step < 1 ? 2 : 0;
  const displayVal = typeof value === 'number' ? (decimals > 0 ? value.toFixed(decimals) : value) : (min ?? 0);

  return (
    <div style={{
      display: 'inline-flex', alignItems: 'stretch', height: '32px',
      border: '1px solid var(--line-strong)', borderRadius: 'var(--radius-sm)',
      background: 'var(--surface)', overflow: 'hidden',
    }}>
      <button type="button" onClick={dec} disabled={disabled || (value ?? min) <= min} style={stepBtnStyle} aria-label={`Decrease ${label}`}>−</button>
      <span className="tabular" style={{
        fontSize: '13px', fontWeight: 600, color: 'var(--ink)',
        borderLeft: '1px solid var(--line)', borderRight: '1px solid var(--line)',
        padding: '0 10px', minWidth: '60px', textAlign: 'center',
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      }}>
        {displayVal}
      </span>
      <button type="button" onClick={inc} disabled={disabled || (value ?? min) >= max} style={stepBtnStyle} aria-label={`Increase ${label}`}>+</button>
    </div>
  );
}

const stepBtnStyle = {
  background: 'var(--surface-subtle)', border: 0, borderRadius: 0,
  color: 'var(--body)', fontSize: '15px', width: '32px',
  lineHeight: '1', userSelect: 'none',
};

/**
 * ConfigControl — reusable config parameter control.
 *
 * Props: paramKey, label, controlType ('toggle'|'slider'|'number'|'text'|'email'),
 *        min, max, step, readOnly, locked, lockWarning, nullable, docAnchor,
 *        infoText, value, onChange, isStaged, disabled
 */
export default function ConfigControl({
  paramKey,
  label,
  controlType,
  min = 0,
  max = 100,
  step = 1,
  readOnly = false,
  locked = false,
  lockWarning,
  nullable = false,
  docAnchor,
  infoText,
  value,
  onChange,
  isStaged = false,
  disabled = false,
}) {
  const [isUnlocked, setIsUnlocked] = useState(false);
  const [localNum, setLocalNum] = useState(
    value !== null && value !== undefined ? value : (min ?? 0)
  );

  function renderControl() {
    switch (controlType) {
      case 'toggle':
        return <Toggle checked={!!value} onChange={onChange} disabled={disabled} label={label} />;

      case 'slider': {
        const v = value ?? min;
        const decimals = step < 0.01 ? 3 : step < 0.1 ? 2 : step < 1 ? 2 : 0;
        return (
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <input
              type="range" min={min} max={max} step={step} value={v}
              disabled={disabled}
              aria-label={label}
              onChange={e => onChange(parseFloat(e.target.value))}
              style={{
                width: '150px',
                cursor: disabled ? 'default' : 'pointer', opacity: disabled ? 0.5 : 1,
              }}
            />
            <span className="tabular" style={{
              fontSize: '13px', fontWeight: 600, color: 'var(--ink)',
              minWidth: '44px', textAlign: 'right',
              padding: '4px 8px', background: 'var(--surface-subtle)',
              border: '1px solid var(--line)', borderRadius: 'var(--radius-sm)',
            }}>
              {Number(v).toFixed(decimals)}
            </span>
          </div>
        );
      }

      case 'number':
        if (nullable) {
          const isEnabled = value !== null && value !== undefined;
          return (
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <Toggle
                label={`Enable ${label}`}
                checked={isEnabled}
                onChange={on => {
                  if (on) { onChange(localNum); }
                  else { if (isEnabled) setLocalNum(value); onChange(null); }
                }}
                disabled={disabled}
              />
              {isEnabled ? (
                <NumberStepper
                  value={value} min={min} max={max} step={step} disabled={disabled} label={label}
                  onChange={v => { setLocalNum(v); onChange(v); }}
                />
              ) : (
                <span className="tw-small tw-muted" title="Set to null in the configuration">Not set</span>
              )}
            </div>
          );
        }
        return (
          <NumberStepper
            value={value ?? min} min={min} max={max} step={step}
            disabled={disabled} onChange={onChange} label={label}
          />
        );

      case 'text':
      case 'email': {
        const effectiveReadOnly = locked ? !isUnlocked : readOnly;
        return (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', alignItems: 'flex-end' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <input
                type={controlType}
                className="tw-input"
                value={value ?? ''}
                readOnly={effectiveReadOnly}
                disabled={disabled}
                aria-label={label}
                onChange={e => !effectiveReadOnly && onChange(e.target.value)}
                style={{
                  fontFamily: controlType === 'text' ? 'var(--font-code)' : undefined,
                  fontSize: controlType === 'text' ? '12px' : '13px',
                  width: '260px',
                  color: effectiveReadOnly ? 'var(--muted)' : 'var(--ink)',
                  background: effectiveReadOnly ? 'var(--surface-subtle)' : 'var(--surface)',
                  cursor: effectiveReadOnly ? 'default' : 'text',
                }}
              />
              {locked && (
                <button
                  type="button"
                  className={`tw-btn is-sm${isUnlocked ? ' is-danger' : ''}`}
                  onClick={() => setIsUnlocked(u => !u)}
                >
                  {isUnlocked ? 'Lock' : 'Unlock'}
                </button>
              )}
            </div>
            {locked && isUnlocked && lockWarning && (
              <div style={{
                fontSize: '12px', color: 'var(--warn)', maxWidth: '340px',
                background: 'var(--warn-soft)', border: '1px solid var(--warn-line)',
                borderRadius: 'var(--radius-sm)', padding: '6px 9px',
              }}>
                {lockWarning}
              </div>
            )}
          </div>
        );
      }

      default:
        return null;
    }
  }

  return (
    <div
      data-param={paramKey}
      style={{
        display: 'flex', alignItems: 'center', gap: '16px',
        padding: '12px 20px',
        boxShadow: isStaged ? 'inset 3px 0 0 var(--ochre)' : 'none',
        background: isStaged ? 'color-mix(in srgb, var(--ochre) 8%, transparent)' : 'transparent',
        borderBottom: '1px solid var(--line-soft)',
        opacity: disabled ? 0.6 : 1,
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <span style={{
            fontSize: '13.5px', fontWeight: 600,
            color: disabled ? 'var(--muted)' : 'var(--ink)',
            userSelect: 'none',
          }}>
            {label}
          </span>
          {isStaged && <Badge tone="warn">Unsaved</Badge>}
          {infoText && (
            <Tooltip
              content={infoText}
              learnMoreHref={docAnchor ? `/document#${docAnchor}` : undefined}
            >
              <span tabIndex={0} aria-label={`About ${label}`} style={{ display: 'inline-flex', color: 'var(--faint)', cursor: 'help' }}>
                <Icon name="info" size={15} />
              </span>
            </Tooltip>
          )}
        </div>
        <div className="tw-code" style={{ fontSize: '11px', color: 'var(--faint)', marginTop: '2px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {paramKey}
        </div>
      </div>
      <div style={{ flexShrink: 0 }}>
        {renderControl()}
      </div>
    </div>
  );
}
