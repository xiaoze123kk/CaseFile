import type { Metadata } from "next";

import { AnalystWorkbench } from "@/features/analyst-workbench/analyst-workbench";

export const metadata: Metadata = {
  title: "CaseFile 工作台",
  description: "核对卷宗对象、时间线、证据关系、推理路径和候选工作稿。",
};

export default async function WorkbenchRoute({
  searchParams,
}: {
  searchParams: Promise<{ project?: string | string[] }>;
}) {
  const value = (await searchParams).project;
  const rawProjectId = Array.isArray(value) ? value[0] : value;
  const parsedProjectId = rawProjectId ? Number(rawProjectId) : null;
  const projectId =
    parsedProjectId !== null &&
    Number.isSafeInteger(parsedProjectId) &&
    parsedProjectId > 0
      ? parsedProjectId
      : null;

  return (
    <AnalystWorkbench
      invalidProjectId={rawProjectId !== undefined && projectId === null}
      requestedProjectId={projectId}
    />
  );
}
