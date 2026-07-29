"use client";

import { useEffect, useMemo, useState } from "react";

import type { CaseFileDocument } from "@/lib/api-client";
import { errorMessage } from "@/lib/api-client";

import styles from "./real-workbench.module.css";
import {
  allWorkbenchObjects,
  collectionLabel,
  dateTimeLocalValue,
  dateTimeRequestValue,
  listFieldValue,
  objectDescription,
  objectHeadline,
  objectTypeForCollection,
  parseListField,
  resolveObjectRef,
  timePrecisionOptions,
  truthStatusOptions,
  type WorkbenchCollectionKey,
  type WorkbenchObject,
  type WorkbenchObjectRef,
} from "./workbench-model";

type FieldOption = readonly [string, string];
type StructuredFieldKind =
  | "required_slots"
  | "accepted_answers"
  | "travel_times"
  | "reasoning_steps";

type FieldDefinition =
  | {
      key: string;
      label: string;
      kind: "text" | "textarea" | "list" | "number" | "datetime";
      hint?: string;
      placeholder?: string;
    }
  | {
      key: string;
      label: string;
      kind: "select";
      options: readonly FieldOption[];
      hint?: string;
    }
  | {
      key: string;
      label: string;
      kind: "checkbox";
      hint?: string;
    }
  | {
      key: string;
      label: string;
      kind: "ref" | "refs";
      collections?: WorkbenchCollectionKey[];
      hint?: string;
    }
  | {
      key: string;
      label: string;
      kind: StructuredFieldKind | "protected_fields";
      hint?: string;
    };

interface FieldSection {
  title: string;
  description?: string;
  fields: FieldDefinition[];
}

const entityTypeOptions = [
  ["person", "人物"],
  ["organization", "组织"],
  ["object", "物件"],
  ["system", "系统"],
  ["faction", "阵营"],
  ["rule_actor", "规则角色"],
  ["other", "其他"],
] as const;

const relationshipDirectionOptions = [
  ["directed", "单向"],
  ["undirected", "无方向"],
  ["bidirectional", "双向"],
] as const;

const visibilityOptions = [
  ["public", "公开"],
  ["private", "私密"],
  ["restricted", "有限可见"],
  ["hidden", "隐藏"],
] as const;

