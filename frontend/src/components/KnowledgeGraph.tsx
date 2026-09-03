import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Alert,
  App,
  AutoComplete,
  Button,
  Card,
  Checkbox,
  Col,
  Collapse,
  Descriptions,
  Divider,
  Drawer,
  Empty,
  Flex,
  Grid,
  Input,
  InputNumber,
  Row,
  Segmented,
  Select,
  Space,
  Spin,
  Statistic,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import {
  AimOutlined,
  ApartmentOutlined,
  CompressOutlined,
  ExpandOutlined,
  EyeInvisibleOutlined,
  FilterOutlined,
  FullscreenExitOutlined,
  FullscreenOutlined,
  MinusOutlined,
  PlusOutlined,
  ReloadOutlined,
  RollbackOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import CytoscapeComponent from "react-cytoscapejs";
import type { Core, EventObject } from "cytoscape";
import { useNavigate } from "react-router-dom";
import { useTheme } from "../state/theme";
import knowledgeApi from "../api/knowledge";
import paperApi from "../api/paper";
import type { Paper } from "../api/types/domain";
import type {
  KnowledgeGraphEdge,
  KnowledgeGraphNode,
  KnowledgeGraphSearchResult,
} from "../api/types/knowledge";
import {
  branchGraph,
  connectedNodeIds,
  contentSummary,
  hideEntityLayer,
  type GraphData,
  type GraphViewMode,
  mergeGraph,
  projectGraph,
  relationLabel,
  resolvedNodeType,
  shortLabel,
  TYPE_LABELS,
  VIEW_CONFIG,
} from "./knowledgeGraph/graphUtils";

const { Paragraph, Text, Title } = Typography;
const { useBreakpoint } = Grid;

const PAGE_SIZE = 40;

const TYPE_COLORS: Record<string, string> = {
  paper: "#475569",
  method: "#3b82f6",
  task: "#22a06b",
  dataset: "#f59e0b",
  claim: "#8b5cf6",
  limitation: "#e85d75",
  evidence: "#7c3aed",
  canonical_entity: "#0f766e",
  paper_mention: "#a78bfa",
};

const RELATION_COLORS: Record<string, string> = {
  supports: "#168f5b",
  contradicts: "#dc3f57",
  qualifies: "#d97706",
  evaluates_on: "#2563eb",
  extends: "#7c3aed",
  compares_with: "#0891b2",
  related_to: "#94a3b8",
  contains: "#cbd5e1",
  canonicalizes: "#94a3b8",
  mentioned_in: "#a78bfa",
  refers_to: "#8b5cf6",
  evidences: "#7c3aed",
};

const STATUS_OPTIONS = [
  ["extracted_candidate", "AI 待审核"],
  ["evidence_backed_proposal", "有证据候选"],
  ["human_confirmed", "人工确认"],
  ["rejected", "已拒绝"],
] as const;

interface GraphFilters {
  type?: string;
  status?: string;
  paperId?: string;
  relationType?: string;
  minConfidence?: number;
  includeRelatedPapers?: boolean;
}

interface ViewState {
  graph: GraphData;
  initialGraph: GraphData;
  history: GraphData[];
  expandedNodeIds: string[];
  nextOffset: number;
  totalNodes: number;
  totalEdges: number;
  hasMore: boolean;
  truncated: boolean;
  truncationReason?: string;
  nodeCounts: Record<string, number>;
  relationCounts: Record<string, number>;
  workspaceCounts: Record<string, number>;
  loaded: boolean;
  signature: string;
}

const emptyViewState = (): ViewState => ({
  graph: { nodes: [], edges: [] },
  initialGraph: { nodes: [], edges: [] },
  history: [],
  expandedNodeIds: [],
  nextOffset: 0,
  totalNodes: 0,
  totalEdges: 0,
  hasMore: false,
  truncated: false,
  nodeCounts: {},
  relationCounts: {},
  workspaceCounts: {},
  loaded: false,
  signature: "",
});

function errorMessage(error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: { message?: string } } } })
    .response?.data?.detail;
  return detail?.message || (error as Error).message || "请求失败";
}

function filterSignature(filters: GraphFilters): string {
  return JSON.stringify(filters);
}

function typeOptions(mode: GraphViewMode) {
  const values = mode === "workspace" || mode === "landscape"
    ? ["method", "task", "dataset"]
    : mode === "claims"
      ? ["claim", "limitation"]
      : ["method", "task", "dataset", "claim", "limitation", "evidence"];
  return values.map((value) => ({ value, label: TYPE_LABELS[value] ?? value }));
}

function nodeShape(node: KnowledgeGraphNode): string {
  if (node.node_kind === "paper") return "round-rectangle";
  if (node.node_kind === "canonical_entity") return "hexagon";
  if (node.node_kind === "paper_mention") return "ellipse";
  if (resolvedNodeType(node) === "claim" || resolvedNodeType(node) === "limitation") {
    return "round-rectangle";
  }
  return "ellipse";
}

function layoutOptions(mode: GraphViewMode) {
  if (mode === "evidence") {
    return { name: "breadthfirst", directed: true, spacingFactor: 1.25, padding: 42 };
  }
  if (mode === "workspace") {
    return {
      name: "cose",
      animate: false,
      randomize: true,
      padding: 56,
      nodeRepulsion: 7800,
      idealEdgeLength: 112,
      edgeElasticity: 100,
      gravity: 0.15,
      numIter: 1200,
    };
  }
  if (mode === "landscape") {
    return {
      name: "cose",
      animate: false,
      randomize: true,
      padding: 48,
      nodeRepulsion: 6500,
      idealEdgeLength: 78,
      edgeElasticity: 120,
      nestingFactor: 0.8,
      gravity: 0.18,
      numIter: 1400,
    };
  }
  return { name: "cose", animate: false, padding: 42, nodeRepulsion: 8000, idealEdgeLength: 120 };
}

