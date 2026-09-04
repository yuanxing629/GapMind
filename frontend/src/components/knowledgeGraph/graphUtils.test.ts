import { describe, expect, it } from "vitest";
import type { KnowledgeGraphEdge, KnowledgeGraphNode } from "../../api/types/knowledge";
import { branchGraph, hideEntityLayer, mergeGraph, projectGraph } from "./graphUtils";

function node(id: string, type: string, nodeKind = "knowledge"): KnowledgeGraphNode {
  return {
    id, label: id, type, node_kind: nodeKind, workspace_id: "ws",
    confidence: 0.9, status: "extracted_candidate", content: {},
    importance_score: 0.8, relation_count: 0, evidence_count: 0, paper_count: 0,
    mention_count: 0, knowledge_item_count: 0, confirmed_item_count: 0,
    aliases: [], supporting_paper_ids: [], supporting_paper_ids_truncated: false,
  };
}

function edge(id: string, source: string, target: string): KnowledgeGraphEdge {
  return {
    id, source, target, relation_type: "related_to", confidence: 0.8, payload: {},
    occurrence_count: 0, paper_count: 0, evidence_count: 0,
    supporting_paper_ids: [], supporting_item_ids: [],
  };
}

describe("knowledge graph utilities", () => {
  const graph = {
    nodes: [
      node("paper", "paper", "paper"),
      node("method", "method"),
      node("claim", "claim"),
      node("mention", "paper_mention", "paper_mention"),
    ],
    edges: [edge("pm", "paper", "method"), edge("pc", "paper", "claim"), edge("cm", "claim", "mention")],
  };

  it("projects workspace mode to papers and canonical entities only", () => {
    const projected = projectGraph({
      nodes: [node("paper", "paper", "paper"), node("entity", "method", "canonical_entity")],
      edges: [edge("pe", "paper", "entity")],
    }, "workspace");
    expect(projected.nodes.map((item) => item.id)).toEqual(["paper", "entity"]);
    expect(projected.edges.map((item) => item.id)).toEqual(["pe"]);
  });

  it("projects the three views without dangling edges", () => {
    expect(projectGraph(graph, "landscape").nodes.map((item) => item.id)).toEqual(["paper", "method"]);
    expect(projectGraph(graph, "claims").nodes.map((item) => item.id)).toEqual(["paper", "claim"]);
    expect(projectGraph(graph, "evidence").nodes).toHaveLength(4);
    for (const mode of ["landscape", "claims", "evidence"] as const) {
      const projected = projectGraph(graph, mode);
      const ids = new Set(projected.nodes.map((item) => item.id));
      expect(projected.edges.every((item) => ids.has(item.source) && ids.has(item.target))).toBe(true);
    }
  });

  it("merges appended batches and neighbor expansions by id", () => {
    const merged = mergeGraph(
      { nodes: [node("a", "method")], edges: [] },
      { nodes: [node("a", "method"), node("b", "task")], edges: [edge("ab", "a", "b")] },
    );
    expect(merged.nodes.map((item) => item.id)).toEqual(["a", "b"]);
    expect(merged.edges.map((item) => item.id)).toEqual(["ab"]);
  });

  it("keeps same-label nodes distinct and drops edges with unloaded endpoints", () => {
    const first = { ...node("item-1", "method"), label: "Same method" };
    const second = { ...node("item-2", "method"), label: "Same method" };
    const merged = mergeGraph(
      { nodes: [first], edges: [] },
      { nodes: [second], edges: [edge("valid", "item-1", "item-2"), edge("dangling", "item-2", "missing")] },
    );
    expect(merged.nodes.map((item) => item.id)).toEqual(["item-1", "item-2"]);
    expect(merged.edges.map((item) => item.id)).toEqual(["valid"]);
  });

  it("limits a branch to the selected node and its one-hop neighbors", () => {
    const result = branchGraph(
      { nodes: [node("a", "claim"), node("b", "claim"), node("c", "claim")], edges: [edge("ab", "a", "b")] },
      "a",
    );
    expect(result.nodes.map((item) => item.id)).toEqual(["a", "b"]);
  });

  it("hides canonicalizes edges and the entity nodes they leave isolated", () => {
    const graph = {
      nodes: [
        node("gcn-a", "method"),
        node("gcn-b", "method"),
        node("entity-gcn", "canonical_entity", "canonical_entity"),
        node("m1", "method"),
      ],
      edges: [
        { ...edge("ca", "gcn-a", "entity-gcn"), relation_type: "canonicalizes" },
        { ...edge("cb", "gcn-b", "entity-gcn"), relation_type: "canonicalizes" },
        edge("mm", "gcn-a", "m1"),
      ],
    };
    const result = hideEntityLayer(graph);
    expect(result.edges.map((item) => item.id)).toEqual(["mm"]);
    expect(result.nodes.map((item) => item.id)).toEqual(["gcn-a", "gcn-b", "m1"]);
  });

  it("keeps a canonical_entity node that still has a non-canonicalizes edge", () => {
    const graph = {
      nodes: [
        node("gcn", "method"),
        node("entity-gcn", "canonical_entity", "canonical_entity"),
      ],
      edges: [
        { ...edge("ca", "gcn", "entity-gcn"), relation_type: "canonicalizes" },
        edge("sem", "entity-gcn", "gcn"),
      ],
    };
    const result = hideEntityLayer(graph);
    expect(result.nodes.map((item) => item.id)).toContain("entity-gcn");
    expect(result.edges.map((item) => item.id)).toContain("sem");
  });

  it("leaves graphs without canonical_entity nodes untouched", () => {
    const graph = {
      nodes: [node("a", "method"), node("b", "task")],
      edges: [edge("ab", "a", "b")],
    };
    expect(hideEntityLayer(graph)).toEqual(graph);
  });
});