const fieldSections: Record<WorkbenchCollectionKey, FieldSection[]> = {
  resolution_specs: [
    {
      title: "结论命题",
      fields: [
        { key: "title", label: "名称", kind: "text" },
        { key: "description", label: "说明", kind: "textarea" },
        {
          key: "question_type",
          label: "问题类型",
          kind: "select",
          options: [
            ["fact_reconstruction", "事实还原"],
            ["identity_inference", "身份推断"],
            ["causal_explanation", "因果解释"],
            ["path_discovery", "路径发现"],
            ["rule_discovery", "规则发现"],
            ["relationship_inference", "关系推断"],
            ["decision_reasoning", "决策推理"],
          ],
        },
        {
          key: "reasoning_question",
          label: "需要回答的问题",
          kind: "textarea",
        },
        {
          key: "conclusion_mode",
          label: "结论形式",
          kind: "select",
          options: [
            ["unique", "唯一答案"],
            ["finite_multiple", "有限多个答案"],
            ["optimal", "最优答案"],
            ["probabilistic", "概率判断"],
            ["open_interpretation", "开放解释"],
            ["multiple_endings", "多结局"],
            ["undetermined", "尚未确定"],
          ],
        },
        {
          key: "required_claim_refs",
          label: "必须成立的主张",
          kind: "refs",
          collections: ["claims"],
        },
        {
          key: "required_slots",
          label: "答案需要包含",
          kind: "required_slots",
          hint: "每行填写：要素名称｜内容类型｜必填或选填",
        },
        {
          key: "accepted_answers",
          label: "可接受答案",
          kind: "accepted_answers",
          hint: "每行填写一个文本答案，或使用“引用：对象名称”",
        },
      ],
    },
  ],
  entities: [
    {
      title: "身份与特征",
      fields: [
        { key: "name", label: "名称", kind: "text" },
        { key: "description", label: "对象说明", kind: "textarea" },
        {
          key: "entity_type",
          label: "对象类型",
          kind: "select",
          options: entityTypeOptions,
        },
        {
          key: "aliases",
          label: "别名",
          kind: "list",
          hint: "每行一个别名",
        },
        {
          key: "traits",
          label: "特征",
          kind: "list",
          hint: "每行一个特征",
        },
      ],
    },
    {
      title: "动机与能力",
      fields: [
        { key: "goals", label: "目标", kind: "list" },
        { key: "secrets", label: "秘密", kind: "list" },
        { key: "capabilities", label: "能力", kind: "list" },
      ],
    },
  ],
  relationships: [
    {
      title: "关系两端",
      fields: [
        { key: "title", label: "关系名称", kind: "text" },
        { key: "description", label: "关系说明", kind: "textarea" },
        { key: "from_ref", label: "起点对象", kind: "ref" },
        { key: "to_ref", label: "终点对象", kind: "ref" },
        { key: "relationship_type", label: "关系类型", kind: "text" },
        {
          key: "direction",
          label: "方向",
          kind: "select",
          options: relationshipDirectionOptions,
        },
        {
          key: "truth_status",
          label: "事实状态",
          kind: "select",
          options: truthStatusOptions,
        },
        {
          key: "visibility",
          label: "可见范围",
          kind: "select",
          options: visibilityOptions,
        },
      ],
    },
  ],
  locations: [
    {
      title: "地点档案",
      fields: [
        { key: "name", label: "地点名称", kind: "text" },
        { key: "description", label: "地点说明", kind: "textarea" },
        {
          key: "parent_ref",
          label: "所属地点",
          kind: "ref",
          collections: ["locations"],
        },
        {
          key: "adjacency_refs",
          label: "相邻地点",
          kind: "refs",
          collections: ["locations"],
        },
        { key: "access_rules", label: "进入条件", kind: "list" },
        {
          key: "travel_times",
          label: "到相邻地点的用时",
          kind: "travel_times",
          hint: "每行填写：地点名称｜分钟数",
        },
        { key: "visibility_rules", label: "可见规则", kind: "list" },
      ],
    },
  ],
  events: [
    {
      title: "事件内容",
      fields: [
        { key: "title", label: "事件标题", kind: "text" },
        { key: "description", label: "事件说明", kind: "textarea" },
        {
          key: "truth_status",
          label: "事实状态",
          kind: "select",
          options: truthStatusOptions,
        },
        {
          key: "participant_refs",
          label: "参与对象",
          kind: "refs",
          collections: ["entities"],
        },
        {
          key: "location_ref",
          label: "发生地点",
          kind: "ref",
          collections: ["locations"],
        },
      ],
    },
    {
      title: "事实时间",
      description: "时间线只使用这里的事实时间，不推断叙事出场顺序。",
      fields: [
        { key: "time.start", label: "开始时间", kind: "datetime" },
        { key: "time.end", label: "结束时间", kind: "datetime" },
        {
          key: "time.precision",
          label: "时间精度",
          kind: "select",
          options: timePrecisionOptions,
        },
        {
          key: "cause_refs",
          label: "前置事件",
          kind: "refs",
          collections: ["events"],
        },
        {
          key: "effect_refs",
          label: "后续事件",
          kind: "refs",
          collections: ["events"],
        },
        {
          key: "observed_by_refs",
          label: "知情对象",
          kind: "refs",
          collections: ["entities"],
        },
      ],
    },
  ],
  information_units: [
    {
      title: "信息内容",
      fields: [
        { key: "title", label: "信息标题", kind: "text" },
        { key: "description", label: "补充说明", kind: "textarea" },
        {
          key: "information_type",
          label: "信息类型",
          kind: "select",
          options: [
            ["evidence", "证据"],
            ["observation", "观察"],
            ["dialogue", "对话"],
            ["document", "文档"],
            ["system_log", "系统记录"],
            ["rule", "规则"],
            ["environment", "环境信息"],
            ["feedback", "反馈"],
            ["other", "其他"],
          ],
        },
        { key: "content", label: "正文", kind: "textarea" },
        {
          key: "source_event_ref",
          label: "来源事件",
          kind: "ref",
          collections: ["events"],
        },
        {
          key: "reliability",
          label: "可靠程度",
          kind: "select",
          options: [
            ["high", "高"],
            ["medium", "中"],
            ["low", "低"],
            ["unknown", "未知"],
          ],
        },
        {
          key: "truth_status",
          label: "事实状态",
          kind: "select",
          options: truthStatusOptions,
        },
        {
          key: "classification",
          label: "信息作用",
          kind: "select",
          options: [
            ["key", "关键信息"],
            ["supporting", "辅助信息"],
            ["background", "背景信息"],
            ["distractor", "干扰信息"],
            ["misleading", "误导信息"],
            ["incomplete", "不完整信息"],
          ],
        },
        {
          key: "availability.perspective_refs",
          label: "可获知该信息的对象",
          kind: "refs",
          collections: ["entities"],
        },
        {
          key: "availability.acquisition_conditions",
          label: "获取条件",
          kind: "list",
        },
        {
          key: "availability.alternative_path_refs",
          label: "替代获取路径",
          kind: "refs",
          collections: ["reasoning_paths"],
        },
        {
          key: "supports_claim_refs",
          label: "支持的主张",
          kind: "refs",
          collections: ["claims"],
        },
        {
          key: "refutes_claim_refs",
          label: "反驳的主张",
          kind: "refs",
          collections: ["claims"],
        },
      ],
    },
  ],
  claims: [
    {
      title: "主张内容",
      fields: [
        { key: "title", label: "主张名称", kind: "text" },
        { key: "description", label: "补充说明", kind: "textarea" },
        { key: "statement", label: "主张陈述", kind: "textarea" },
        {
          key: "claim_type",
          label: "主张类型",
          kind: "select",
          options: [
            ["fact", "事实"],
            ["causal", "因果"],
            ["identity", "身份"],
            ["relationship", "关系"],
            ["temporal", "时间"],
            ["rule", "规则"],
            ["evaluative", "评价"],
            ["other", "其他"],
          ],
        },
        { key: "support_refs", label: "支持材料", kind: "refs" },
        { key: "refute_refs", label: "反驳材料", kind: "refs" },
        {
          key: "dependency_claim_refs",
          label: "依赖主张",
          kind: "refs",
          collections: ["claims"],
        },
        {
          key: "status",
          label: "当前结论",
          kind: "select",
          options: [
            ["unsupported", "尚无支持"],
            ["partially_supported", "部分支持"],
            ["supported", "已有支持"],
            ["refuted", "已被反驳"],
            ["disputed", "存在争议"],
            ["unresolved", "尚未解决"],
          ],
        },
        {
          key: "materiality",
          label: "重要程度",
          kind: "select",
          options: [
            ["critical", "关键"],
            ["major", "重要"],
            ["minor", "次要"],
            ["background", "背景"],
          ],
        },
      ],
    },
  ],
  hypotheses: [
    {
      title: "假设内容",
      fields: [
        { key: "title", label: "假设名称", kind: "text" },
        { key: "description", label: "补充说明", kind: "textarea" },
        { key: "proposition", label: "假设命题", kind: "textarea" },
        {
          key: "target_resolution_ref",
          label: "对应结论",
          kind: "ref",
          collections: ["resolution_specs"],
        },
        {
          key: "required_claim_refs",
          label: "必要主张",
          kind: "refs",
          collections: ["claims"],
        },
        { key: "falsifier_refs", label: "可证伪材料", kind: "refs" },
        {
          key: "competing_hypothesis_refs",
          label: "竞争假设",
          kind: "refs",
          collections: ["hypotheses"],
        },
        {
          key: "status",
          label: "当前状态",
          kind: "select",
          options: [
            ["active", "分析中"],
            ["supported", "已有支持"],
            ["eliminated", "已排除"],
            ["accepted", "已采纳"],
            ["rejected", "已拒绝"],
            ["undetermined", "尚未确定"],
          ],
        },
        {
          key: "score",
          label: "可信评分",
          kind: "number",
          hint: "填写 0 到 1 之间的数值",
        },
      ],
    },
  ],
  reasoning_paths: [
    {
      title: "推理路径",
      fields: [
        { key: "title", label: "路径名称", kind: "text" },
        { key: "description", label: "路径说明", kind: "textarea" },
        {
          key: "path_type",
          label: "推理方式",
          kind: "select",
          options: [
            ["exclusion", "排除"],
            ["causal", "因果"],
            ["proof", "证明"],
            ["combination", "组合"],
            ["relationship", "关系"],
            ["temporal", "时间"],
            ["decision", "决策"],
            ["rule_derivation", "规则推导"],
            ["counterfactual", "反事实"],
          ],
        },
        { key: "target_ref", label: "推理目标", kind: "ref" },
        {
          key: "steps",
          label: "推理步骤",
          kind: "reasoning_steps",
          hint: "每行填写：推理方式｜输入对象名称（顿号分隔）｜输出对象名称",
        },
        {
          key: "required_for_resolution",
          label: "解决问题时必须经过",
          kind: "checkbox",
        },
        {
          key: "alternative_path_refs",
          label: "替代路径",
          kind: "refs",
          collections: ["reasoning_paths"],
        },
      ],
    },
  ],
  constraints: [
    {
      title: "约束内容",
      fields: [
        { key: "title", label: "约束名称", kind: "text" },
        { key: "description", label: "补充说明", kind: "textarea" },
        {
          key: "level",
          label: "约束级别",
          kind: "select",
          options: [
            ["hard", "必须满足"],
            ["soft", "尽量满足"],
          ],
        },
        { key: "statement", label: "约束陈述", kind: "textarea" },
        { key: "scope_refs", label: "作用对象", kind: "refs" },
        {
          key: "conflict_refs",
          label: "冲突约束",
          kind: "refs",
          collections: ["constraints"],
        },
        {
          key: "rule_expression",
          label: "规则表达",
          kind: "textarea",
          hint: "仅在需要精确表达规则时填写",
        },
      ],
    },
  ],
  structure_locks: [
    {
      title: "结构保护",
      fields: [
        { key: "title", label: "保护项名称", kind: "text" },
        { key: "description", label: "补充说明", kind: "textarea" },
        {
          key: "lock_type",
          label: "保护强度",
          kind: "select",
          options: [
            ["hard", "禁止改动"],
            ["soft", "改动前提醒"],
            ["open", "开放调整"],
          ],
        },
        { key: "object_ref", label: "保护对象", kind: "ref" },
        {
          key: "field_paths",
          label: "保护内容",
          kind: "protected_fields",
          hint: "选择用户可理解的业务内容；后台路径由系统维护",
        },
        { key: "reason", label: "保护原因", kind: "textarea" },
      ],
    },
  ],
};

