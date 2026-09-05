import type { Metadata } from "next";

import { VisualIntakeDemo } from "@/features/intake/visual-intake-demo";

export const metadata: Metadata = {
  title: "CaseFile 建案视觉实验",
  description: "以活的卷宗脊柱验证建案入口、追问确认与 Brief 版本修订体验。",
};

export default function VisualIntakePage() {
  return <VisualIntakeDemo />;
}
