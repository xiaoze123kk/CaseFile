import type {
  CaseFileDocument,
  WorkbenchContextView,
  WorkbenchSourceView,
} from "@/lib/api-client";

import {
  findWorkbenchDetailObject,
  type DetailCollection,
} from "./workbench-object-detail-model";

/**
 * Deterministic, local-first provenance presenter.
 *
 * Phase 3 maps the existing CaseFile provenance facts into author language:
 *   CaseFile field ──(declared)──▶ source_fragment ──▶ source_record ──▶ text span
 *   source_record ──(parent/task)──▶ derivation chain
 *
 * Fragment ids stay opaque until a backend stores the exact mapping, so a
 * field is only cited when its normalized value appears verbatim inside a
 * source record. Declared object-level fragments are preserved separately and
 * never guessed into a specific record.
 */

export interface ContextProvenanceSpan {
  start: number;
  end: number;
  paragraphNo: number;
  before: string;
  match: string;
  after: string;
}

export interface ContextSourceMatch {
  sourceRecordId: number;
  sourceLabel: string;
  kindLabel: string;
  span: ContextProvenanceSpan;
}

export interface ContextFieldCitation {
  fieldLabel: string;
  fieldValue: string;
  matches: ContextSourceMatch[];
}

export interface ContextSourceDerivation {
  source: WorkbenchSourceView;
  label: string;
  ordinal: string;
  kindLabel: string;
  originNote: string;
  parentRecordId: number | null;
  childRecordIds: number[];
}

export interface ContextFragmentRef {
  fragmentId: string;
  paths: string[];
}

export interface ContextProvenanceModel {
  citations: ContextFieldCitation[];
  derivations: ContextSourceDerivation[];
  fragments: ContextFragmentRef[];
  totals: {
    citedFields: number;
    citations: number;
    fragments: number;
  };
}

const CHINESE_ORDINALS = [
  "①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩",
  "⑪", "⑫", "⑬", "⑭", "⑮", "⑯", "⑰", "⑱", "⑲", "⑳",
];

const EXCERPT_WINDOW = 30;
const MIN_MATCH_LENGTH = 4;

export function sourceKindLabel(kind: WorkbenchSourceView["source_kind"]) {
  if (kind === "human_original") return "作者原稿";
  if (kind === "human_revision") return "作者修订";
  return "Agent 建议";
}

export function buildContextProvenanceModel(
  document: CaseFileDocument,
  objectId: string | null,
  context: WorkbenchContextView | null,
): ContextProvenanceModel {
  const sources = context?.sources ?? [];
  const derivations = buildSourceDerivations(sources);
  const selected = findWorkbenchDetailObject(document, objectId);
  if (!selected) {
    return {
      citations: [],
      derivations,
      fragments: [],
      totals: { citedFields: 0, citations: 0, fragments: 0 },
    };
  }

  const candidates = provenanceCandidates(
    selected.collection,
    selected.object as Record<string, unknown>,
  );
  const citations = candidates.flatMap((candidate) => {
    const matches = derivations.flatMap((derivation) => {
      const span = findExactSpan(derivation.source.content_text, candidate.value);
      if (!span) return [];
      return [{
        sourceRecordId: derivation.source.source_record_id,
        sourceLabel: derivation.label,
        kindLabel: derivation.kindLabel,
        span,
      }];
    });
    return matches.length
      ? [{ fieldLabel: candidate.label, fieldValue: candidate.value, matches }]
      : [];
  });

  const fragmentIds = objectReferenceIds(
    (selected.object as Record<string, unknown>).source_refs,
    "source_fragment",
  );
  const contractRefs = context?.contract_source_refs ?? [];
  const fragments = fragmentIds.map((fragmentId) => ({
    fragmentId,
    paths: contractRefs.find((reference) =>
      reference.source_fragment_id === fragmentId,
    )?.paths ?? [],
  }));

  return {
    citations,
    derivations,
    fragments,
    totals: {
      citedFields: citations.length,
      citations: citations.reduce((sum, citation) => sum + citation.matches.length, 0),
      fragments: fragments.length,
    },
  };
}

export function buildSourceDerivations(
  sources: WorkbenchSourceView[],
): ContextSourceDerivation[] {
  const byId = new Map(sources.map((source) => [source.source_record_id, source]));
  const byCreated = (left: WorkbenchSourceView, right: WorkbenchSourceView) =>
    left.created_at.localeCompare(right.created_at);
  const roots = sources
    .filter((source) => {
      const parent = source.parent_source_record_id;
      return parent === null || parent === undefined || !byId.has(parent);
    })
    .sort(byCreated);
  const seen = new Set<number>();
  const result: ContextSourceDerivation[] = [];

  const assignChain = (root: WorkbenchSourceView) => {
    const queue: WorkbenchSourceView[] = [root];
    let ordinal = 0;
    while (queue.length) {
      const source = queue.shift();
      if (!source || seen.has(source.source_record_id)) continue;
      seen.add(source.source_record_id);
      ordinal += 1;
      result.push({
        source,
        label: derivationLabel(source, ordinal),
        ordinal: CHINESE_ORDINALS[ordinal - 1] ?? `#${ordinal}`,
        kindLabel: sourceKindLabel(source.source_kind),
        originNote: derivationNote(source, byId, result),
        parentRecordId: source.parent_source_record_id,
        childRecordIds: sources
          .filter((candidate) => candidate.parent_source_record_id === source.source_record_id)
          .map((candidate) => candidate.source_record_id),
      });
      queue.push(...sources
        .filter((candidate) => candidate.parent_source_record_id === source.source_record_id)
        .sort(byCreated));
    }
  };

  for (const root of roots) assignChain(root);
  for (const source of [...sources].sort(byCreated)) {
    if (!seen.has(source.source_record_id)) assignChain(source);
  }
  return result;
}