const commonMetadataSection: FieldSection = {
  title: "整理信息",
  description: "用于检索和归类，不影响对象本身的事实含义。",
  fields: [
    {
      key: "tags",
      label: "标签",
      kind: "list",
      hint: "每行一个标签",
    },
  ],
};

function editorSections(collection: WorkbenchCollectionKey) {
  return [...fieldSections[collection], commonMetadataSection];
}

function cloneValue<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function getNestedValue(source: WorkbenchObject, path: string): unknown {
  return path.split(".").reduce<unknown>((current, key) => {
    if (!current || typeof current !== "object") return undefined;
    return (current as Record<string, unknown>)[key];
  }, source);
}

function setNestedValue(
  source: WorkbenchObject,
  path: string,
  value: unknown,
) {
  const [head, ...tail] = path.split(".");
  if (!tail.length) return { ...source, [head]: value };
  const nested =
    source[head] && typeof source[head] === "object"
      ? (source[head] as Record<string, unknown>)
      : {};
  return {
    ...source,
    [head]: {
      ...nested,
      [tail.join(".")]: value,
    },
  };
}

function topLevelFieldKeys(collection: WorkbenchCollectionKey) {
  const systemManagedFields = new Set([
    "id",
    "revision",
    "knowledge_states",
  ]);
  return [
    ...new Set(
      editorSections(collection).flatMap((section) =>
        section.fields.map((field) => field.key.split(".")[0]),
      ),
    ),
  ].filter((field) => !systemManagedFields.has(field));
}

