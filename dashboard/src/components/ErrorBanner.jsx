import { Callout } from './ui.jsx';

export default function ErrorBanner({ error }) {
  if (!error) return null;
  const msg = error?.message ?? String(error);
  const isDbMissing = msg.includes('503') || msg.includes('database_not_found');
  return (
    <Callout tone="error" icon="alert" title={isDbMissing ? 'Database unavailable' : 'Something went wrong loading this data'} style={{ marginBottom: '16px' }}>
      {isDbMissing
        ? 'Check DATA_ROOT and make sure ipfr.sqlite is present.'
        : msg}
    </Callout>
  );
}
