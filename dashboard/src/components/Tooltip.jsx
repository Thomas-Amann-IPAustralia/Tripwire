import React, { useState, useRef, useCallback, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';

const HIDE_GRACE_MS = 300; // time allowed to move the pointer onto the tooltip

export default function Tooltip({ content, children, learnMoreHref }) {
  const [visible, setVisible] = useState(false);
  const [coords, setCoords] = useState({ top: 0, left: 0 });
  const showTimerRef = useRef(null);
  const hideTimerRef = useRef(null);
  const triggerRef = useRef(null);
  const navigate = useNavigate();

  const show = useCallback(() => {
    // Cancel any pending hide so moving between trigger and tooltip
    // doesn't dismiss it mid-travel.
    clearTimeout(hideTimerRef.current);
    clearTimeout(showTimerRef.current);
    showTimerRef.current = setTimeout(() => {
      const rect = triggerRef.current?.getBoundingClientRect();
      if (rect) {
        // Clamp so the 240px panel never renders off the right edge.
        const left = Math.min(rect.left, window.innerWidth - 252);
        setCoords({ top: rect.bottom + 6, left: Math.max(8, left) });
      }
      setVisible(true);
    }, 150);
  }, []);

  // Delayed hide: without the grace period the tooltip unmounted the moment
  // the pointer left the ⓘ trigger, making the "Learn more" link unclickable.
  const hide = useCallback(() => {
    clearTimeout(showTimerRef.current);
    clearTimeout(hideTimerRef.current);
    hideTimerRef.current = setTimeout(() => setVisible(false), HIDE_GRACE_MS);
  }, []);

  const hideNow = useCallback(() => {
    clearTimeout(showTimerRef.current);
    clearTimeout(hideTimerRef.current);
    setVisible(false);
  }, []);

  useEffect(() => () => {
    clearTimeout(showTimerRef.current);
    clearTimeout(hideTimerRef.current);
  }, []);

  const keepOpen = useCallback(() => {
    clearTimeout(hideTimerRef.current);
    clearTimeout(showTimerRef.current);
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
            padding: '10px 12px',
            zIndex: 1000,
            pointerEvents: learnMoreHref ? 'auto' : 'none',
            animation: 'tooltipFade 150ms ease both',
          }}
          onMouseEnter={learnMoreHref ? keepOpen : undefined}
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
                onClick={() => {
                  hideNow();
                  const [routePart, anchorPart] = (learnMoreHref || '').split('#');
                  navigate(routePart || learnMoreHref);
                  if (anchorPart) {
                    setTimeout(() => {
                      window.dispatchEvent(
                        new CustomEvent('tripwire:navigate-doc', { detail: anchorPart })
                      );
                    }, 100);
                  }
                }}
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
