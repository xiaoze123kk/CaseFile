import type { Metadata } from "next";

import { AnalystWorkbench } from "@/features/analyst-workbench/analyst-workbench";

export const metadata: Metadata = {
  title: "CaseFile 工作台",
  description: "核对卷宗对象、时间线、证据关系、推理路径和候选工作稿。",
};

function positiveInteger(value: string | string[] | undefined) {
  const raw = Array.isArray(value) ? value[0] : value;
  if (raw === undefined || !/^\d+$/u.test(raw)) return null;
  const parsed = Number(raw);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
}

export default async function WorkbenchRoute({
  searchParams,
}: {
  searchParams: Promise<{
    project?: string | string[];
    preview?: string | string[];
  }>;
}) {
  const params = await searchParams;
  const rawProjectId = Array.isArray(params.project)
    ? params.project[0]
    : params.project;
  const rawPreviewTaskRunId = Array.isArray(params.preview)
    ? params.preview[0]
    : params.preview;
  const projectId = positiveInteger(params.project);
  const previewTaskRunId = positiveInteger(params.preview);

  return (
    <AnalystWorkbench
      invalidPreviewTaskRunId={
        rawPreviewTaskRunId !== undefined && previewTaskRunId === null
      }
      invalidProjectId={rawProjectId !== undefined && projectId === null}
      requestedPreviewTaskRunId={previewTaskRunId}
      requestedProjectId={projectId}
    />
  );
}