function changedFields(
  collection: WorkbenchCollectionKey,
  baseline: WorkbenchObject,
  draft: WorkbenchObject,
) {
  return Object.fromEntries(
    topLevelFieldKeys(collection)
      .filter(
        (key) =>
          JSON.stringify(baseline[key]) !== JSON.stringify(draft[key]),
      )
      .map((key) => [key, draft[key]]),
  );
}

function referenceKey(ref: WorkbenchObjectRef) {
  return `${ref.object_type ?? ""}:${ref.object_id ?? ""}`;
}

const slotValueTypes: readonly FieldOption[] = [
  ["entity_or_claim_ref", "人物或主张"],
  ["text_or_claim_ref", "文本或主张"],
  ["object_ref", "卷宗对象"],
  ["text", "文本"],
  ["number", "数字"],
  ["boolean", "是或否"],
];

const reasoningOperations: readonly FieldOption[] = [
  ["infer", "推断"],
  ["compare", "比较"],
  ["eliminate", "排除"],
  ["combine", "合并"],
  ["calculate", "计算"],
  ["verify_rule", "验证规则"],
];

const protectedFieldOptions: readonly FieldOption[] = [
  ["/title", "名称或标题"],
  ["/name", "对象名称"],
  ["/description", "对象说明"],
  ["/content", "正文内容"],
  ["/statement", "主张或约束陈述"],
  ["/proposition", "假设命题"],
  ["/time", "事实时间"],
  ["/truth_status", "事实状态"],
  ["/location_ref", "发生地点"],
  ["/participant_refs", "参与对象"],
  ["/required_claim_refs", "必要主张"],
  ["/accepted_answers", "可接受答案"],
  ["/steps", "推理步骤"],
];

