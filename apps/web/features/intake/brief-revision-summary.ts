import { conclusionModes, resolutionModes, type IntakeBrief } from "./intake-model";

type BriefRevisionChangeKey =
  | "concept"
  | "reasoningGoal"
  | "conclusionMode"
  | "resolutionMode"
  | "authorAnswer"
  | "sellingPoints"
  | "outline"
  | "scopeEstimate"
  | "riskNotes"
  | "constraints";

export interface BriefRevisionChange {
  key: BriefRevisionChangeKey;
  label: string;
  before: string;
  after: string;
}

function compactRevisionText(value: string) {
  const compact = value.trim().replace(/\s+/gu, " ");
  if (!compact) return "未填写";
  return compact.length > 58 ? compact.slice(0, 58) + "…" : compact;
}

function briefListSummary(value: string, unit: string) {
  const items = value
    .split(/\r?\n/u)
    .map((item) => item.trim())
    .filter(Boolean);
  return items.length
    ? `${items.length} ${unit} · ${compactRevisionText(items[0] ?? "")}`
    : `0 ${unit}`;
}

function briefConstraintSummary(brief: IntakeBrief) {
  const items = brief.constraints.filter((constraint) =>
    constraint.statement.trim(),
  );
  if (!items.length) return "0 条";
  return `${items.length} 条 · ${items
    .slice(0, 2)
    .map((constraint) => constraint.label)
    .join("、")}`;
}

export function describeBriefRevision(
  before: IntakeBrief,
  after: IntakeBrief,
): BriefRevisionChange[] {
  const changes: BriefRevisionChange[] = [];
  const addTextChange = (
    key: Exclude<
      BriefRevisionChangeKey,
      "conclusionMode" | "resolutionMode" | "sellingPoints" | "outline" | "constraints"
    >,
    label: string,
  ) => {
    if (before[key].trim() === after[key].trim()) return;
    changes.push({
      key,
      label,
      before: compactRevisionText(before[key]),
      after: compactRevisionText(after[key]),
    });
  };

  addTextChange("concept", "一句话概念");
  addTextChange("reasoningGoal", "推理目标");

  if (before.conclusionMode !== after.conclusionMode) {
    changes.push({
      key: "conclusionMode",
      label: "结论模式",
      before:
        conclusionModes.find((mode) => mode.value === before.conclusionMode)
          ?.label ?? before.conclusionMode,
      after:
        conclusionModes.find((mode) => mode.value === after.conclusionMode)
          ?.label ?? after.conclusionMode,
    });
  }
  if (before.resolutionMode !== after.resolutionMode) {
    changes.push({
      key: "resolutionMode",
      label: "结论处理方式",
      before:
        resolutionModes.find((mode) => mode.value === before.resolutionMode)
          ?.label ?? before.resolutionMode,
      after:
        resolutionModes.find((mode) => mode.value === after.resolutionMode)
          ?.label ?? after.resolutionMode,
    });
  }

  addTextChange("authorAnswer", "作者答案");

  if (before.sellingPoints.trim() !== after.sellingPoints.trim()) {
    changes.push({
      key: "sellingPoints",
      label: "核心卖点",
      before: briefListSummary(before.sellingPoints, "项"),
      after: briefListSummary(after.sellingPoints, "项"),
    });
  }
  if (before.outline.trim() !== after.outline.trim()) {
    changes.push({
      key: "outline",
      label: "内容骨架",
      before: briefListSummary(before.outline, "阶段"),
      after: briefListSummary(after.outline, "阶段"),
    });
  }

  addTextChange("scopeEstimate", "预计规模");
  addTextChange("riskNotes", "风险提示");

  const constraintSnapshot = (brief: IntakeBrief) =>
    JSON.stringify(
      brief.constraints.map(({ key, statement, strength }) => ({
        key,
        statement: statement.trim(),
        strength,
      })),
    );
  if (constraintSnapshot(before) !== constraintSnapshot(after)) {
    changes.push({
      key: "constraints",
      label: "创作约束",
      before: briefConstraintSummary(before),
      after: briefConstraintSummary(after),
    });
  }
  return changes;
}
