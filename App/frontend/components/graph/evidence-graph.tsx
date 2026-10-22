"use client";

import { memo, useMemo } from "react";
import { Background, Controls, Handle, MarkerType, MiniMap, Position, ReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import type { GraphResponse, Label } from "@/lib/types";
import { LABEL_COLOR } from "@/components/claims/badges";
import { cn } from "@/lib/utils";

type NodeData = { title: string; subtitle?: string; kind: string; label?: Label; raw: Record<string, any> }; // eslint-disable-line @typescript-eslint/no-explicit-any

const LABEL_TEXT: Record<Label, string> = { SUPPORTS: "supports", CONTRADICTS: "contradicts", AMBIGUOUS: "ambiguous", NEUTRAL: "neutral" };

const GraphNodeView = memo(function GraphNodeView({ data, selected }: NodeProps<Node<NodeData>>) {
  const color = data.label ? LABEL_COLOR[data.label] : data.kind === "claim" ? "var(--primary)" : "var(--border)";
  return (
    <div
      className={cn(
        "max-w-[230px] border bg-card px-3 py-2 text-left text-xs",
        data.kind === "claim" && "max-w-[300px] border-2 bg-hl-soft font-serif text-[0.85rem]",
        data.kind === "related_document" && "border-dashed",
        selected && "shadow-[0_0_0_3px_var(--hl)]",
      )}
      style={{ borderColor: color, borderLeftWidth: data.kind === "evidence" ? 4 : undefined }}
    >
      <Handle type="target" position={Position.Left} className="!size-1.5 !bg-border" />
      <div className="font-semibold leading-snug">{data.title}</div>
      {data.subtitle && <div className="mt-0.5 line-clamp-3 text-muted-foreground">{data.subtitle}</div>}
      {data.label && <div className="mt-1 font-medium" style={{ color }}>{LABEL_TEXT[data.label]}</div>}
      <Handle type="source" position={Position.Right} className="!size-1.5 !bg-border" />
    </div>
  );
});

const nodeTypes = { card: GraphNodeView };

export function buildFlow(g: GraphResponse): { nodes: Node<NodeData>[]; edges: Edge[] } {
  const ev = g.nodes.filter((n) => n.type === "evidence");
  const side = (l: Label) => (l === "SUPPORTS" ? -1 : l === "CONTRADICTS" ? 1 : 0);
  const pos: Record<string, { x: number; y: number }> = { claim: { x: 0, y: 0 } };
  const cols: Record<number, number> = { [-1]: 0, [1]: 0, [0]: 0 };
  ev.forEach((n) => {
    const s = side(n.data.label);
    const i = cols[s]++;
    pos[n.id] = s === 0 ? { x: -240 + i * 260, y: 300 + Math.floor(i / 4) * 110 } : { x: s * 330, y: -260 + i * 95 };
  });
  // documents sit beside the first evidence item that cites them
  const docY: Record<string, number> = {};
  const docSide: Record<string, number> = {};
  g.edges.filter((e) => e.type === "part_of").forEach((e) => {
    if (!(e.target in docY)) {
      docY[e.target] = pos[e.source]?.y ?? 0;
      const evNode = ev.find((n) => n.id === e.source);
      docSide[e.target] = evNode ? side(evNode.data.label) || -1 : -1;
    }
  });
  const used: Record<string, number> = {};
  g.nodes.filter((n) => n.type === "document").forEach((n) => {
    const s = docSide[n.id] ?? -1;
    let y = docY[n.id] ?? 0;
    const key = `${s}:${Math.round(y / 60)}`;
    used[key] = (used[key] || 0) + 1;
    y += (used[key] - 1) * 55;
    pos[n.id] = { x: s * 600, y };
  });
  let rl = 0, rr = 0;
  g.nodes.filter((n) => n.type === "related_document").forEach((n) => {
    const link = g.edges.find((e) => (e.source === n.id && pos[e.target]) || (e.target === n.id && pos[e.source]));
    const anchor = link ? pos[link.source === n.id ? link.target : link.source] : undefined;
    const s = anchor && anchor.x > 0 ? 1 : -1;
    pos[n.id] = { x: s * 860, y: s > 0 ? -260 + rr++ * 80 : -260 + rl++ * 80 };
  });

  const nodes: Node<NodeData>[] = g.nodes.map((n) => ({
    id: n.id,
    type: "card",
    position: pos[n.id] ?? { x: 0, y: 500 },
    data: {
      kind: n.type,
      label: n.type === "evidence" ? n.data.label : undefined,
      title: n.type === "claim" ? "Claim" : n.type === "evidence" ? `E${n.data.rank} · ${n.data.document_id}` : n.data.id || n.label,
      subtitle: n.type === "claim" ? n.data.text : n.type === "evidence" ? n.data.span : `${n.data.doc_type ?? ""} · ${n.data.status ?? ""}`,
      raw: { ...n.data, nodeType: n.type, id: n.id },
    },
  }));
  const edges: Edge[] = g.edges.map((e) => {
    const stance = e.type === "supports" || e.type === "contradicts" || e.type === "ambiguous" || e.type === "neutral";
    const color = stance ? LABEL_COLOR[e.type.toUpperCase() as Label] : "var(--muted-foreground)";
    return {
      id: e.id,
      source: e.source,
      target: e.target,
      label: stance ? undefined : e.label + (e.data.inferred ? " (inferred)" : ""),
      labelStyle: { fontSize: 10, fill: "var(--muted-foreground)" },
      style: {
        stroke: color,
        strokeWidth: stance ? 2 : 1,
        strokeDasharray: e.type === "contradicts" ? "6 4" : e.data.inferred ? "2 3" : undefined,
      },
      markerEnd: { type: MarkerType.ArrowClosed, color, width: 14, height: 14 },
      data: e.data,
    };
  });
  return { nodes, edges };
}

export function EvidenceGraph({ graph, onSelect }: { graph: GraphResponse; onSelect: (n: NodeData["raw"] | null) => void }) {
  const flow = useMemo(() => buildFlow(graph), [graph]);
  return (
    <ReactFlow
      nodes={flow.nodes}
      edges={flow.edges}
      nodeTypes={nodeTypes}
      fitView
      fitViewOptions={{ padding: 0.05 }}
      minZoom={0.15}
      nodesConnectable={false}
      onNodeClick={(_, n) => onSelect(n.data.raw)}
      onPaneClick={() => onSelect(null)}
      proOptions={{ hideAttribution: true }}
      aria-label="Evidence graph"
    >
      <Background gap={22} color="var(--line)" />
      <Controls showInteractive={false} />
      <MiniMap pannable zoomable nodeColor={(n) => ((n.data as NodeData).label ? LABEL_COLOR[(n.data as NodeData).label!] : "var(--muted-foreground)")} />
    </ReactFlow>
  );
}
