import type { Metadata } from "next";

import { AnalystWorkbench } from "@/features/analyst-workbench/analyst-workbench";

export const metadata: Metadata = {
  title: "CaseFile 工作台",
  description: "核对卷宗对象、时间线、证据关系、推理路径和候选工作稿。",
};

export default function WorkbenchRoute() {
  return <AnalystWorkbench />;
}
