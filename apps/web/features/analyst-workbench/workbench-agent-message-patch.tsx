import type { ComponentProps, Dispatch, SetStateAction } from "react";
import type { PublicAgentMessage } from "@casefile/contracts";
import { AgentPatchReview, initialPatchReview, type PatchReviewState, type WorkbenchAgentInspector } from "./workbench-agent-inspector";

/** Both conversation and detail hosts bind to the live controller's one review state. */
export function AgentMessagePatch({ message, inspector, scope, reviews, onReviewsChange, conversation, onDetails, onAdjust }: {
  message: PublicAgentMessage;
  inspector: Omit<ComponentProps<typeof WorkbenchAgentInspector>, "onFocusPatch">;
  scope: string;
  reviews: Record<string, PatchReviewState>;
  onReviewsChange: Dispatch<SetStateAction<Record<string, PatchReviewState>>>;
  conversation?: boolean;
  onDetails?: () => void;
  onAdjust?: () => void;
}) {
  const patch = message.patch;
  if (!patch) return null;
  const key = `${scope}:${patch.patch_id}:${patch.status}`;
  return <AgentPatchReview patchSet={patch} busy={inspector.busyPatchSetId !== null} requireApplyConfirmation
    conversation={conversation} onDetails={onDetails} onAdjust={onAdjust}
    reviewState={reviews[key] ?? initialPatchReview(patch)}
    onReviewStateChange={(update) => onReviewsChange((previous) => {
      const current = previous[key] ?? initialPatchReview(patch);
      return { ...previous, [key]: typeof update === "function" ? update(current) : update };
    })}
    onApply={(ids, confirmation) => inspector.onApply(patch, ids, confirmation)}
    onSimulate={inspector.onSimulate ? (ids, warnings, note) => inspector.onSimulate!(patch, ids, warnings, note) : undefined}
    onUndo={() => inspector.onUndo(patch)} onRedo={inspector.onRedo ? () => inspector.onRedo!(patch) : undefined}
    onRetry={() => inspector.onRetry(message)} onLocateObject={inspector.onLocateObject}
  />;
}
