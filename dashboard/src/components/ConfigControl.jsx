import React, { useState } from 'react';
import Tooltip from './Tooltip.jsx';

function Toggle({ checked, onChange, disabled }) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      onClick={() => !disabled && onChange(!checked)}
      style={{
        width: '36px', height: '20px', borderRadius: '10px',
        border: 'none', cursor: disabled ? 'default' : 'pointer',
        background: checked
          ? (disabled ? 'var(--state-inactive)' : 'var(--stage-1)')
          : 'var(--bg-accent)',
        position: 'relative', transition: 'background 150ms',
        flexShrink: 0, outline: 'none',
      }}
    >
      <span style={{
        position: 'absolute', top: '3px',
        left: checked ? '19px' : '3px',
        width: '14px', height: '14px', borderRadius: '50%',
        background: 'var(--text-primary)', transition: 'left 150ms', display: 'block',
      }} />
    </button>
  );
}

function NumberStepper({ value, min, max, step, disabled, onChange }) {
  const fmt = v => step < 1 ? parseFloat(v.toFixed(step < 0.01 ? 3 : 2)) : Math.round(v);
  const dec = () => onChange(Math.max(min, fmt((value ?? min) - step)));
  const inc = () => onChange(Math.min(max, fmt((value ?? min) + step)));
  const decimals = step < 0.01 ? 3 : step < 1 ? 2 : 0;
  const displayVal = typeof value === 'number' ? (decimals > 0 ? value.toFixed(decimals) : value) : (min ?? 0);

  return (
    <div style={{ display: 'flex', alignItems: 'center' }}>
      <button onClick={dec} disabled={disabled || (value ?? min) <= min} style={stepBtnStyle}>−</button>
      <span style={{
        fontFamily: 'var(--font-mono)', fontSize: '12px', color: 'var(--text-primary)',
        background: 'var(--bg-tertiary)', border: '1px solid var(--rule-accent)',
        borderLeft: 'none', borderRight: 'none',
        padding: '3px 10px', minWidth: '52px', textAlign: 'center', lineHeight: '1.4',
        display: 'inline-block',
      }}>
        {displayVal}
      </span>
      <button onClick={inc} disabled={disabled || (value ?? min) >= max} style={stepBtnStyle}>+</button>
    </div>
  );
}

const stepBtnStyle = {
  background: 'var(--bg-tertiary)', border: '1px solid var(--rule-accent)',
  cursor: 'pointer', color: 'var(--text-secondary)',
  fontFamily: 'var(--font-mono)', fontSize: '13px',
  padding: '3px 8px', lineHeight: '1', userSelect: 'none',
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
        return <Toggle checked={!!value} onChange={onChange} disabled={disabled} />;

      case 'slider': {
        const v = value ?? min;
        const decimals = step < 0.01 ? 3 : step < 0.1 ? 2 : step < 1 ? 2 : 0;
        return (
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <input
              type="range" min={min} max={max} step={step} value={v}
              disabled={disabled}
              onChange={e => onChange(parseFloat(e.target.value))}
              style={{
                width: '110px', accentColor: 'var(--stage-4)',
                cursor: disabled ? 'default' : 'pointer', opacity: disabled ? 0.5 : 1,
              }}
            />
            <span style={{
              fontFamily: 'var(--font-mono)', fontSize: '12px', color: 'var(--text-primary)',
              minWidth: '40px', textAlign: 'right',
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
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <Toggle
                checked={isEnabled}
                onChange={on => {
                  if (on) { onChange(localNum); }
                  else { if (isEnabled) setLocalNum(value); onChange(null); }
                }}
                disabled={disabled}
              />
              {isEnabled ? (
                <NumberStepper
                  value={value} min={min} max={max} step={step} disabled={disabled}
                  onChange={v => { setLocalNum(v); onChange(v); }}
                />
              ) : (
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)' }}>
                  NULL
                </span>
              )}
            </div>
          );
        }
        return (
          <NumberStepper
            value={value ?? min} min={min} max={max} step={step}
            disabled={disabled} onChange={onChange}
          />
        );

      case 'text':
      case 'email': {
        const effectiveReadOnly = locked ? !isUnlocked : readOnly;
        return (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <input
                type={controlType}
                value={value ?? ''}
                readOnly={effectiveReadOnly}
                disabled={disabled}
                onChange={e => !effectiveReadOnly && onChange(e.target.value)}
                style={{
                  fontFamily: 'var(--font-mono)', fontSize: '11px',
                  color: effectiveReadOnly ? 'var(--text-secondary)' : 'var(--text-primary)',
                  background: 'var(--bg-tertiary)', border: '1px solid var(--rule-accent)',
                  padding: '4px 8px', width: '180px', outline: 'none',
                  opacity: disabled ? 0.5 : 1,
                  cursor: effectiveReadOnly ? 'default' : 'text',
                }}
              />
              {locked && (
                <button
                  onClick={() => setIsUnlocked(u => !u)}
                  style={{
                    background: 'none', border: '1px solid var(--rule)',
                    cursor: 'pointer',
                    color: isUnlocked ? 'var(--state-warn)' : 'var(--text-tertiary)',
                    fontFamily: 'var(--font-mono)', fontSize: '9px',
                    padding: '3px 8px', letterSpacing: '0.05em',
                  }}
                >
                  {isUnlocked ? 'LOCK' : 'UNLOCK'}
                </button>
              )}
            </div>
            {locked && isUnlocked && lockWarning && (
              <div style={{
                fontFamily: 'var(--font-mono)', fontSize: '9px',
                color: 'var(--state-warn)', letterSpacing: '0.04em',
                borderLeft: '2px solid var(--state-warn)', paddingLeft: '6px',
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
        display: 'flex', alignItems: 'center', gap: '12px',
        padding: '8px 12px',
        borderLeft: isStaged ? '3px solid var(--state-warn)' : '3px solid transparent',
        borderBottom: '1px solid var(--rule)',
        opacity: disabled ? 0.65 : 1,
        background: 'transparent',
      }}
    >
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: '5px', minWidth: 0 }}>
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: '10px',
          color: disabled ? 'var(--text-tertiary)' : 'var(--text-secondary)',
          letterSpacing: '0.04em', textTransform: 'uppercase',
          userSelect: 'none', whiteSpace: 'nowrap',
          overflow: 'hidden', textOverflow: 'ellipsis',
        }}>
          {label}
        </span>
        {infoText && (
          <Tooltip
            content={infoText}
            learnMoreHref={docAnchor ? `/document#${docAnchor}` : undefined}
          >
            <span style={{
              fontFamily: 'var(--font-mono)', fontSize: '9px',
              color: 'var(--text-tertiary)', cursor: 'default', userSelect: 'none',
            }}>
              ⓘ
            </span>
          </Tooltip>
        )}
      </div>
      <div style={{ flexShrink: 0 }}>
        {renderControl()}
      </div>
    </div>
  );
}