function recordArray(value: unknown) {
  return Array.isArray(value)
    ? value.filter(
        (item): item is Record<string, unknown> =>
          Boolean(item) && typeof item === "object" && !Array.isArray(item),
      )
    : [];
}

function refLabel(document: CaseFileDocument, value: unknown) {
  return resolveObjectRef(document, value as WorkbenchObjectRef | null)?.label ?? "";
}

function refForLabel(
  document: CaseFileDocument,
  label: string,
  collections?: WorkbenchCollectionKey[],
): WorkbenchObjectRef | null {
  const normalized = label.trim().toLocaleLowerCase("zh-CN");
  const match = allWorkbenchObjects(document).find(
    ({ collection, object }) =>
      (!collections || collections.includes(collection)) &&
      objectHeadline(object).trim().toLocaleLowerCase("zh-CN") === normalized,
  );
  return match
    ? {
        object_type: objectTypeForCollection(match.collection),
        object_id: match.object.id,
      }
    : null;
}

function optionLabel(options: readonly FieldOption[], value: unknown) {
  return options.find(([key]) => key === value)?.[1] ?? options[0][1];
}

function optionValue(options: readonly FieldOption[], label: string) {
  return options.find(([, candidate]) => candidate === label.trim())?.[0] ?? options[0][0];
}

function structuredFieldText(
  kind: StructuredFieldKind,
  value: unknown,
  document: CaseFileDocument,
) {
  if (kind === "required_slots") {
    return recordArray(value)
      .map(
        (slot, index) =>
          `答案要素 ${index + 1}｜${optionLabel(slotValueTypes, slot.value_type)}｜${
            slot.required ? "必填" : "选填"
          }`,
      )
      .join("\n");
  }
  if (kind === "accepted_answers") {
    return (Array.isArray(value) ? value : [])
      .map((answer) =>
        typeof answer === "string"
          ? `文本：${answer}`
          : `引用：${refLabel(document, answer) || "未找到的对象"}`,
      )
      .join("\n");
  }
  if (kind === "travel_times") {
    return recordArray(value)
      .map(
        (travel) =>
          `${refLabel(document, travel.to_ref) || "未找到的地点"}｜${
            typeof travel.minutes === "number" ? travel.minutes : 0
          }`,
      )
      .join("\n");
  }
  return recordArray(value)
    .map((step) => {
      const inputs = (Array.isArray(step.input_refs) ? step.input_refs : [])
        .map((ref) => refLabel(document, ref))
        .filter(Boolean)
        .join("、");
      return `${optionLabel(reasoningOperations, step.operation)}｜${inputs}｜${
        refLabel(document, step.output_ref) || "请选择输出对象"
      }`;
    })
    .join("\n");
}

function parseStructuredField(
  kind: StructuredFieldKind,
  text: string,
  current: unknown,
  document: CaseFileDocument,
) {
  const lines = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const existing = recordArray(current);
  if (kind === "required_slots") {
    return lines.map((line, index) => {
      const [, typeLabel = "文本", requiredLabel = "必填"] = line.split("｜");
      return {
        slot_id:
          typeof existing[index]?.slot_id === "string"
            ? existing[index].slot_id
            : `slot_item_${String(index + 1).padStart(2, "0")}`,
        value_type: optionValue(slotValueTypes, typeLabel),
        required: requiredLabel.trim() !== "选填",
      };
    });
  }
  if (kind === "accepted_answers") {
    const answers: Array<string | WorkbenchObjectRef> = [];
    lines.forEach((line) => {
      if (!line.startsWith("引用：")) {
        answers.push(line.replace(/^文本：/, "").trim());
        return;
      }
      const ref = refForLabel(document, line.slice(3));
      if (ref) answers.push(ref);
    });
    return answers;
  }
  if (kind === "travel_times") {
    return lines.flatMap((line) => {
      const [label = "", minuteText = "0"] = line.split("｜");
      const toRef = refForLabel(document, label, ["locations"]);
      const minutes = Number(minuteText.replace(/[^\d.]/g, ""));
      return toRef && Number.isFinite(minutes)
        ? [{ to_ref: toRef, minutes: Math.max(0, minutes) }]
        : [];
    });
  }
  return lines.flatMap((line, index) => {
    const [operationLabel = "推断", inputText = "", outputText = ""] =
      line.split("｜");
    const outputRef = refForLabel(document, outputText);
    if (!outputRef) return [];
    return [
      {
        step_id:
          typeof existing[index]?.step_id === "string"
            ? existing[index].step_id
            : `step_item_${String(index + 1).padStart(2, "0")}`,
        input_refs: inputText
          .split(/[、,，]/)
          .map((label) => refForLabel(document, label))
          .filter((ref): ref is WorkbenchObjectRef => ref !== null),
        operation: optionValue(reasoningOperations, operationLabel),
        output_ref: outputRef,
      },
    ];
  });
}

