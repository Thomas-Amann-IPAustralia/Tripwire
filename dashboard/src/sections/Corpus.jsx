import React, { useState, useMemo, useEffect } from 'react';
import { usePages, useGraphNodes, useGraphEdges } from '../hooks/useData.js';
import ErrorBanner from '../components/ErrorBanner.jsx';
import Embedding3D from '../visualisations/Embedding3D.jsx';
import KnowledgeGraph from '../visualisations/KnowledgeGraph.jsx';
import ContentMap from '../visualisations/ContentMap.jsx';
import BipartiteMap from '../visualisations/BipartiteMap.jsx';

const TABS = [
  '3D EMBEDDING SPACE',
  '2D KNOWLEDGE GRAPH',
  'CONTENT MAP',
  'SOURCE-CORPUS MAP',
];

class TabErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error) {
    console.error('[Corpus tab error]', error);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          height: '100%',
          flexDirection: 'column',
          gap: '8px',
          color: 'var(--state-error)',
          fontFamily: 'var(--font-mono)',
          fontSize: '11px',
        }}>
          <div style={{ fontFamily: 'var(--font-display)', fontSize: '18px' }}>
            VISUALISATION ERROR
          </div>
          {this.state.error?.message}
        </div>
      );
    }
    return this.props.children;
  }
}

export default function Corpus() {
  const [activeTab, setActiveTab] = useState(0);

  // Switch to the 2D Knowledge Graph tab when a highlight-graph-node event arrives
  useEffect(() => {
    const handler = () => setActiveTab(1);
    window.addEventListener('tripwire:highlight-graph-node', handler);
    return () => window.removeEventListener('tripwire:highlight-graph-node', handler);
  }, []);

  const { data: pagesRaw, error: pagesError } = usePages();
  const { data: nodesRaw } = useGraphNodes();
  const { data: edgesRaw } = useGraphEdges();

  const pages  = useMemo(() => (Array.isArray(pagesRaw?.data)  ? pagesRaw.data  : pagesRaw  ?? []), [pagesRaw]);
  const nodes  = useMemo(() => (Array.isArray(nodesRaw?.data)  ? nodesRaw.data  : nodesRaw  ?? []), [nodesRaw]);
  const edges  = useMemo(() => (Array.isArray(edgesRaw?.data)  ? edgesRaw.data  : edgesRaw  ?? []), [edgesRaw]);

  const stats = useMemo(() => {
    const totalChunks = pages.reduce((s, p) => s + (p.chunk_count ?? 0), 0);
    const clusterSet  = new Set(pages.map(p => p.cluster).filter(c => c != null));
    const lastIngested = pages.reduce((best, p) => {
      if (!p.last_ingested) return best;
      return (!best || p.last_ingested > best) ? p.last_ingested : best;
    }, null);
    return {
      pageCount:  pages.length,
      chunkCount: totalChunks,
      edgeCount:  edges.length,
      clusters:   clusterSet.size,
      lastIngested: lastIngested
        ? new Date(lastIngested).toLocaleDateString('en-AU', { day: '2-digit', month: 'short', year: 'numeric' }).toUpperCase()
        : '—',
    };
  }, [pages, edges]);

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

      {/* Section header */}
      <div style={{
        height: '80px', minHeight: '80px',
        display: 'flex', alignItems: 'center',
        padding: '0 24px', borderBottom: '1px solid var(--rule)',
      }}>
        <div>
          <div style={{
            fontFamily: 'var(--font-display)', fontSize: '50px',
            lineHeight: 1, color: 'var(--text-primary)',
          }}>
            CORPUS
          </div>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: '13px',
            color: 'var(--text-tertiary)', letterSpacing: '0.06em',
          }}>
            IPFR Knowledge Base
          </div>
        </div>
      </div>

      <ErrorBanner error={pagesError} />

      {/* Tab bar */}
      <div style={{
        display: 'flex', alignItems: 'flex-end',
        padding: '0 24px',
        borderBottom: '1px solid var(--rule)',
        gap: '32px',
        flexShrink: 0,
      }}>
        {TABS.map((label, i) => (
          <button
            key={label}
            onClick={() => setActiveTab(i)}
            style={{
              background: 'none', border: 'none', cursor: 'pointer',
              padding: '12px 0',
              fontFamily: 'var(--font-display)',
              fontSize: '17px',
              letterSpacing: '0.06em',
              color: activeTab === i ? 'var(--text-primary)' : 'var(--text-tertiary)',
              borderBottom: activeTab === i ? '2px solid var(--text-primary)' : '2px solid transparent',
              marginBottom: '-1px',
              transition: 'color 120ms ease, border-color 120ms ease',
              whiteSpace: 'nowrap',
            }}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Stats strip */}
      <div style={{
        padding: '6px 24px',
        borderBottom: '1px solid var(--rule)',
        fontFamily: 'var(--font-mono)',
        fontSize: '13px',
        color: 'var(--text-tertiary)',
        letterSpacing: '0.06em',
        flexShrink: 0,
      }}>
        PAGES: {stats.pageCount} · CHUNKS: {stats.chunkCount} · GRAPH EDGES: {stats.edgeCount} · CLUSTERS: {stats.clusters} · LAST INGESTED: {stats.lastIngested}
      </div>

      {/* Tab content */}
      <div style={{ flex: 1, overflow: 'hidden', position: 'relative' }}>
        <TabErrorBoundary key="tab0">
          <div style={{ position: 'absolute', inset: 0, display: activeTab === 0 ? 'block' : 'none' }}>
            <Embedding3D isActive={activeTab === 0} />
          </div>
        </TabErrorBoundary>

        <TabErrorBoundary key="tab1">
          <div style={{ position: 'absolute', inset: 0, display: activeTab === 1 ? 'block' : 'none' }}>
            <KnowledgeGraph nodes={nodes} edges={edges} isActive={activeTab === 1} />
          </div>
        </TabErrorBoundary>

        <TabErrorBoundary key="tab2">
          <div style={{ position: 'absolute', inset: 0, display: activeTab === 2 ? 'block' : 'none' }}>
            <ContentMap pages={pages} isActive={activeTab === 2} />
          </div>
        </TabErrorBoundary>

        <TabErrorBoundary key="tab3">
          <div style={{ position: 'absolute', inset: 0, display: activeTab === 3 ? 'block' : 'none' }}>
            <BipartiteMap isActive={activeTab === 3} />
          </div>
        </TabErrorBoundary>
      </div>
    </div>
  );
}
