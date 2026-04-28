import React, { useState, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';

export default function Tooltip({ content, children, learnMoreHref }) {
  const [visible, setVisible] = useState(false);
  const [coords, setCoords] = useState({ top: 0, left: 0 });
  const timerRef = useRef(null);
  const triggerRef = useRef(null);
  const navigate = useNavigate();

  const show = useCallback((e) => {
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      const rect = triggerRef.current?.getBoundingClientRect();
      if (rect) {
        setCoords({ top: rect.bottom + 6, left: rect.left });
      }
      setVisible(true);
    }, 150);
  }, []);

  const hide = useCallback(() => {
    clearTimeout(timerRef.current);
    setVisible(false);
  }, []);

  return (
    <>
      <span
        ref={triggerRef}
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
        style={{ display: 'inline-flex', alignItems: 'center' }}
      >
        {children}
      </span>
      {visible && (
        <div
          style={{
            position: 'fixed',
            top: coords.top,
            left: coords.left,
            width: '240px',
            background: 'var(--bg-secondary)',
            border: '1px solid var(--rule-accent)',
            borderRadius: '3px',
            padding: '10px 12px',
            zIndex: 1000,
            pointerEvents: learnMoreHref ? 'auto' : 'none',
            animation: 'tooltipFade 150ms ease both',
          }}
          onMouseEnter={learnMoreHref ? show : undefined}
          onMouseLeave={learnMoreHref ? hide : undefined}
        >
          <div style={{
            fontFamily: 'var(--font-body)',
            fontSize: '12px',
            color: 'var(--text-secondary)',
            lineHeight: 1.5,
          }}>
            {content}
          </div>
          {learnMoreHref && (
            <div style={{ marginTop: '8px', borderTop: '1px solid var(--rule)', paddingTop: '6px' }}>
              <button
                onClick={() => { hide(); navigate(learnMoreHref); }}
                style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: '10px',
                  color: 'var(--text-tertiary)',
                  background: 'none',
                  border: 'none',
                  cursor: 'pointer',
                  padding: 0,
                }}
                onMouseEnter={e => e.currentTarget.style.color = 'var(--text-secondary)'}
                onMouseLeave={e => e.currentTarget.style.color = 'var(--text-tertiary)'}
              >
                Learn more ↗
              </button>
            </div>
          )}
        </div>
      )}
      <style>{`
        @keyframes tooltipFade {
          from { opacity: 0; }
          to   { opacity: 1; }
        }
      `}</style>
    </>
  );
}
