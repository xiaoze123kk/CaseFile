import type { ReasoningOutcome } from "./analyst-fixture";

export const reasoningOutcomeLabels: Record<ReasoningOutcome, string> = {
  supported: "证据支持",
  contested: "解释竞争",
  eliminated: "已排除",
};

type ReasoningOperation =
  | "infer"
  | "compare"
  | "eliminate"
  | "combine"
  | "calculate"
  | "verify_rule";

export const reasoningOperationLabels: Record<ReasoningOperation, string> = {
  infer: "推断",
  compare: "比较",
  eliminate: "排除",
  combine: "合并",
  calculate: "计算",
  verify_rule: "验证规则",
};

export function reasoningOperationLabel(operation: string): string {
  return (
    reasoningOperationLabels[operation as ReasoningOperation] ?? "其他推理操作"
  );
}
