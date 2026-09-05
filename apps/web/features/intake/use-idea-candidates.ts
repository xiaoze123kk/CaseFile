"use client";

import { useState, type Dispatch, type SetStateAction } from "react";

import {
  generateIdeas, fetchIdeas, selectIdea, bookmarkIdea, archiveIdea,
  regenerateIdea, createCaseProject, type IdeaGenerationPreferences,
} from "@/features/case-session/case-session-api";
import { useSessionUiOperation } from "@/features/case-session/use-session-ui-operation";
import type { IdeaCandidateView } from "./intake-model";

type RawIdeaRecord = Record<string, unknown>;

type Options = {
  activeProjectId: number | null;
  hydrating: boolean;
  loadProject: (projectId: number) => Promise<void>;
  setActivePath: Dispatch<SetStateAction<"A" | "B" | "C">>;
  setShowIdeaGeneration: Dispatch<SetStateAction<boolean>>;
  setShowReverseParse: Dispatch<SetStateAction<boolean>>;
  setError: Dispatch<SetStateAction<string | null>>;
};

export function useIdeaCandidates({
  activeProjectId, hydrating, loadProject, setActivePath, setShowIdeaGeneration, setShowReverseParse, setError,
}: Options) {
  const captureOperation = useSessionUiOperation();
  const [ideaProjectId, setIdeaProjectId] = useState<number | null>(null);
  const [ideaCandidates, setIdeaCandidates] = useState<IdeaCandidateView[]>([]);
  const [pastBatches, setPastBatches] = useState<Record<string, IdeaCandidateView[]>>({});
  const [ideaGenerating, setIdeaGenerating] = useState(false);
  const [regeneratingIds, setRegeneratingIds] = useState<number[]>([]);
  const [previousProjectId, setPreviousProjectId] = useState(activeProjectId);

  if (previousProjectId !== activeProjectId) {
    setPreviousProjectId(activeProjectId);
    if (ideaProjectId !== activeProjectId) resetIdeas();
    else clearIdeaPending();
  }

  const ideaFromRecord = (idea: RawIdeaRecord): IdeaCandidateView => ({
    id: idea.id as number,
    batch_id: idea.batch_id as string,
    ordinal: idea.ordinal as number,
    content: idea.content as IdeaCandidateView["content"],
    status: (idea.status ?? "active") as IdeaCandidateView["status"],
    bookmarked: (idea.bookmarked ?? false) as boolean,
    created_at: (idea.created_at ?? null) as string | null,
  });

  const pastBatchesFromRecord = (
    batches: Record<string, unknown>,
  ): Record<string, IdeaCandidateView[]> => {
    const pastMap: Record<string, IdeaCandidateView[]> = {};
    for (const [key, val] of Object.entries(batches)) {
      pastMap[key] = (val as RawIdeaRecord[]).map(ideaFromRecord);
    }
    return pastMap;
  };

  // 进入路径 B：只恢复已有创意，不自动重新生成；生成由用户显式触发。
  const enterPathB = async () => {
    if (hydrating) return;
    const isCurrent = captureOperation();
    if (ideaGenerating) return;
    setActivePath("B");
    setShowIdeaGeneration(true);
    setShowReverseParse(false);
    setError(null);
    try {
      // 优先复用已有 ideaProjectId，避免“切回 A 再切回 B”时误建新项目，
      // 导致界面上的候选归属到另一个项目，收藏/淘汰/重新生成全部失效。
      const project = ideaProjectId !== null
        ? { id: ideaProjectId }
        : activeProjectId
          ? { id: activeProjectId }
          : await createCaseProject("帮我想一个");
      if (!isCurrent()) return;
      setIdeaProjectId(project.id);

      // 恢复历史创意批次；已有创意时直接展示最近一批，不重新生成。
      try {
        const past = await fetchIdeas(project.id);
        if (!isCurrent()) return;
        const pastMap = pastBatchesFromRecord(past.batches ?? {});
        setPastBatches(pastMap);
        if (ideaCandidates.length === 0) {
          const batchIds = Object.keys(pastMap).sort();
          const latestBatchId = batchIds[batchIds.length - 1];
          if (latestBatchId) setIdeaCandidates(pastMap[latestBatchId] ?? []);
        }
      } catch { /* silently ignore */ }
    } catch (err) {
      if (!isCurrent()) return;
      setError(err instanceof Error ? err.message : "打开创意方向失败。");
    }
  };

  // 显式“生成创意候选”：每次点击都重新生成一批，并把偏好传给后端。
  const generateAll = async (preferences?: IdeaGenerationPreferences) => {
    if (hydrating) return;
    const isCurrent = captureOperation();
    if (ideaGenerating) return;
    setError(null);
    setIdeaGenerating(true);
    try {
      const project = ideaProjectId !== null
        ? { id: ideaProjectId }
        : activeProjectId
          ? { id: activeProjectId }
          : await createCaseProject("帮我想一个");
      if (!isCurrent()) return;
      setIdeaProjectId(project.id);
      const result = await generateIdeas(project.id, preferences);
      if (!isCurrent()) return;
      setIdeaCandidates((result.ideas ?? []).map(ideaFromRecord));
      await refetchIdeasForProject(project.id);
      if (!isCurrent()) return;
    } catch (err) {
      if (!isCurrent()) return;
      setError(err instanceof Error ? err.message : "生成创意失败。");
    } finally {
      if (isCurrent()) {
        setIdeaGenerating(false);
      }
    }
  };

  const handleSelectIdea = async (ideaId: number) => {
    if (hydrating) return;
    let isCurrent = captureOperation();
    if (!ideaProjectId) return;
    try {
      await selectIdea(ideaProjectId, ideaId);
      if (!isCurrent()) return;
      setIdeaCandidates((prev) =>
        prev.map((i) => (i.id === ideaId ? { ...i, status: "selected" as const } : i)),
      );
      const loading = loadProject(ideaProjectId);
      isCurrent = captureOperation();
      await loading;
      if (!isCurrent()) return;
      setShowIdeaGeneration(false);
    } catch (err) {
      if (!isCurrent()) return;
      setError(err instanceof Error ? err.message : "选择失败。");
    }
  };

  const refetchIdeasForProject = async (projectId: number) => {
    if (hydrating) return;
    const isCurrent = captureOperation();
    try {
      const past = await fetchIdeas(projectId);
      if (!isCurrent()) return;
      const all = pastBatchesFromRecord(past.batches ?? {});
      setPastBatches(all);
      setIdeaCandidates((prev) => {
        const latestBatchId = prev[0]?.batch_id;
        return latestBatchId ? (all[latestBatchId] ?? prev) : prev;
      });
    } catch { /* noop */ }
  };

  const refetchIdeas = async () => {
    if (hydrating) return;
    const isCurrent = captureOperation();
    if (!ideaProjectId) return;
    await refetchIdeasForProject(ideaProjectId);
    if (!isCurrent()) return;
  };

  const handleBookmarkIdea = async (ideaId: number) => {
    if (hydrating) return;
    const isCurrent = captureOperation();
    if (!ideaProjectId) return;
    try {
      await bookmarkIdea(ideaProjectId, ideaId);
      if (!isCurrent()) return;
      await refetchIdeas();
      if (!isCurrent()) return;
    } catch { /* noop */ }
  };

  const handleArchiveIdea = async (ideaId: number) => {
    if (hydrating) return;
    const isCurrent = captureOperation();
    if (!ideaProjectId) return;
    try {
      await archiveIdea(ideaProjectId, ideaId);
      if (!isCurrent()) return;
      await refetchIdeas();
      if (!isCurrent()) return;
    } catch { /* noop */ }
  };

  const handleRegenerateIdea = async (ideaId: number) => {
    if (hydrating) return;
    const isCurrent = captureOperation();
    if (!ideaProjectId) return;
    if (regeneratingIds.includes(ideaId)) return;
    setRegeneratingIds((prev) => [...prev, ideaId]);
    try {
      await regenerateIdea(ideaProjectId, ideaId);
      if (!isCurrent()) return;
      await refetchIdeas();
      if (!isCurrent()) return;
    } catch { /* noop */ } finally {
      if (isCurrent()) {
        setRegeneratingIds((prev) => prev.filter((id) => id !== ideaId));
      }
    }
  };

  function clearIdeaPending() {
    setIdeaGenerating(false);
    setRegeneratingIds([]);
  }

  function resetIdeas() {
    clearIdeaPending();
    setIdeaProjectId(null);
    setIdeaCandidates([]);
    setPastBatches({});
  }

  return {
    ideaCandidates, pastBatches, ideaGenerating, regeneratingIds,
    enterPathB, generateAll, handleSelectIdea, handleBookmarkIdea,
    handleArchiveIdea, handleRegenerateIdea, resetIdeas, clearIdeaPending,
  };
}
