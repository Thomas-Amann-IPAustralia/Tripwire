export default function ErrorBanner({ error }) {
  if (!error) return null;
  const msg = error?.message ?? String(error);
  const isDbMissing = msg.includes('503') || msg.includes('database_not_found');
  return (
    <div style={{
      fontFamily: 'var(--font-mono)',
      fontSize: '11px',
      color: 'var(--state-error)',
      background: 'rgba(201,64,32,0.08)',
      border: '1px solid var(--state-error)',
      padding: '8px 16px',
      margin: '0',
      flexShrink: 0,
    }}>
      {isDbMissing
        ? 'DATABASE UNAVAILABLE — check DATA_ROOT and ensure ipfr.sqlite is present'
        : msg}
    </div>
  );
}
