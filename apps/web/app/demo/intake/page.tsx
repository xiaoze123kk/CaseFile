import type { Metadata } from "next";

import { IntakeCenterPrototype } from "@/features/intake-prototype/intake-center-prototype";

export const metadata: Metadata = {
  title: "建案中心原型 · CaseFile",
  description: "沿用创作模式数字档案纸语言的建案、简报审阅与候选稿交互原型。",
};

export default function DemoIntakePage() {
  return <IntakeCenterPrototype />;
}
