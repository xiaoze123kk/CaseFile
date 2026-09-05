import type { ComponentProps, Dispatch, SetStateAction } from "react";
import { createPortal } from "react-dom";
import { type PatchReviewState, type WorkbenchAgentInspector } from "./workbench-agent-inspector";
import { AgentMessagePatch } from "./workbench-agent-message-patch";
import { WorkbenchCollaborationDetail, type CollaborationDetailData } from "./workbench-collaboration-detail";
import type { CollaborationDetail, DetailHost } from "./workbench-collaboration-state";
import type { ContextItem } from "./workbench-agent-context";

export interface AgentDetailNavigation {
  center: CollaborationDetail | null;
  side: CollaborationDetail | null;
  centerHost: HTMLElement | null;
  sideHost: HTMLElement | null;
  data: CollaborationDetailData;
  onBack: (host: DetailHost) => void;
  onOpen: (detail: CollaborationDetail) => void;
}

export function WorkbenchAgentDetailPortals({ details, inspector, scope, loading, reviews, onReviewsChange, onAddContext }: {
  details: AgentDetailNavigation;
  inspector: Omit<ComponentProps<typeof WorkbenchAgentInspector>, "onFocusPatch">;
  scope: string;
  loading: boolean;
  reviews: Record<string, PatchReviewState>;
  onReviewsChange: Dispatch<SetStateAction<Record<string, PatchReviewState>>>;
  onAddContext: (items: ContextItem[]) => void;
}) {
  return (["center", "side"] as const).map((host) => {
    const detail = details[host];
    const element = host === "center" ? details.centerHost : details.sideHost;
    if (!detail || !element) return null;
    const entry = detail.kind === "patch" ? inspector.patches.find((item) => item.patchSet.patch_id === detail.patchId) : null;
    const patch = entry?.patchSet;
    return createPortal(<WorkbenchCollaborationDetail
      detail={detail} data={details.data} loading={loading}
      finding={detail.kind === "validation" ? inspector.findings.find((item) => item.finding.finding_id === detail.findingId)?.finding : undefined}
      onBack={() => details.onBack(host)} onLocate={inspector.onLocateObject ?? (() => {})}
      onOpenDetail={details.onOpen} onAddContext={onAddContext}
      patch={patch && entry ? <><AgentMessagePatch message={entry.message} inspector={inspector} scope={scope}
        reviews={reviews} onReviewsChange={onReviewsChange}
      />{inspector.patchError ? <p role="alert">{inspector.patchError}</p> : null}</> : undefined}
    />, element, host);
  });
}