function fitGraphReadably(cy: Core): void {
  if (cy.nodes().length === 0) return;
  cy.fit(undefined, 42);
  if (cy.zoom() < 0.68) {
    cy.zoom(0.68);
    cy.center();
  }
}

function Inspector({
  node,
  edges,
  nodes,
  papers,
  workspaceId,
  expanded,
  loading,
  onExpand,
  onBranch,
  onRestore,
  onEnterPaperView,
}: {
  node: KnowledgeGraphNode | null;
  edges: KnowledgeGraphEdge[];
  nodes: KnowledgeGraphNode[];
  papers: Paper[];
  workspaceId: string;
  expanded: boolean;
  loading: boolean;
  onExpand: () => void;
  onBranch: () => void;
  onRestore: () => void;
  onEnterPaperView: (paperId: string) => void;
}) {
  const navigate = useNavigate();
  if (!node) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={
          <Space direction="vertical" size={2}>
            <Text strong>选择一个节点查看详情</Text>
            <Text type="secondary">单击查看关系，双击可展开一层邻居。</Text>
          </Space>
        }
      />
    );
  }

  const connected = edges.filter((edge) => edge.source === node.id || edge.target === node.id);
  const nodeMap = new Map(nodes.map((item) => [item.id, item]));
  const kind = resolvedNodeType(node);
  const year = node.content?.year;
  const source = node.content?.source;
  const parseStatus = node.content?.parse_status;
  const aliases = node.content?.aliases;
  const entityAliases = node.aliases ?? (Array.isArray(aliases) ? aliases : []);
  const supportingPapers = papers.filter((paper) => node.supporting_paper_ids?.includes(paper.id));

  return (
    <div>
      <Space wrap style={{ marginBottom: 12 }}>
        <Tag color={TYPE_COLORS[kind] ?? "default"}>{node.display_type || TYPE_LABELS[kind] || kind}</Tag>
        <Tag>{Math.round(node.confidence * 100)}% 置信度</Tag>
        <Tag color={node.status === "human_confirmed" ? "green" : "gold"}>{node.status}</Tag>
      </Space>
      <Title level={5} style={{ marginTop: 0 }}>{node.label}</Title>
      <Paragraph style={{ whiteSpace: "pre-wrap" }}>{contentSummary(node)}</Paragraph>

      <Descriptions column={1} size="small">
        {node.node_kind === "paper" && <>
          <Descriptions.Item label="年份">{typeof year === "number" ? year : "—"}</Descriptions.Item>
          <Descriptions.Item label="来源">{typeof source === "string" ? source : "—"}</Descriptions.Item>
          <Descriptions.Item label="解析状态">{typeof parseStatus === "string" ? parseStatus : node.status}</Descriptions.Item>
        </>}
        {node.paper_title && <Descriptions.Item label="来源论文">{node.paper_title}</Descriptions.Item>}
        {node.entity_type && <Descriptions.Item label="实体类型">{TYPE_LABELS[node.entity_type] ?? node.entity_type}</Descriptions.Item>}
        {entityAliases.length > 0 && (
          <Descriptions.Item label="别名">{entityAliases.join("、")}</Descriptions.Item>
        )}
        <Descriptions.Item label="直接关系">{connected.length}</Descriptions.Item>
        {node.node_kind === "canonical_entity" ? <>
          <Descriptions.Item label="覆盖论文数">{node.paper_count ?? 0}</Descriptions.Item>
          <Descriptions.Item label="提及次数">{node.mention_count ?? 0}</Descriptions.Item>
          <Descriptions.Item label="知识条目数">{node.knowledge_item_count ?? 0}</Descriptions.Item>
          <Descriptions.Item label="证据数">{node.evidence_count ?? 0}</Descriptions.Item>
          <Descriptions.Item label="人工确认数">{node.confirmed_item_count ?? 0}</Descriptions.Item>
        </> : <Descriptions.Item label="证据数量">{node.evidence_count ?? 0}</Descriptions.Item>}
      </Descriptions>

      <Flex wrap gap={8} style={{ marginTop: 16 }}>
        <Button type="primary" icon={<ExpandOutlined />} loading={loading} disabled={expanded} onClick={onExpand}>
          {expanded ? "邻居已展开" : "展开一层邻居"}
        </Button>
        <Button icon={<AimOutlined />} onClick={onBranch}>仅查看此分支</Button>
        <Button onClick={onRestore}>恢复完整视图</Button>
      </Flex>

      {(node.node_kind === "knowledge" || node.knowledge_item_id) && (
        <Flex wrap gap={8} style={{ marginTop: 8 }}>
          <Button onClick={() => navigate(`/workspaces/${workspaceId}/knowledge`)}>在审核工作台打开</Button>
          {kind === "claim" && (
            <Button onClick={() => navigate(`/workspaces/${workspaceId}/discover`)}>用于 Discover</Button>
          )}
        </Flex>
      )}
      {node.node_kind === "paper" && (
        <Flex wrap gap={8} style={{ marginTop: 8 }}>
          <Button type="primary" onClick={() => node.paper_id && onEnterPaperView(node.paper_id)}>
            进入论文视角
          </Button>
          <Button onClick={() => navigate(`/workspaces/${workspaceId}/papers`)}>打开论文列表</Button>
        </Flex>
      )}

      {node.node_kind === "canonical_entity" && (
        <>
          <Divider orientation="left">关联论文</Divider>
          {supportingPapers.length === 0 ? (
            <Text type="secondary">当前没有可展示的来源论文</Text>
          ) : (
            <Space direction="vertical" size={4} style={{ width: "100%" }}>
              {supportingPapers.map((paper) => (
                <Button
                  key={paper.id}
                  type="link"
                  style={{ padding: 0, height: "auto", textAlign: "left" }}
                  onClick={() => onEnterPaperView(paper.id)}
                >
                  {paper.title}
                </Button>
              ))}
            </Space>
          )}
        </>
      )}

      <Divider orientation="left">关联内容</Divider>
      {connected.length === 0 ? <Text type="secondary">当前已加载范围内没有直接关系</Text> : (
        <Space direction="vertical" style={{ width: "100%" }} size={10}>
          {connected.slice(0, 14).map((edge) => {
            const otherId = edge.source === node.id ? edge.target : edge.source;
            const other = nodeMap.get(otherId);
            return (
              <Card key={edge.id} size="small" styles={{ body: { padding: 10 } }}>
                <Space direction="vertical" size={2} style={{ width: "100%" }}>
                  <Space wrap>
                    <Tag color={RELATION_COLORS[edge.relation_type] ?? "default"}>{relationLabel(edge)}</Tag>
                    <Text type="secondary">{edge.source === node.id ? "→" : "←"}</Text>
                    <Text strong>{other?.label || edge.source_label || edge.target_label || otherId}</Text>
                  </Space>
                  <Text type="secondary">关系置信度 {Math.round(edge.confidence * 100)}%</Text>
                  {(edge.occurrence_count ?? 0) > 0 && (
                    <Text type="secondary">
                      出现 {edge.occurrence_count} 次 · 覆盖 {edge.paper_count ?? 0} 篇论文 · 证据 {edge.evidence_count ?? 0} 条
                    </Text>
                  )}
                </Space>
              </Card>
            );
          })}
        </Space>
      )}

      <Collapse
        ghost
        style={{ marginTop: 16 }}
        items={[{
          key: "developer",
          label: "开发信息",
          children: (
            <>
              <Text copyable type="secondary">节点 ID：{node.id}</Text>
              <pre style={{ whiteSpace: "pre-wrap", background: "var(--gm-surface-3)", padding: 12, borderRadius: 8 }}>
                {JSON.stringify(node.content, null, 2)}
              </pre>
            </>
          ),
        }]}
      />
    </div>
  );
}

