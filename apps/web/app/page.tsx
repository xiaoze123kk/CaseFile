import type { Metadata } from "next";

import { IntakeCenter } from "@/features/intake/intake-center";

export const metadata: Metadata = {
  title: "建案中心",
  description: "从最初想法、关键追问和创作简报生成可审阅的 CaseFile 工作稿。",
};

export default function HomePage() {
  return <IntakeCenter />;
}