function ObjectReferencePicker({
  document,
  value,
  multiple,
  collections,
  onChange,
}: {
  document: CaseFileDocument;
  value: WorkbenchObjectRef | WorkbenchObjectRef[] | null | undefined;
  multiple: boolean;
  collections?: WorkbenchCollectionKey[];
  onChange: (
    value: WorkbenchObjectRef | WorkbenchObjectRef[] | null,
  ) => void;
}) {
  const [query, setQuery] = useState("");
  const candidates = useMemo(
    () =>
      allWorkbenchObjects(document)
        .filter(({ collection }) => !collections || collections.includes(collection))
        .filter(({ object }) =>
          objectHeadline(object).toLocaleLowerCase("zh-CN").includes(
            query.trim().toLocaleLowerCase("zh-CN"),
          ),
        ),
    [collections, document, query],
  );
  const selected = Array.isArray(value)
    ? value
    : value
      ? [value]
      : [];
  const selectedKeys = new Set(selected.map(referenceKey));

  function choose(
    collection: WorkbenchCollectionKey,
    object: WorkbenchObject,
  ) {
    const ref = {
      object_type: objectTypeForCollection(collection),
      object_id: object.id,
    };
    if (!multiple) {
      onChange(ref);
      return;
    }
    const key = referenceKey(ref);
    onChange(
      selectedKeys.has(key)
        ? selected.filter((item) => referenceKey(item) !== key)
        : [...selected, ref],
    );
  }

  return (
    <div className={styles.referencePicker}>
      <div className={styles.referenceChips}>
        {selected.length ? (
          selected.map((ref) => {
            const resolved = resolveObjectRef(document, ref);
            return (
              <button
                aria-label={`移除${resolved?.label ?? "关联对象"}`}
                key={referenceKey(ref)}
                onClick={() =>
                  onChange(
                    multiple
                      ? selected.filter(
                          (item) => referenceKey(item) !== referenceKey(ref),
                        )
                      : null,
                  )
                }
                type="button"
              >
                {resolved?.label ?? "未找到的对象"}
                <span aria-hidden="true">×</span>
              </button>
            );
          })
        ) : (
          <small>尚未选择</small>
        )}
      </div>
      <details>
        <summary>{multiple ? "查找并添加对象" : "选择对象"}</summary>
        <div className={styles.referenceMenu}>
          <input
            aria-label="搜索可关联对象"
            onChange={(event) => setQuery(event.target.value)}
            placeholder="输入对象名称…"
            type="search"
            value={query}
          />
          <div>
            {candidates.map(({ collection, object }) => {
              const key = referenceKey({
                object_type: objectTypeForCollection(collection),
                object_id: object.id,
              });
              return (
                <button
                  aria-pressed={selectedKeys.has(key)}
                  key={key}
                  onClick={() => choose(collection, object)}
                  type="button"
                >
                  <span>{selectedKeys.has(key) ? "✓" : "+"}</span>
                  <strong>{objectHeadline(object)}</strong>
                  <small>{collectionLabel(collection)}</small>
                </button>
              );
            })}
            {!candidates.length ? <p>没有匹配的对象</p> : null}
          </div>
        </div>
      </details>
    </div>
  );
}