export default function KnowledgeGraph({ workspaceId }: { workspaceId: string }) {
  const { message } = App.useApp();
  const { isDark } = useTheme();
  const screens = useBreakpoint();
  const cyRef = useRef<Core | null>(null);
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const pendingFocusRef = useRef<string | null>(null);
  const expandNodeRef = useRef<(nodeId: string) => void>(() => undefined);
  const lastNodeTapRef = useRef<{ nodeId: string; at: number } | null>(null);
  const hoverClearTimerRef = useRef<number | null>(null);
  const [mode, setMode] = useState<GraphViewMode>("workspace");
  const [views, setViews] = useState<Record<GraphViewMode, ViewState>>({
    workspace: emptyViewState(),
    landscape: emptyViewState(),
    claims: emptyViewState(),
    evidence: emptyViewState(),
  });
  const [filters, setFilters] = useState<Record<GraphViewMode, GraphFilters>>({
    workspace: {}, landscape: {}, claims: {}, evidence: {},
  });
  const [papers, setPapers] = useState<Paper[]>([]);
  const [loading, setLoading] = useState(false);
  const [expanding, setExpanding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [branchNodeId, setBranchNodeId] = useState<string | null>(null);
  const [filtersCollapsed, setFiltersCollapsed] = useState(false);
  const [showRelationLabels, setShowRelationLabels] = useState(false);
  const [showLowConfidence, setShowLowConfidence] = useState(false);
  // P2: hide the redundant canonical-entity layer in the semantic views by
  // default (many same-named items pointing at one same-named entity node).
  // Evidence view always keeps it as part of the provenance chain.
  const [showEntityLayer, setShowEntityLayer] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [searchText, setSearchText] = useState("");
  const [searching, setSearching] = useState(false);
  const [searchResults, setSearchResults] = useState<KnowledgeGraphSearchResult[]>([]);
  const [hoveredEdge, setHoveredEdge] = useState<KnowledgeGraphEdge | null>(null);
  const [hoveredNode, setHoveredNode] = useState<KnowledgeGraphNode | null>(null);
  const active = views[mode];
  const activeFilters = filters[mode];

  const updateView = useCallback((viewMode: GraphViewMode, updater: (state: ViewState) => ViewState) => {
    setViews((current) => ({ ...current, [viewMode]: updater(current[viewMode]) }));
  }, []);

  const requestParams = useCallback((viewMode: GraphViewMode, offset = 0) => {
    const current = filters[viewMode];
    return {
      projection_mode: viewMode,
      type: current.type,
      status: current.status,
      paper_id: current.paperId,
      relation_type: current.relationType,
      min_confidence: current.minConfidence,
      include_related_papers: current.includeRelatedPapers,
      limit: PAGE_SIZE,
      offset,
    } as const;
  }, [filters]);

  const runLayout = useCallback((viewMode = mode) => {
    const cy = cyRef.current;
    if (!cy || cy.nodes().length === 0) return;
    cy.layout(layoutOptions(viewMode)).run();
    window.setTimeout(() => fitGraphReadably(cy), 100);
  }, [mode]);

  const loadInitial = useCallback(async (viewMode: GraphViewMode, force = false) => {
    const signature = filterSignature(filters[viewMode]);
    const current = views[viewMode];
    if (!force && current.loaded && current.signature === signature) return;
    setLoading(true);
    setError(null);
    try {
      const response = await knowledgeApi.graph(workspaceId, requestParams(viewMode, 0));
      const graph = { nodes: response.nodes ?? [], edges: response.edges ?? [] };
      updateView(viewMode, () => ({
        graph,
        initialGraph: graph,
        history: [],
        expandedNodeIds: [],
        nextOffset: PAGE_SIZE,
        totalNodes: response.total_nodes,
        totalEdges: response.total_edges,
        hasMore: response.has_more,
        truncated: response.truncated,
        truncationReason: response.truncation_reason ?? undefined,
        nodeCounts: response.node_counts ?? {},
        relationCounts: response.relation_counts ?? {},
        workspaceCounts: response.workspace_counts ?? {},
        loaded: true,
        signature,
      }));
      if (viewMode === mode) {
        setSelectedNodeId(null);
        setBranchNodeId(null);
        window.setTimeout(() => runLayout(viewMode), 30);
      }
    } catch (requestError) {
      const nextError = errorMessage(requestError);
      setError(nextError);
      message.error(`知识图谱加载失败：${nextError}`);
    } finally {
      setLoading(false);
    }
  }, [filters, message, mode, requestParams, runLayout, updateView, views, workspaceId]);

  useEffect(() => {
    void loadInitial(mode);
  }, [loadInitial, mode]);

  useEffect(() => {
    paperApi.list(workspaceId, { limit: 200 })
      .then((response) => setPapers(response.items))
      .catch(() => setPapers([]));
  }, [workspaceId]);

  useEffect(() => {
    const term = searchText.trim();
    if (term.length < 2) {
      setSearchResults([]);
      return undefined;
    }
    const timer = window.setTimeout(async () => {
      setSearching(true);
      try {
        const response = await knowledgeApi.searchGraphNodes(workspaceId, {
          q: term,
          projection_mode: mode,
          limit: 12,
        });
        setSearchResults(response.items ?? []);
      } catch {
        setSearchResults([]);
      } finally {
        setSearching(false);
      }
    }, 300);
    return () => window.clearTimeout(timer);
  }, [mode, searchText, workspaceId]);

  const displayGraph = useMemo(() => {
    const projected = projectGraph(active.graph, mode, {
      showRejected: activeFilters.status === "rejected",
      minConfidence: showLowConfidence ? 0 : Math.max(activeFilters.minConfidence ?? 0, 0.6),
    });
    const layerFiltered = (mode !== "evidence" && mode !== "workspace" && !showEntityLayer && activeFilters.relationType !== "canonicalizes")
      ? hideEntityLayer(projected)
      : projected;
    return branchGraph(layerFiltered, branchNodeId);
  }, [active.graph, activeFilters.minConfidence, activeFilters.relationType, activeFilters.status, branchNodeId, mode, showEntityLayer, showLowConfidence]);

  const selectedNode = displayGraph.nodes.find((node) => node.id === selectedNodeId)
    ?? active.graph.nodes.find((node) => node.id === selectedNodeId)
    ?? null;
  const highlightedIds = useMemo(
    () => selectedNodeId ? connectedNodeIds(displayGraph.edges, selectedNodeId) : new Set<string>(),
    [displayGraph.edges, selectedNodeId],
  );
  const activeLayout = useMemo(() => layoutOptions(mode), [mode]);

  const elements = useMemo(() => {
    return [
      ...displayGraph.nodes.map((node) => {
        const type = resolvedNodeType(node);
        const isFocused = node.id === selectedNodeId;
        const isNeighbor = highlightedIds.has(node.id) && !isFocused;
        const dimmed = selectedNodeId !== null && !highlightedIds.has(node.id);
        const labelLimit = node.node_kind === "paper" ? 38 : type === "claim" ? 34 : 25;
        const label = shortLabel(node.display_label || node.label, labelLimit);
        return {
          data: {
            id: node.id,
            label,
            fullLabel: node.label,
            color: TYPE_COLORS[type] ?? "#64748b",
            shape: nodeShape(node),
            nodeKind: node.node_kind,
            nodeType: type,
            opacity: Math.max(0.56, node.confidence),
            borderStyle: node.status === "human_confirmed" ? "solid" : "dashed",
          },
          classes: [
            isFocused ? "focused" : "",
            isNeighbor ? "neighbor" : "",
            dimmed ? "dimmed" : "",
            active.expandedNodeIds.includes(node.id) ? "expanded" : "",
          ].filter(Boolean).join(" "),
        };
      }),
      ...displayGraph.edges.map((edge) => {
        const activeEdge = selectedNodeId !== null && (edge.source === selectedNodeId || edge.target === selectedNodeId);
        const dimmed = selectedNodeId !== null && !activeEdge;
        return {
          data: {
            id: edge.id,
            source: edge.source,
            target: edge.target,
            label: showRelationLabels || activeEdge ? relationLabel(edge) : "",
            color: RELATION_COLORS[edge.relation_type] ?? "#94a3b8",
            lineStyle: edge.relation_type === "contradicts" ? "dashed" : "solid",
          },
          classes: [activeEdge ? "active" : "", dimmed ? "dimmed" : ""].filter(Boolean).join(" "),
        };
      }),
    ];
  }, [active.expandedNodeIds, displayGraph.edges, displayGraph.nodes, highlightedIds, selectedNodeId, showRelationLabels]);

  // P0.5-4: canvas chrome adapts to the theme; node/relation hues are
  // mid-saturation and stay identical in both themes.
  const canvasInk = isDark ? "rgba(255, 255, 255, .92)" : "#172033";
  const canvasLabelBg = isDark ? "#141414" : "#ffffff";
  const stylesheet = useMemo(() => [
    {
      selector: "node",
      style: {
        label: "data(label)", "background-color": "data(color)", shape: "data(shape)",
        opacity: "data(opacity)", "border-color": isDark ? "#1f1f1f" : "#ffffff", "border-width": 3,
        "border-style": "data(borderStyle)", color: canvasInk, "font-size": 10,
        "text-wrap": "wrap", "text-max-width": 125, "text-valign": "bottom",
        "text-halign": "center", "text-background-color": canvasLabelBg,
        "text-background-opacity": 0.92, "text-background-padding": 3,
        "text-margin-y": 7, width: 50, height: 50,
      },
    },
    { selector: "node[nodeKind = 'paper']", style: { width: 142, height: 54, "font-size": 11, "font-weight": "bold", "text-valign": "center", "text-margin-y": 0 } },
    { selector: "node[nodeType = 'claim']", style: { width: 118, height: 60, "font-weight": "bold", "text-valign": "center", "text-margin-y": 0 } },
    { selector: "node[nodeType = 'limitation']", style: { width: 106, height: 56, "text-valign": "center", "text-margin-y": 0 } },
    { selector: "node[nodeKind = 'paper_mention']", style: { width: 32, height: 32, "font-size": 8 } },
    {
      selector: "edge",
      style: {
        label: "data(label)", "line-color": "data(color)", "target-arrow-color": "data(color)",
        "target-arrow-shape": "triangle", "curve-style": "bezier", "line-style": "data(lineStyle)",
        width: 1.5, "font-size": 9, color: isDark ? "rgba(255, 255, 255, .78)" : "#334155", "text-background-color": canvasLabelBg,
        "text-background-opacity": 0.94, "text-background-padding": 3,
      },
    },
    { selector: "node.focused", style: { "border-color": isDark ? "#e6f0ff" : "#0f172a", "border-width": 5, opacity: 1, "z-index": 20 } },
    { selector: "node.neighbor", style: { "border-color": "#60a5fa", "border-width": 4, opacity: 1 } },
    { selector: "node.expanded", style: { "underlay-color": isDark ? "rgba(96, 165, 250, .3)" : "#dbeafe", "underlay-opacity": 0.7, "underlay-padding": 7 } },
    { selector: "edge.active", style: { width: 3.5, opacity: 1, "z-index": 10 } },
    { selector: ".dimmed", style: { opacity: 0.2 } },
  ], [isDark, canvasInk, canvasLabelBg]);

  const expandNode = useCallback(async (nodeId: string) => {
    if (active.expandedNodeIds.includes(nodeId) || expanding) return;
    setExpanding(true);
    try {
      const response = await knowledgeApi.graphNeighbors(workspaceId, nodeId, {
        depth: 1,
        relation_type: activeFilters.relationType,
        projection_mode: mode,
      });
      const incoming = { nodes: response.nodes ?? [], edges: response.edges ?? [] };
      updateView(mode, (state) => ({
        ...state,
        history: [...state.history, state.graph],
        graph: mergeGraph(state.graph, incoming),
        expandedNodeIds: [...state.expandedNodeIds, nodeId],
      }));
      pendingFocusRef.current = nodeId;
      setSelectedNodeId(nodeId);
      message.success(`已展开 ${incoming.nodes.length} 个相关节点`);
      window.setTimeout(() => runLayout(mode), 50);
    } catch (requestError) {
      message.error(`邻居展开失败：${errorMessage(requestError)}`);
    } finally {
      setExpanding(false);
    }
  }, [active.expandedNodeIds, activeFilters.relationType, expanding, message, mode, runLayout, updateView, workspaceId]);

  expandNodeRef.current = (nodeId: string) => { void expandNode(nodeId); };

  const handleCy = useCallback((cy: Core) => {
    cyRef.current = cy;
    cy.minZoom(0.45);
    cy.maxZoom(2.5);
    cy.removeAllListeners();
    cy.on("tap", "node", (event: EventObject) => {
      const nodeId = event.target.id();
      const now = Date.now();
      const previous = lastNodeTapRef.current;
      setSelectedNodeId(nodeId);
      if (previous && previous.nodeId === nodeId && now - previous.at < 360) {
        lastNodeTapRef.current = null;
        expandNodeRef.current(nodeId);
      } else {
        lastNodeTapRef.current = { nodeId, at: now };
      }
    });
    cy.on("tap", (event: EventObject) => {
      if (event.target === cy) setSelectedNodeId(null);
    });
    const cancelHoverClear = () => {
      if (hoverClearTimerRef.current !== null) {
        window.clearTimeout(hoverClearTimerRef.current);
        hoverClearTimerRef.current = null;
      }
    };
    const scheduleHoverClear = () => {
      cancelHoverClear();
      hoverClearTimerRef.current = window.setTimeout(() => {
        setHoveredNode(null);
        setHoveredEdge(null);
      }, 100);
    };
    cy.on("mouseover", "node", (event: EventObject) => {
      cancelHoverClear();
      const id = event.target.id();
      setHoveredNode(displayGraph.nodes.find((node) => node.id === id) ?? null);
      setHoveredEdge(null);
    });
    cy.on("mouseout", "node", scheduleHoverClear);
    cy.on("mouseover", "edge", (event: EventObject) => {
      cancelHoverClear();
      const id = event.target.id();
      setHoveredEdge(displayGraph.edges.find((edge) => edge.id === id) ?? null);
      setHoveredNode(null);
    });
    cy.on("mouseout", "edge", scheduleHoverClear);
  }, [displayGraph.edges, displayGraph.nodes]);

  useEffect(() => () => {
    if (hoverClearTimerRef.current !== null) {
      window.clearTimeout(hoverClearTimerRef.current);
    }
  }, []);

  useEffect(() => {
    const targetId = pendingFocusRef.current;
    if (!targetId || !cyRef.current) return;
    const target = cyRef.current.$id(targetId);
    if (target.length > 0) {
      cyRef.current.animate({ center: { eles: target }, zoom: 1.2 }, { duration: 350 });
      pendingFocusRef.current = null;
    }
  }, [elements]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const cy = cyRef.current;
      if (!cy) return;
      cy.resize();
      fitGraphReadably(cy);
    }, 180);
    return () => window.clearTimeout(timer);
  }, [filtersCollapsed, screens.lg, selectedNodeId]);

  const loadMore = async () => {
    if (!active.hasMore || loading) return;
    setLoading(true);
    try {
      const response = await knowledgeApi.graph(workspaceId, requestParams(mode, active.nextOffset));
      const incoming = { nodes: response.nodes ?? [], edges: response.edges ?? [] };
      updateView(mode, (state) => ({
        ...state,
        graph: mergeGraph(state.graph, incoming),
        nextOffset: state.nextOffset + PAGE_SIZE,
        hasMore: response.has_more,
        totalNodes: response.total_nodes,
        totalEdges: response.total_edges,
        truncated: response.truncated,
        truncationReason: response.truncation_reason ?? undefined,
      }));
      message.success(`新增加载 ${incoming.nodes.length} 个节点`);
      window.setTimeout(() => runLayout(mode), 50);
    } catch (requestError) {
      message.error(`继续加载失败：${errorMessage(requestError)}`);
    } finally {
      setLoading(false);
    }
  };

  const undoExpansion = () => {
    updateView(mode, (state) => {
      if (state.history.length === 0) return state;
      return {
        ...state,
        graph: state.history[state.history.length - 1],
        history: state.history.slice(0, -1),
        expandedNodeIds: state.expandedNodeIds.slice(0, -1),
      };
    });
    setBranchNodeId(null);
    setSelectedNodeId(null);
  };

  const restoreInitial = () => {
    updateView(mode, (state) => ({
      ...state,
      graph: state.initialGraph,
      history: [],
      expandedNodeIds: [],
    }));
    setSelectedNodeId(null);
    setBranchNodeId(null);
    window.setTimeout(() => runLayout(mode), 30);
  };

  const selectSearchResult = async (nodeId: string) => {
    const result = searchResults.find((item) => item.node_id === nodeId);
    if (result) setSearchText(result.label);
    if (!active.graph.nodes.some((node) => node.id === nodeId)) {
      await expandNode(nodeId);
    }
    pendingFocusRef.current = nodeId;
    setSelectedNodeId(nodeId);
  };

  const setActiveFilter = <K extends keyof GraphFilters>(key: K, value: GraphFilters[K]) => {
    setFilters((current) => ({ ...current, [mode]: { ...current[mode], [key]: value } }));
  };

  const resetFilters = () => {
    setFilters((current) => ({ ...current, [mode]: {} }));
  };

  const enterPaperView = (paperId: string) => {
    setFilters((current) => ({
      ...current,
      landscape: { ...current.landscape, paperId, includeRelatedPapers: false },
    }));
    setMode("landscape");
    setSelectedNodeId(null);
    setBranchNodeId(null);
  };

  const toggleFullscreen = async () => {
    if (!document.fullscreenElement) {
      await canvasRef.current?.requestFullscreen();
      setIsFullscreen(true);
    } else {
      await document.exitFullscreen();
      setIsFullscreen(false);
    }
  };

  const workspaceCounts = active.workspaceCounts;
  const emptyDescription = workspaceCounts.papers === 0
    ? "当前课题空间还没有论文，请先导入论文。"
    : workspaceCounts.parsed_papers === 0
      ? "已有论文但尚未完成解析，请先解析 PDF。"
      : workspaceCounts.knowledge_items === 0
        ? "论文已经解析，但还没有知识条目，请先运行知识抽取。"
        : "当前视角或筛选条件下没有匹配内容。";

  const groupedSearchResults = new Map<string, KnowledgeGraphSearchResult[]>();
  searchResults.forEach((result) => {
    const group = TYPE_LABELS[result.type] ?? TYPE_LABELS[result.node_kind] ?? result.type;
    groupedSearchResults.set(group, [...(groupedSearchResults.get(group) ?? []), result]);
  });
  const searchOptions = [...groupedSearchResults.entries()].map(([group, results]) => ({
    label: group,
    options: results.map((result) => ({
      value: result.node_id,
      label: (
        <Space direction="vertical" size={0} style={{ width: "100%", minWidth: 0 }}>
          <Text ellipsis>{result.label}</Text>
          {result.paper_title && <Text type="secondary" ellipsis style={{ fontSize: 12 }}>{result.paper_title}</Text>}
        </Space>
      ),
    })),
  }));

  const inspector = (
    <Inspector
      node={selectedNode}
      edges={displayGraph.edges}
      nodes={displayGraph.nodes}
      papers={papers}
      workspaceId={workspaceId}
      expanded={selectedNode ? active.expandedNodeIds.includes(selectedNode.id) : false}
      loading={expanding}
      onExpand={() => selectedNode && void expandNode(selectedNode.id)}
      onBranch={() => selectedNode && setBranchNodeId(selectedNode.id)}
      onRestore={() => setBranchNodeId(null)}
      onEnterPaperView={enterPaperView}
    />
  );

  return (
    <div ref={canvasRef} style={{ background: "var(--gm-surface-2)", padding: isFullscreen ? 20 : 0 }}>
      <Card className="gm-graph-overview">
        <Flex className="gm-graph-overview-header" justify="space-between" align="flex-start" wrap gap={12}>
          <div className="gm-graph-overview-copy">
            <Text type="secondary" className="gm-graph-overview-eyebrow">{VIEW_CONFIG[mode].eyebrow}</Text>
            <Title level={4}>知识图谱探索</Title>
            <Paragraph type="secondary">{VIEW_CONFIG[mode].description}</Paragraph>
          </div>
          <Segmented
            value={mode}
            onChange={(value) => {
              setMode(value as GraphViewMode);
              setSelectedNodeId(null);
              setBranchNodeId(null);
            }}
            options={(Object.keys(VIEW_CONFIG) as GraphViewMode[]).map((key) => ({
              value: key,
              label: VIEW_CONFIG[key].label,
            }))}
          />
        </Flex>

        <Row className="gm-graph-overview-stats" gutter={[10, 10]}>
          <Col xs={12} md={6}><Card className="gm-graph-stat-card" size="small"><Statistic title="论文" value={workspaceCounts.papers ?? 0} suffix="篇" /></Card></Col>
          <Col xs={12} md={6}><Card className="gm-graph-stat-card" size="small"><Statistic title={mode === "workspace" ? "规范实体" : "知识条目"} value={mode === "workspace" ? (workspaceCounts.canonical_entities ?? 0) : (workspaceCounts.knowledge_items ?? 0)} suffix={mode === "workspace" ? "个" : "条"} /></Card></Col>
          <Col xs={12} md={6}><Card className="gm-graph-stat-card" size="small"><Statistic title={mode === "workspace" ? "原文提及" : "人工确认"} value={mode === "workspace" ? (workspaceCounts.mentions ?? 0) : (workspaceCounts.confirmed_items ?? 0)} suffix="条" /></Card></Col>
          <Col xs={12} md={6}><Card className="gm-graph-stat-card" size="small"><Statistic title={mode === "workspace" ? "证据跨度" : "语义关系"} value={mode === "workspace" ? (workspaceCounts.evidence_spans ?? 0) : (workspaceCounts.relations ?? 0)} suffix="条" /></Card></Col>
        </Row>

        <Alert
          className="gm-graph-overview-tip"
          type="info"
          showIcon
          closable
          message="探索提示：单击节点查看一跳关系，双击展开邻居；三个视角会分别保留本次浏览状态。"
        />

        <Flex className="gm-graph-overview-search" gap={10} align="center" wrap>
          <AutoComplete
            value={searchText}
            options={searchOptions}
            onSearch={setSearchText}
            onChange={setSearchText}
            onSelect={(value) => void selectSearchResult(value)}
            notFoundContent={searching ? <Spin size="small" /> : "没有匹配节点"}
            style={{ width: 360, maxWidth: "100%" }}
          >
            <Input allowClear prefix={<SearchOutlined />} placeholder="搜索论文、观点、方法、任务或数据集" />
          </AutoComplete>
          <Text type="secondary"><SearchOutlined /> 搜索可定位尚未加载的论文或知识节点</Text>
        </Flex>
      </Card>

      <Row gutter={12} style={{ marginTop: 12 }} align="stretch">
        {!filtersCollapsed && (
          <Col xs={24} lg={4}>
            <Card
              title={<Space><FilterOutlined />筛选</Space>}
              extra={<Button type="text" size="small" icon={<EyeInvisibleOutlined />} onClick={() => setFiltersCollapsed(true)} />}
              style={{ height: "100%" }}
            >
              <Space direction="vertical" size={14} style={{ width: "100%" }}>
                <div><Text type="secondary">知识类型</Text><Select allowClear value={activeFilters.type} options={typeOptions(mode)} onChange={(value) => setActiveFilter("type", value)} style={{ width: "100%", marginTop: 6 }} placeholder="全部类型" /></div>
                <div><Text type="secondary">审核状态</Text><Select allowClear value={activeFilters.status} options={STATUS_OPTIONS.map(([value, label]) => ({ value, label }))} onChange={(value) => setActiveFilter("status", value)} style={{ width: "100%", marginTop: 6 }} placeholder="全部状态" /></div>
                <div><Text type="secondary">来源论文</Text><Select allowClear showSearch optionFilterProp="label" value={activeFilters.paperId} options={papers.map((paper) => ({ value: paper.id, label: paper.title }))} onChange={(value) => setActiveFilter("paperId", value)} style={{ width: "100%", marginTop: 6 }} placeholder="全部论文" /></div>
                <Checkbox
                  checked={activeFilters.includeRelatedPapers ?? false}
                  disabled={!activeFilters.paperId}
                  onChange={(event) => setActiveFilter("includeRelatedPapers", event.target.checked)}
                >显示共享实体关联论文</Checkbox>
                {activeFilters.paperId && <Text type="secondary" style={{ display: "block", fontSize: 12, marginTop: -8 }}>默认严格只看所选论文，开启后才展开共享实体的来源论文。</Text>}
                <div><Text type="secondary">关系类型</Text><Select allowClear value={activeFilters.relationType} options={Object.keys(RELATION_COLORS).map((value) => ({ value, label: relationLabel({ relation_type: value } as KnowledgeGraphEdge) }))} onChange={(value) => setActiveFilter("relationType", value)} style={{ width: "100%", marginTop: 6 }} placeholder="全部关系" /></div>
                <div><Text type="secondary">最低置信度</Text><InputNumber min={0} max={1} step={0.1} value={activeFilters.minConfidence} onChange={(value) => setActiveFilter("minConfidence", value ?? undefined)} style={{ width: "100%", marginTop: 6 }} placeholder="0.0 - 1.0" /></div>
                <Button block onClick={resetFilters}>清除筛选</Button>
                <Text type="secondary" style={{ fontSize: 12 }}>修改筛选后会重建当前视角，其他视角的探索状态不受影响。</Text>
              </Space>
            </Card>
          </Col>
        )}

        <Col
          xs={24}
          lg={
            filtersCollapsed
              ? selectedNode ? 19 : 24
              : selectedNode ? 15 : 20
          }
        >
          <Card
            title={
              <Space wrap>
                {filtersCollapsed && <Button type="text" icon={<FilterOutlined />} onClick={() => setFiltersCollapsed(false)}>筛选</Button>}
                <Text strong>{VIEW_CONFIG[mode].label}</Text>
                {branchNodeId && <Tag color="blue">局部分支</Tag>}
              </Space>
            }
            extra={
              <Space size={4} wrap>
                <Tooltip title="放大"><Button size="small" icon={<PlusOutlined />} onClick={() => cyRef.current?.zoom(cyRef.current.zoom() * 1.2)} /></Tooltip>
                <Tooltip title="缩小"><Button size="small" icon={<MinusOutlined />} onClick={() => cyRef.current?.zoom(cyRef.current.zoom() / 1.2)} /></Tooltip>
                <Tooltip title="适应画布并保持文字可读"><Button size="small" icon={<CompressOutlined />} onClick={() => cyRef.current && fitGraphReadably(cyRef.current)} /></Tooltip>
                <Tooltip title="重新布局"><Button size="small" icon={<ApartmentOutlined />} onClick={() => runLayout(mode)} /></Tooltip>
                <Tooltip title="撤销上次邻居展开"><Button size="small" icon={<RollbackOutlined />} disabled={active.history.length === 0} onClick={undoExpansion} /></Tooltip>
                <Tooltip title="恢复初始图谱"><Button size="small" icon={<ReloadOutlined />} onClick={restoreInitial} /></Tooltip>
                <Tooltip title={isFullscreen ? "退出全屏" : "全屏查看"}><Button size="small" icon={isFullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />} onClick={() => void toggleFullscreen()} /></Tooltip>
              </Space>
            }
            styles={{ body: { padding: 0 } }}
          >
            <Flex justify="space-between" align="center" wrap gap={8} style={{ padding: "10px 14px", borderBottom: "1px solid var(--gm-border-2)" }}>
              <Space wrap>
                <Checkbox checked={showRelationLabels} onChange={(event) => setShowRelationLabels(event.target.checked)}>显示全部关系标签</Checkbox>
                <Checkbox checked={showLowConfidence} onChange={(event) => setShowLowConfidence(event.target.checked)}>显示低置信度节点</Checkbox>
                {mode !== "evidence" && mode !== "workspace" && (
                  <Checkbox checked={showEntityLayer} onChange={(event) => setShowEntityLayer(event.target.checked)}>
                    显示规范实体层
                  </Checkbox>
                )}
              </Space>
              <Text type="secondary">画布显示 {displayGraph.nodes.length} 个节点 · {displayGraph.edges.length} 条关系</Text>
            </Flex>
            {active.truncated && (
              <Alert
                type="warning"
                showIcon
                banner
                message={active.truncationReason === "edge_limit"
                  ? "关系数量较多，当前只显示部分关系，请通过搜索或展开节点继续查看。"
                  : "当前图谱已按服务端上限截断，请通过搜索、筛选或展开节点渐进加载。"}
              />
            )}

            <div
              style={{
                height: 38,
                padding: "8px 14px",
                boxSizing: "border-box",
                background: hoveredEdge || hoveredNode ? "var(--gm-hover)" : "var(--gm-surface-2)",
                borderBottom: "1px solid var(--gm-border)",
                overflow: "hidden",
                whiteSpace: "nowrap",
                textOverflow: "ellipsis",
              }}
            >
              {hoveredEdge ? (
                <Text>
                  {hoveredEdge.source_label || hoveredEdge.source} → <Text strong>{relationLabel(hoveredEdge)}</Text> → {hoveredEdge.target_label || hoveredEdge.target} · {Math.round(hoveredEdge.confidence * 100)}%
                </Text>
              ) : hoveredNode ? (
                <Text>
                  <Tag color={TYPE_COLORS[resolvedNodeType(hoveredNode)] ?? "default"}>{hoveredNode.display_type || TYPE_LABELS[resolvedNodeType(hoveredNode)]}</Tag>
                  <Text strong>{hoveredNode.label}</Text> · {Math.round(hoveredNode.confidence * 100)}%
                </Text>
              ) : (
                <Text type="secondary">悬停查看完整节点名称或关系说明，单击节点可固定查看详情。</Text>
              )}
            </div>
            {error && active.graph.nodes.length > 0 && (
              <Alert type="warning" showIcon message="刷新失败，已保留当前画布" description={error} action={<Button size="small" onClick={() => void loadInitial(mode, true)}>重试</Button>} />
            )}

            <div style={{ height: isFullscreen ? "calc(100vh - 300px)" : "clamp(650px, 72vh, 900px)", minHeight: 480, background: isDark ? "radial-gradient(circle at center, #1f1f1f 0%, #161616 72%, #141414 100%)" : "radial-gradient(circle at center, #ffffff 0%, #f8fafc 72%, #f1f5f9 100%)" }}>
              {loading && !active.loaded ? (
                <Flex justify="center" align="center" style={{ height: "100%" }}><Spin tip="正在构建知识图谱…" /></Flex>
              ) : displayGraph.nodes.length === 0 ? (
                <Flex justify="center" align="center" style={{ height: "100%", padding: 32 }}>
                  <Empty description={emptyDescription}>
                    {activeFilters.type || activeFilters.status || activeFilters.paperId || activeFilters.minConfidence !== undefined
                      ? <Button onClick={resetFilters}>清除筛选</Button>
                      : error ? <Button type="primary" onClick={() => void loadInitial(mode, true)}>重新加载</Button> : null}
                  </Empty>
                </Flex>
              ) : (
                <CytoscapeComponent
                  elements={elements}
                  stylesheet={stylesheet}
                  layout={activeLayout}
                  cy={handleCy}
                  style={{ width: "100%", height: "100%" }}
                />
              )}
            </div>

            <Flex justify="space-between" align="center" wrap gap={10} style={{ padding: "12px 14px", borderTop: "1px solid #edf0f5" }}>
              <Space wrap size={[6, 6]}>
                {(mode === "workspace" ? ["paper", "canonical_entity", "method", "task", "dataset"] : mode === "landscape" ? ["paper", "method", "task", "dataset"] : mode === "claims" ? ["paper", "claim", "limitation"] : ["paper", "paper_mention", "method", "task", "dataset", "claim"]).map((type) => (
                  <Tag key={type} color={TYPE_COLORS[type]}>{TYPE_LABELS[type]}</Tag>
                ))}
              </Space>
              <Space wrap>
                <Text type="secondary">已加载 {active.graph.nodes.length} / {active.totalNodes} 个节点</Text>
                {active.hasMore && <Button type="primary" ghost loading={loading} onClick={() => void loadMore()}>继续加载节点</Button>}
              </Space>
            </Flex>
          </Card>
        </Col>

        {screens.lg && selectedNode && (
          <Col lg={5}>
            <Card title="节点详情" style={{ height: "100%" }} styles={{ body: { maxHeight: 810, overflowY: "auto" } }}>
              {inspector}
            </Card>
          </Col>
        )}
      </Row>

      {!screens.lg && (
        <Drawer title="节点详情" open={selectedNode !== null} onClose={() => setSelectedNodeId(null)} width="min(92vw, 520px)">
          {inspector}
        </Drawer>
      )}
    </div>
  );
}
