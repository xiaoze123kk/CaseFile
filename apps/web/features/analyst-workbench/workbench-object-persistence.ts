"use client";

import { useRef, useState } from "react";

import {
  fetchCaseDraft,
  patchCaseDraftObject,
} from "@/features/case-session/case-session-api";
import { ApiError, type DraftView } from "@/lib/api-client";

import type {
  ReloadedSpatialLocation,
  SpatialPositionPayload,
  SpatialPositionSaveResult,
} from "./workbench-real-data-types";

type ObjectSaveResult = "saved" | "conflict" | "error";

export function useWorkbenchObjectPersistence({
  draft,
  projectId,
  onDraftLoaded,
  onRefreshContext,
}: {
  draft: DraftView | null;
  projectId: number | null;
  onDraftLoaded: (draft: DraftView) => void;
  onRefreshContext: () => void;
}) {
  const [savingObject, setSavingObject] = useState(false);
  const saveInFlightRef = useRef(false);

  async function loadLatestDraft(): Promise<DraftView> {
    if (projectId === null) throw new Error("当前工作稿的项目标识缺失");
    const latest = await fetchCaseDraft(projectId);
    onDraftLoaded(latest);
    onRefreshContext();
    return latest;
  }

  async function saveObject(
    objectId: string,
    changes: Record<string, unknown>,
  ): Promise<ObjectSaveResult> {
    if (!draft || projectId === null || saveInFlightRef.current) return "error";
    saveInFlightRef.current = true;
    setSavingObject(true);
    try {
      await patchCaseDraftObject(
        projectId,
        objectId,
        draft.draft_id,
        draft.revision,
        changes,
      );
      await loadLatestDraft();
      return "saved";
    } catch (caught) {
      if (
        caught instanceof ApiError &&
        (caught.status === 409 || caught.body.code === "draft_revision_conflict")
      ) {
        try {
          await loadLatestDraft();
          return "conflict";
        } catch {
          return "error";
        }
      }
      return "error";
    } finally {
      saveInFlightRef.current = false;
      setSavingObject(false);
    }
  }

  async function saveSpatialPosition(
    objectId: string,
    position: SpatialPositionPayload,
  ): Promise<SpatialPositionSaveResult> {
    if (!draft || projectId === null || saveInFlightRef.current) return "error";
    saveInFlightRef.current = true;
    setSavingObject(true);
    try {
      await patchCaseDraftObject(
        projectId,
        objectId,
        draft.draft_id,
        draft.revision,
        { spatial_position: position },
      );
      await loadLatestDraft();
      return "saved";
    } catch (caught) {
      return caught instanceof ApiError &&
        (caught.status === 409 || caught.body.code === "draft_revision_conflict")
        ? "conflict"
        : "error";
    } finally {
      saveInFlightRef.current = false;
      setSavingObject(false);
    }
  }

  async function reloadSpatialLocation(
    objectId: string,
  ): Promise<ReloadedSpatialLocation> {
    const latest = await loadLatestDraft();
    const location = latest.content?.locations.find((item) => item.id === objectId);
    return {
      found: Boolean(location),
      position: location?.spatial_position ?? null,
      revision: latest.revision,
    };
  }

  return {
    reloadSpatialLocation,
    saveObject,
    saveSpatialPosition,
    savingObject,
  };
}
