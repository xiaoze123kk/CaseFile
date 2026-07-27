import type { Metadata } from "next";

import { ReasoningLab } from "@/features/reasoning/reasoning-lab";

export const metadata: Metadata = {
  title: "推理实验室 · CaseFile",
  description: "从当前 CaseFile Draft 生成、审阅和编辑可追溯的推理路径。",
};

export default function ReasoningRoute() {
  return <ReasoningLab />;
}