function derivationLabel(source: WorkbenchSourceView, ordinal: number): string {
  const mark = CHINESE_ORDINALS[ordinal - 1] ?? `#${ordinal}`;
  if (source.source_kind === "human_original") return `你的原稿 ${mark}`;
  if (source.source_kind === "human_revision") return `你的修订 ${mark}`;
  return `Agent 建议 ${mark}`;
}

function derivationNote(
  source: WorkbenchSourceView,
  byId: Map<number, WorkbenchSourceView>,
  derivations: ContextSourceDerivation[],
): string {
  const parentId = source.parent_source_record_id;
  const parent = parentId === null || parentId === undefined
    ? null
    : byId.get(parentId) ?? null;
  const parentLabel = parent
    ? derivations.find((item) => item.source.source_record_id === parent.source_record_id)?.label
    : null;
  const taskNote = source.generated_by_task_run_id
    ? ` · Agent 任务 #${source.generated_by_task_run_id}`
    : "";
  if (parent && parentLabel) return `承接 ${parentLabel}${taskNote}`;
  if (source.source_kind === "human_original") {
    return source.created_by_user_id ? `作者提交${taskNote}` : "作者最初提交";
  }
  if (source.source_kind === "human_revision") {
    return source.created_by_user_id ? `作者修订${taskNote}` : "作者确认后的修订";
  }
  return taskNote ? `Agent 生成${taskNote}` : "Agent 生成";
}

function provenanceCandidates(
  collection: DetailCollection,
  object: Record<string, unknown>,
): Array<{ label: string; value: string }> {
  const candidates: Array<{ label: string; value: string }> = [];
  const push = (label: string, value: unknown) => {
    const text = typeof value === "string" ? value.trim() : "";
    if (text.length < MIN_MATCH_LENGTH) return;
    if (candidates.some((candidate) =>
      candidate.label === label && candidate.value === text,
    )) return;
    candidates.push({ label, value: text });
  };

  push("标题", object.title);
  push("说明", object.description);
  if (collection === "resolution_specs") {
    push("待解问题", object.reasoning_question);
    const conclusion = asRecord(object.conclusion);
    push("结论摘要", conclusion?.summary);
    push("裁决依据", conclusion?.rationale);
  } else if (collection === "entities") {
    push("名称", object.name);
  } else if (collection === "information_units") {
    push("正文", object.content);
  } else if (collection === "events") {
    // Event identity already covers 标题/说明.
  } else if (collection === "locations") {
    push("名称", object.name);
  } else if (collection === "hypotheses") {
    push("命题", object.proposition);
  }
  return candidates;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function objectReferenceIds(value: unknown, objectType: string): string[] {
  if (!Array.isArray(value)) return [];
  const result: string[] = [];
  for (const item of value) {
    const record = asRecord(item);
    if (!record) continue;
    if (record.object_type !== objectType || typeof record.object_id !== "string") continue;
    if (!result.includes(record.object_id)) result.push(record.object_id);
  }
  return result;
}

function normalizeText(value: string) {
  return value.replace(/\s+/gu, " ").trim();
}

function paragraphNumber(contentText: string, needle: string) {
  const paragraphs = contentText.split(/\n+/u);
  const normalized = normalizeText(needle);
  for (let index = 0; index < paragraphs.length; index += 1) {
    if (normalizeText(paragraphs[index]).includes(normalized)) return index + 1;
  }
  return 1;
}

export function findExactSpan(
  contentText: string,
  fieldValue: string,
): ContextProvenanceSpan | null {
  const normalizedContent = normalizeText(contentText);
  const needle = normalizeText(fieldValue);
  if (!needle || needle.length < MIN_MATCH_LENGTH) return null;
  const start = normalizedContent.indexOf(needle);
  if (start < 0) return null;
  const end = start + needle.length;
  const beforeStart = Math.max(0, start - EXCERPT_WINDOW);
  const afterEnd = Math.min(normalizedContent.length, end + EXCERPT_WINDOW);
  return {
    start,
    end,
    paragraphNo: paragraphNumber(contentText, needle),
    before: `${beforeStart > 0 ? "…" : ""}${normalizedContent.slice(beforeStart, start)}`,
    match: normalizedContent.slice(start, end),
    after: `${normalizedContent.slice(end, afterEnd)}${afterEnd < normalizedContent.length ? "…" : ""}`,
  };
}