function EditorField({
  definition,
  document,
  draft,
  onChange,
}: {
  definition: FieldDefinition;
  document: CaseFileDocument;
  draft: WorkbenchObject;
  onChange: (path: string, value: unknown) => void;
}) {
  const value = getNestedValue(draft, definition.key);
  if (definition.kind === "ref" || definition.kind === "refs") {
    return (
      <label className={styles.editorWideField}>
        <span>{definition.label}</span>
        <ObjectReferencePicker
          collections={definition.collections}
          document={document}
          multiple={definition.kind === "refs"}
          onChange={(next) => onChange(definition.key, next)}
          value={
            definition.kind === "refs"
              ? (value as WorkbenchObjectRef[] | undefined) ?? []
              : (value as WorkbenchObjectRef | null | undefined)
          }
        />
        {definition.hint ? <small>{definition.hint}</small> : null}
      </label>
    );
  }
  if (definition.kind === "protected_fields") {
    const selected = Array.isArray(value)
      ? value.filter((item): item is string => typeof item === "string")
      : [];
    const knownPaths = new Set(protectedFieldOptions.map(([path]) => path));
    const unrecognized = selected.filter((path) => !knownPaths.has(path));
    return (
      <div className={styles.editorWideField}>
        <span>{definition.label}</span>
        {protectedFieldOptions.map(([path, label]) => (
          <label className={styles.editorCheckField} key={path}>
            <input
              checked={selected.includes(path)}
              onChange={(event) =>
                onChange(
                  definition.key,
                  event.target.checked
                    ? [...selected, path]
                    : selected.filter((item) => item !== path),
                )
              }
              type="checkbox"
            />
            <span>
              <strong>{label}</strong>
            </span>
          </label>
        ))}
        {unrecognized.length ? (
          <small>另有 {unrecognized.length} 项既有保护内容由系统继续保留。</small>
        ) : null}
        {definition.hint ? <small>{definition.hint}</small> : null}
      </div>
    );
  }
  if (
    definition.kind === "required_slots" ||
    definition.kind === "accepted_answers" ||
    definition.kind === "travel_times" ||
    definition.kind === "reasoning_steps"
  ) {
    return (
      <label className={styles.editorWideField}>
        <span>{definition.label}</span>
        <textarea
          onChange={(event) =>
            onChange(
              definition.key,
              parseStructuredField(
                definition.kind as StructuredFieldKind,
                event.target.value,
                value,
                document,
              ),
            )
          }
          rows={4}
          value={structuredFieldText(
            definition.kind as StructuredFieldKind,
            value,
            document,
          )}
        />
        {definition.hint ? <small>{definition.hint}</small> : null}
      </label>
    );
  }
  if (definition.kind === "checkbox") {
    return (
      <label className={styles.editorCheckField}>
        <input
          checked={Boolean(value)}
          onChange={(event) => onChange(definition.key, event.target.checked)}
          type="checkbox"
        />
        <span>
          <strong>{definition.label}</strong>
          {definition.hint ? <small>{definition.hint}</small> : null}
        </span>
      </label>
    );
  }
  if (definition.kind === "select") {
    return (
      <label>
        <span>{definition.label}</span>
        <select
          onChange={(event) => onChange(definition.key, event.target.value)}
          value={String(value ?? "")}
        >
          {definition.options.map(([optionValue, label]) => (
            <option key={optionValue} value={optionValue}>
              {label}
            </option>
          ))}
        </select>
        {definition.hint ? <small>{definition.hint}</small> : null}
      </label>
    );
  }
  if (definition.kind === "textarea" || definition.kind === "list") {
    return (
      <label className={styles.editorWideField}>
        <span>{definition.label}</span>
        <textarea
          onChange={(event) =>
            onChange(
              definition.key,
              definition.kind === "list"
                ? parseListField(event.target.value)
                : event.target.value,
            )
          }
          placeholder={definition.placeholder}
          rows={definition.kind === "list" ? 3 : 4}
          value={
            definition.kind === "list"
              ? listFieldValue(value)
              : String(value ?? "")
          }
        />
        {definition.hint ? <small>{definition.hint}</small> : null}
      </label>
    );
  }
  if (definition.kind === "datetime") {
    return (
      <label>
        <span>{definition.label}</span>
        <input
          onChange={(event) =>
            onChange(
              definition.key,
              dateTimeRequestValue(event.target.value),
            )
          }
          type="datetime-local"
          value={dateTimeLocalValue(
            typeof value === "string" ? value : null,
          )}
        />
        {definition.hint ? <small>{definition.hint}</small> : null}
      </label>
    );
  }
  return (
    <label>
      <span>{definition.label}</span>
      <input
        min={definition.kind === "number" ? "0" : undefined}
        max={definition.kind === "number" ? "1" : undefined}
        onChange={(event) =>
          onChange(
            definition.key,
            definition.kind === "number"
              ? event.target.value
                ? Number(event.target.value)
                : null
              : event.target.value,
          )
        }
        placeholder={
          "placeholder" in definition ? definition.placeholder : undefined
        }
        step={definition.kind === "number" ? "0.01" : undefined}
        type={definition.kind === "number" ? "number" : "text"}
        value={
          typeof value === "number" || typeof value === "string"
            ? value
            : ""
        }
      />
      {definition.hint ? <small>{definition.hint}</small> : null}
    </label>
  );
}

