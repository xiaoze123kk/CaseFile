import type { Metadata } from "next";

import { RealWorkbench } from "@/features/workflow/real-workbench";

export const metadata: Metadata = {
  title: "CaseFile 工作台",
  description: "编辑卷宗事件、引用与角色可见范围。",
};

export default function WorkbenchRoute() {
  return <RealWorkbench />;
}
