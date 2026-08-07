import type { ReasoningOutcome } from "./analyst-fixture";

export const reasoningOutcomeLabels: Record<ReasoningOutcome, string> = {
  supported: "证据支持",
  contested: "解释竞争",
  eliminated: "已排除",
};
