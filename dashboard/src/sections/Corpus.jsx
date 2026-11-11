import React, { useState, useMemo, useEffect } from 'react';
import { usePages, useGraphNodes, useGraphEdges } from '../hooks/useData.js';
import ErrorBanner from '../components/ErrorBanner.jsx';
import Embedding3D from '../visualisations/Embedding3D.jsx';
import KnowledgeGraph from '../visualisations/KnowledgeGraph.jsx';
import ContentMap from '../visualisations/ContentMap.jsx';
import BipartiteMap from '../visualisations/BipartiteMap.jsx';
import { PageHeader, Callout, Icon } from '../components/ui.jsx';

const TABS = [
  { label: 'Embedding space (3D)', icon: 'layers',   hint: 'Every content chunk positioned by meaning; nearby points say similar things.' },
  { label: 'Knowledge graph',      icon: 'grid',     hint: 'Pages linked by embedding similarity, shared entities and internal links. Select a page for details.' },
  { label: 'Content map',          icon: 'file',     hint: 'The corpus as a treemap of topic clusters, sized by how much content each page carries.' },
  { label: 'Source to page map',   icon: 'source',   hint: 'Which monitored sources have triggered which IPFR pages over the full run history.' },
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
        <div style={{ padding: '24px' }}>
          <Callout tone="error" icon="alert" title="This visualisation could not be displayed">
            {this.state.error?.message}
          </Callout>
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
        ? new Date(lastIngested).toLocaleDateString('en-AU', { day: '2-digit', month: 'short', year: 'numeric' })
        : '—',
    };
  }, [pages, edges]);

  const statItems = [
    { label: 'Pages', value: stats.pageCount.toLocaleString() },
    { label: 'Chunks', value: stats.chunkCount.toLocaleString() },
    { label: 'Graph edges', value: stats.edgeCount.toLocaleString() },
    { label: 'Clusters', value: stats.clusters },
    { label: 'Last ingested', value: stats.lastIngested },
  ];

  return (
    <div className="tw-page is-full">
      <PageHeader
        eyebrow="Monitor · IPFR knowledge base"
        title="IPFR corpus"
        description="The IP First Response content Tripwire compares source changes against, shown four ways."
      >
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 22px', marginTop: '14px' }}>
          {statItems.map(({ label, value }) => (
            <div key={label} style={{ fontSize: '12.5px', color: 'var(--muted)' }}>
              {label} <strong className="tabular" style={{ color: 'var(--ink)', fontWeight: 650, marginLeft: '4px' }}>{value}</strong>
            </div>
          ))}
        </div>
      </PageHeader>

      <ErrorBanner error={pagesError} />

      <div className="tw-card is-clip" style={{ flex: 1, minHeight: '560px', display: 'flex', flexDirection: 'column' }}>
        {/* Tab bar */}
        <div className="tw-tabs" role="tablist" style={{ padding: '0 12px', flexShrink: 0 }}>
          {TABS.map((tab, i) => (
            <button
              key={tab.label}
              type="button"
              role="tab"
              aria-selected={activeTab === i}
              className={`tw-tab${activeTab === i ? ' active' : ''}`}
              onClick={() => setActiveTab(i)}
            >
              <Icon name={tab.icon} />
              {tab.label}
            </button>
          ))}
        </div>
        <div style={{ padding: '8px 16px', fontSize: '12px', color: 'var(--muted)', borderBottom: '1px solid var(--line-soft)', background: 'var(--surface-subtle)', flexShrink: 0 }}>
          {TABS[activeTab].hint}
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
    </div>
  );
}
