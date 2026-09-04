import type {
  KnowledgeGraphEdge,
  KnowledgeGraphNode,
} from "../../api/types/knowledge";

export type GraphViewMode = "workspace" | "landscape" | "claims" | "evidence";

export interface GraphData {
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
}

export const VIEW_CONFIG: Record<GraphViewMode, {
  label: string;
  eyebrow: string;
  description: string;
  layout: "concentric" | "cose" | "breadthfirst";
}> = {
  workspace: {
    label: "Workspace 总览",
    eyebrow: "Workspace Map",
    description: "以论文和跨论文共享实体呈现研究空间，点击节点后逐层下钻到来源与证据。",
    layout: "cose",
  },
  landscape: {
    label: "研究全景",
    eyebrow: "Research Landscape",
    description: "从论文出发，理解工作区覆盖的方法、任务与数据集。",
    layout: "concentric",
  },
  claims: {
    label: "观点关系",
    eyebrow: "Claim Network",
    description: "聚焦观点与局限，查看支持、反驳、限定、扩展和比较关系。",
    layout: "cose",
  },
  evidence: {
    label: "证据溯源",
    eyebrow: "Evidence Trace",
    description: "沿着论文、原文提及、知识条目与规范实体追溯知识来源。",
    layout: "breadthfirst",
  },
};

export const RELATION_LABELS: Record<string, string> = {
  supports: "支持",
  contradicts: "反驳",
  qualifies: "限定",
  evaluates_on: "在数据集上评估",
  extends: "扩展",
  compares_with: "对比",
  related_to: "相关",
  contains: "包含知识",
  canonicalizes: "对应规范实体",
  mentioned_in: "包含原文提及",
  refers_to: "指向实体",
  evidences: "提供证据",
  paper_entity: "涉及实体",
};

export const TYPE_LABELS: Record<string, string> = {
  paper: "论文",
  method: "方法",
  task: "任务",
  dataset: "数据集",
  claim: "观点",
  limitation: "局限",
  evidence: "证据",
  canonical_entity: "规范实体",
  paper_mention: "原文提及",
};

export function resolvedNodeType(node: KnowledgeGraphNode): string {
  if (node.node_kind === "canonical_entity" && node.entity_type) return node.entity_type;
  if (node.node_kind !== "knowledge") return node.node_kind;
  return node.type;
}

function nodeAllowed(node: KnowledgeGraphNode, mode: GraphViewMode): boolean {
  const type = resolvedNodeType(node);
  if (mode === "workspace") {
    return node.node_kind === "paper" || node.node_kind === "canonical_entity";
  }
  if (mode === "landscape") {
    return node.node_kind === "paper"
      || node.node_kind === "canonical_entity"
      || (node.node_kind === "knowledge" && ["method", "task", "dataset"].includes(type));
  }
  if (mode === "claims") {
    return node.node_kind === "paper"
      || (node.node_kind === "knowledge" && ["claim", "limitation"].includes(type));
  }
  return node.node_kind === "paper"
    || node.node_kind === "paper_mention"
    || node.node_kind === "canonical_entity"
    || node.node_kind === "knowledge";
}

export function projectGraph(
  graph: GraphData,
  mode: GraphViewMode,
  options: { showRejected?: boolean; minConfidence?: number } = {},
): GraphData {
  const minConfidence = options.minConfidence ?? 0;
  const nodes = graph.nodes.filter((node) => (
    nodeAllowed(node, mode)
    && node.confidence >= minConfidence
    && (options.showRejected || !["rejected", "invalidated"].includes(node.status))
  ));
  const nodeIds = new Set(nodes.map((node) => node.id));
  const edges = graph.edges.filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target));
  return { nodes, edges };
}

export function mergeGraph(current: GraphData, incoming: GraphData): GraphData {
  const nodes = new Map(current.nodes.map((node) => [node.id, node]));
  const edges = new Map(current.edges.map((edge) => [edge.id, edge]));
  incoming.nodes.forEach((node) => nodes.set(node.id, node));
  incoming.edges.forEach((edge) => edges.set(edge.id, edge));
  const nodeIds = new Set(nodes.keys());
  return {
    nodes: [...nodes.values()],
    // 不完整或错误的响应不得创建悬空的 Cytoscape 边。
    // 身份是唯一的关联键；label 和 canonical name 只是展示值，
    // 绝不能合并不同论文中的局部 item。
    edges: [...edges.values()].filter(
      (edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target),
    ),
  };
}

export function connectedNodeIds(edges: KnowledgeGraphEdge[], nodeId: string): Set<string> {
  const ids = new Set<string>([nodeId]);
  edges.forEach((edge) => {
    if (edge.source === nodeId) ids.add(edge.target);
    if (edge.target === nodeId) ids.add(edge.source);
  });
  return ids;
}

export function branchGraph(graph: GraphData, nodeId: string | null): GraphData {
  if (!nodeId) return graph;
  const nodeIds = connectedNodeIds(graph.edges, nodeId);
  return {
    nodes: graph.nodes.filter((node) => nodeIds.has(node.id)),
    edges: graph.edges.filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target)),
  };
}

/**
 * 在语义视图中隐藏 canonical-entity 层。
 *
 * 知识项节点标签已经携带 canonical name，因此当多个同名项都指向一个同名实体节点时，
 * “对应规范实体”（canonicalizes）边和实体节点只是视觉噪声（例如 18 个
 * “GNNExplainer” 项 -> 一个 “GNNExplainer” 实体）。函数会移除 canonicalizes 边，
 * 然后删除没有其他连接的 canonical_entity 节点
 * （恰好存在真实语义边的实体会被保留）。
 */
export function hideEntityLayer(graph: GraphData): GraphData {
  const edges = graph.edges.filter((edge) => edge.relation_type !== "canonicalizes");
  const degree = new Map<string, number>();
  edges.forEach((edge) => {
    degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1);
  });
  const nodes = graph.nodes.filter(
    (node) => node.node_kind !== "canonical_entity" || (degree.get(node.id) ?? 0) > 0,
  );
  return { nodes, edges };
}

export function contentSummary(node: KnowledgeGraphNode): string {
  const preferred = ["statement", "description", "key_idea", "problem_addressed", "limitation_type"];
  for (const key of preferred) {
    const value = node.content?.[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  if (node.mention_text) return node.mention_text;
  if (node.paper_title) return node.paper_title;
  return node.label;
}

export function shortLabel(value: string, limit = 46): string {
  return value.length <= limit ? value : `${value.slice(0, limit - 1)}…`;
}

export function relationLabel(edge: KnowledgeGraphEdge): string {
  return edge.display_label || RELATION_LABELS[edge.relation_type] || edge.relation_type;
}
