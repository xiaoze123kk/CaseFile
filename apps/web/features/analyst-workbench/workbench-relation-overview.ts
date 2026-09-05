import type { ContextRelation, ContextRelationModel } from "./workbench-relation-model";

export interface RelationOverviewItem {
  relation: ContextRelation;
  label: string;
  description: string;
  flow: {
    left: string;
    right: string;
    label: string;
    direction: "incoming" | "outgoing" | "mutual" | "neutral";
  };
}

function selectedEndpoint(relation: ContextRelation) {
  return relation.subject.id === relation.counterpart.id ? relation.object : relation.subject;
}

function priority(relation: ContextRelation): number {
  const type = selectedEndpoint(relation).objectType;
  const field = relation.fieldLabel;
  // These are editorial priorities, not claims about narrative significance.
  if (type === "event") {
    if (["原因事件", "结果事件"].includes(field)) return 0;
    if (field === "来源事件" || relation.group === "reasoning") return 1;
    if (field === "认知时点") return 5;
    if (["参与者", "观察者", "发生地点"].includes(field)) return 4;
    return 2;
  }
  if (type === "entity") {
    if (relation.fieldPath === null) return 0;
    if (["已知", "相信", "错误认知"].includes(field)) return 1;
    if (field === "认知时点") return 5;
    return relation.group === "events" ? 2 : 3;
  }
  if (type === "information_unit") {
    if (field === "来源事件") return 0;
    return relation.group === "reasoning" ? 1 : 3;
  }
  if (type === "location") {
    if (relation.counterpart.objectType === "event") return 0;
    return relation.group === "direct" ? 1 : 3;
  }
  if (type === "hypothesis" || type === "resolution_spec") {
    return relation.group === "reasoning" ? 0 : 3;
  }
  return relation.group === "direct" ? 0 : 1;
}

export function describeRelation(relation: ContextRelation): RelationOverviewItem {
  const selected = selectedEndpoint(relation);
  const outgoing = relation.subject.id === selected.id;
  const field = relation.fieldLabel;
  const current = ({
    event: "本事件", entity: "此对象", information_unit: "本条信息",
    location: "此地点", hypothesis: "本假设", resolution_spec: "本问题",
  } as Record<string, string>)[selected.objectType] ?? "此对象";
  const other = ({
    event: "该事件", entity: "对方", information_unit: "该信息",
    location: "该地点", hypothesis: "该假设", resolution_spec: "该问题",
    claim: "该论断", constraint: "该规则", structure_lock: "该结构约束",
    reasoning_path: "该路径",
  } as Record<string, string>)[relation.counterpart.objectType] ?? "对方";
  let label = field || "关联";
  let description = outgoing
    ? `${current}${relation.verb}${other}`
    : `${other}${relation.verb}${current}`;

  if (relation.fieldPath === null) {
    label = "对象关系";
    description = relation.arrow === "⇄"
      ? `双方关系 · ${relation.verb}`
      : relation.arrow === "—"
        ? `关系 · ${relation.verb}`
        : outgoing ? `指向对方 · ${relation.verb}` : `来自对方 · ${relation.verb}`;
  } else if (["原因事件", "结果事件"].includes(field)) {
    label = outgoing ? "后果" : "前因";
    description = outgoing ? "由本事件引发" : "引发本事件";
  } else if (field === "认知时点") {
    label = selected.objectType === "event" ? "认知记录" : "认知时点";
    description = selected.objectType === "event"
      ? "截至本事件的认知状态"
      : "截至该事件的认知状态";
  } else if (field === "来源事件") {
    label = selected.objectType === "event" ? "相关线索" : "来源事件";
    description = selected.objectType === "event" ? "信息来源于本事件" : "本条信息来源于该事件";
  } else if (field === "发生地点") {
    label = selected.objectType === "location" ? "发生的事件" : "发生地点";
    description = selected.objectType === "location" ? "发生在此地点" : "本事件发生于此";
  } else if (field === "约束范围" && relation.counterpart.objectType === "constraint") {
    label = "创作约束";
    description = `适用于${current}`;
  }
  return {
    relation, label, description,
    flow: {
      left: selected.label,
      right: relation.counterpart.label,
      label: field === "认知时点" ? "认知时点" : relation.verb,
      direction: field === "认知时点" || relation.arrow === "—"
        ? "neutral"
        : relation.arrow === "⇄" ? "mutual" : outgoing ? "outgoing" : "incoming",
    },
  };
}

/** Keeps every semantic relation; the first four favor distinct counterparts. */
export function buildRelationOverview(model: ContextRelationModel) {
  const ordered = model.groups.flatMap((group) => group.relations)
    .map((relation, index) => ({ relation, index }))
    .sort((a, b) => priority(a.relation) - priority(b.relation) || a.index - b.index)
    .map(({ relation }) => describeRelation(relation));
  const seen = new Set<string>();
  const preview = ordered.filter(({ relation }) => {
    if (seen.has(relation.counterpart.id) || seen.size >= 4) return false;
    seen.add(relation.counterpart.id);
    return true;
  });
  const previewIds = new Set(preview.map(({ relation }) => relation.id));
  return {
    preview,
    remaining: ordered.filter(({ relation }) => !previewIds.has(relation.id)),
    total: ordered.length,
  };
}
