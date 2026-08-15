import type {
  CaseFileDocument,
  WorkbenchAuditEntryView,
  WorkbenchContextView,
  WorkbenchSourceView,
} from "@/lib/api-client";

import { buildObjectDetailModel } from "./workbench-object-detail-model";

export interface ContextSourceEvidence {
  id: string;
  kind: WorkbenchSourceView["source_kind"];
  kindLabel: string;
  excerpt: string;
  contentText: string;
  createdAt: string;
}

export interface ContextChangeEntry {
  id: string;
  occurredAt: string;
  actorLabel: string;
  actionLabel: string;
  detail: string;
}

export interface ContextInspectorModel {
  objectId: string | null;
  identity: {
    id: string;
    kindLabel: string;
    subtypeLabel: string;
    title: string;
    description: string;
    confirmationLabel: string;
    confidence: number | null;
    confidenceLabel: string;
    revision: number;
  } | null;
  sourceEvidence: ContextSourceEvidence[];
  recentChanges: ContextChangeEntry[];
  counts: {
    associations: number;
    sources: number;
    changes: number;
  };
}

export function sourceKindLabel(kind: WorkbenchSourceView["source_kind"]) {
  if (kind === "human_original") return "作者原稿";
  if (kind === "human_revision") return "作者修订";
  return "Agent 建议";
}

export function sourceExcerpt(content: string, maxLength = 140) {
  const normalized = content.replace(/\s+/gu, " ").trim();
  if (!normalized) return "来源正文待补充。";
  if (normalized.length <= maxLength) return normalized;
  return `${normalized.slice(0, maxLength)}…`;
}

export function auditActorLabel(actor: WorkbenchAuditEntryView["actor"]) {
  if (actor.kind === "user") return `你 · #${actor.user_id ?? "—"}`;
  return actor.ref ? `${actor.kind} · ${actor.ref}` : actor.kind;
}

export function auditActionLabel(action: string) {
  const labels: Record<string, string> = {
    add: "新增对象内容",
    remove: "移除对象内容",
    replace: "修改对象字段",
    agent_generate_from_brief: "Agent 从 Brief 生成工作稿",
    agent_adopt_brief_candidate: "采用 Draft 候选",
    agent_patch_apply: "应用 Agent 补丁",
    agent_patch_undo: "撤销 Agent 补丁",
  };
  return labels[action] ?? action;
}

export function auditChangeDetail(entry: WorkbenchAuditEntryView) {
  if (entry.source_table === "draft_operations") {
    const objectId = typeof entry.details.object_id === "string"
      ? entry.details.object_id
      : "Draft";
    const fieldPath = typeof entry.details.field_path === "string"
      ? entry.details.field_path || "/"
      : "/";
    return `${objectId} · ${fieldPath} · R${String(entry.details.base_revision)} → R${String(entry.details.result_revision)}`;
  }
  return `${entry.target_type} #${String(entry.target_id)}`;
}

function mapSourceEvidence(source: WorkbenchSourceView): ContextSourceEvidence {
  return {
    id: `source-${source.source_record_id}`,
    kind: source.source_kind,
    kindLabel: sourceKindLabel(source.source_kind),
    excerpt: sourceExcerpt(source.content_text),
    contentText: source.content_text,
    createdAt: source.created_at,
  };
}

function mapChangeEntry(entry: WorkbenchAuditEntryView): ContextChangeEntry {
  return {
    id: entry.entry_id,
    occurredAt: entry.occurred_at,
    actorLabel: auditActorLabel(entry.actor),
    actionLabel: auditActionLabel(entry.action),
    detail: auditChangeDetail(entry),
  };
}

export function buildContextInspectorModel(
  document: CaseFileDocument,
  objectId: string | null,
  context: WorkbenchContextView | null,
): ContextInspectorModel {
  const detail = buildObjectDetailModel(document, objectId);
  const sources = context?.sources ?? [];
  const changes = context?.audit_entries ?? [];
  return {
    objectId,
    identity: detail
      ? {
          id: detail.id,
          kindLabel: detail.kindLabel,
          subtypeLabel: detail.subtypeLabel,
          title: detail.title,
          description: detail.description,
          confirmationLabel: detail.confirmationLabel,
          confidence: detail.confidence,
          confidenceLabel: detail.confidenceLabel,
          revision: detail.revision,
        }
      : null,
    sourceEvidence: sources.map(mapSourceEvidence),
    recentChanges: changes.map(mapChangeEntry),
    counts: {
      associations: detail
        ? detail.sourceReferences.length +
          detail.references.length +
          detail.relationships.length
        : 0,
      sources: sources.length,
      changes: changes.length,
    },
  };
}