export function ObjectEditor({
  document,
  collection,
  object,
  draftRevision,
  onDirtyChange,
  onSave,
}: {
  document: CaseFileDocument;
  collection: WorkbenchCollectionKey;
  object: WorkbenchObject;
  draftRevision: number;
  onDirtyChange?: (dirty: boolean) => void;
  onSave: (
    changes: Record<string, unknown>,
    expectedRevision: number,
  ) => Promise<void>;
}) {
  const [baseline, setBaseline] = useState(() => cloneValue(object));
  const [draftState, setDraftState] = useState(() => cloneValue(object));
  const [baseRevision, setBaseRevision] = useState(draftRevision);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const changes = useMemo(
    () => changedFields(collection, baseline, draftState),
    [baseline, collection, draftState],
  );
  const dirty = Object.keys(changes).length > 0;
  const stale = dirty && baseRevision !== draftRevision;
  const draft = dirty ? draftState : object;

  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);

  function updateField(path: string, value: unknown) {
    if (!dirty) {
      setBaseline(cloneValue(object));
      setBaseRevision(draftRevision);
      setDraftState(setNestedValue(cloneValue(object), path, value));
    } else {
      setDraftState((current) => setNestedValue(current, path, value));
    }
    setSaveError(null);
  }

  function cancelChanges() {
    setBaseline(cloneValue(object));
    setDraftState(cloneValue(object));
    setBaseRevision(draftRevision);
    setSaveError(null);
  }

  async function saveChanges() {
    if (!dirty || stale) return;
    setSaving(true);
    setSaveError(null);
    try {
      await onSave(changes, baseRevision);
      setBaseline(cloneValue(draft));
      setDraftState(cloneValue(draft));
      setBaseRevision(draftRevision + 1);
    } catch (error) {
      setSaveError(errorMessage(error));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      className={styles.objectEditor}
      onSubmit={(event) => {
        event.preventDefault();
        void saveChanges();
      }}
    >
      <header className={styles.editorIdentity}>
        <span>{collectionLabel(collection)}</span>
        <h2>{objectHeadline(draft)}</h2>
        <p>{objectDescription(draft)}</p>
      </header>
      <div className={styles.editorScroll}>
        {editorSections(collection).map((section, index) => (
          <section className={styles.editorSection} key={section.title}>
            <header>
              <span>{String(index + 1).padStart(2, "0")}</span>
              <div>
                <strong>{section.title}</strong>
                {section.description ? <small>{section.description}</small> : null}
              </div>
            </header>
            <div className={styles.editorFieldGrid}>
              {section.fields.map((definition) => (
                <EditorField
                  definition={definition}
                  document={document}
                  draft={draft}
                  key={definition.key}
                  onChange={updateField}
                />
              ))}
            </div>
          </section>
        ))}
      </div>
      {stale ? (
        <div className={styles.editorStale} role="alert">
          <strong>卷宗内容已经更新</strong>
          <p>当前表单基于较早内容。取消本次修改后再重新编辑，避免覆盖新内容。</p>
        </div>
      ) : null}
      {saveError ? (
        <p className={styles.editorError} role="alert">
          {saveError}
        </p>
      ) : null}
      <footer className={styles.editorActions}>
        <span>{dirty ? "有尚未保存的修改" : "内容已同步"}</span>
        <button
          disabled={!dirty || saving}
          onClick={cancelChanges}
          type="button"
        >
          取消修改
        </button>
        <button
          className={styles.editorSave}
          disabled={!dirty || stale || saving}
          type="submit"
        >
          {saving ? "保存中…" : "保存对象"}
        </button>
      </footer>
    </form>
  );
}
