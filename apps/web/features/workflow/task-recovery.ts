"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import {
  apiRequest,
  errorMessage,
  streamTaskEvents,
  type TaskEventView,
  type TaskType,
  type TaskView,
} from "@/lib/api-client";

const terminalTaskStatuses = new Set(["succeeded", "failed", "cancelled"]);
const terminalEventTypes = new Set([
  "task.succeeded",
  "task.failed",
  "task.cancelled",
]);

export function mergeTaskEvent(
  current: TaskEventView[],
  incoming: TaskEventView,
) {
  if (current.some((event) => event.sequence_no === incoming.sequence_no)) {
    return current;
  }
  return [...current, incoming].sort(
    (left, right) => left.sequence_no - right.sequence_no,
  );
}

export function selectNewestTask(
  latestTask: TaskView | null | undefined,
  pointerTask: TaskView | null | undefined,
) {
  return (
    [latestTask, pointerTask]
      .filter((candidate): candidate is TaskView => Boolean(candidate))
      .sort((left, right) => right.task_run_id - left.task_run_id)[0] ?? null
  );
}

export function useRecoverableTask(
  projectId: number | null,
  actorId: number,
  taskType: TaskType,
  pointerId: number | null,
  enabled: boolean,
) {
  const latestQuery = useQuery({
    queryKey: ["latest-task", actorId, projectId, taskType],
    queryFn: () =>
      apiRequest<TaskView | null>(
        `/projects/${projectId}/tasks/latest?task_type=${taskType}`,
        { actorId },
      ),
    enabled: enabled && projectId !== null,
    refetchInterval: (query) =>
      query.state.data &&
      !terminalTaskStatuses.has(query.state.data.status)
        ? 1_000
        : false,
  });
  const pointerQuery = useQuery({
    queryKey: ["task", actorId, projectId, pointerId],
    queryFn: () =>
      apiRequest<TaskView>(`/projects/${projectId}/tasks/${pointerId}`, {
        actorId,
      }),
    enabled: enabled && projectId !== null && pointerId !== null,
    refetchInterval: (query) =>
      query.state.data &&
      !terminalTaskStatuses.has(query.state.data.status)
        ? 1_000
        : false,
  });
  const task = selectNewestTask(latestQuery.data, pointerQuery.data);

  return {
    task,
    error: task ? null : (latestQuery.error ?? pointerQuery.error),
    refetch: async () => {
      await Promise.all([latestQuery.refetch(), pointerQuery.refetch()]);
    },
  };
}

export function useTaskEventStream(
  projectId: number | null,
  actorId: number,
  taskRunId: number | null,
) {
  const [events, setEvents] = useState<TaskEventView[]>([]);
  const [streamError, setStreamError] = useState<{
    taskRunId: number;
    message: string;
  } | null>(null);
  const [retryToken, setRetryToken] = useState(0);

  useEffect(() => {
    if (projectId === null || taskRunId === null) return;
    const activeProjectId = projectId;
    const activeTaskRunId = taskRunId;
    const controller = new AbortController();
    let stopped = false;

    async function waitBeforeReconnect() {
      await new Promise<void>((resolve) => {
        const timeoutId = window.setTimeout(resolve, 1_200);
        controller.signal.addEventListener(
          "abort",
          () => {
            window.clearTimeout(timeoutId);
            resolve();
          },
          { once: true },
        );
      });
    }

    async function connect() {
      try {
        const backlog = await apiRequest<TaskEventView[]>(
          `/projects/${activeProjectId}/tasks/${activeTaskRunId}/events?after_sequence=0`,
          { actorId, signal: controller.signal },
        );
        if (stopped) return;
        setEvents(
          [...backlog].sort(
            (left, right) => left.sequence_no - right.sequence_no,
          ),
        );
        let cursor = backlog.reduce(
          (highest, event) => Math.max(highest, event.sequence_no),
          0,
        );
        let terminalSeen = backlog.some((event) =>
          terminalEventTypes.has(event.event_type),
        );
        while (!stopped && !terminalSeen) {
          try {
            setStreamError(null);
            await streamTaskEvents(
              `/projects/${activeProjectId}/tasks/${activeTaskRunId}/stream`,
              actorId,
              (event) => {
                cursor = Math.max(cursor, event.sequence_no);
                terminalSeen ||= terminalEventTypes.has(event.event_type);
                setEvents((current) => mergeTaskEvent(current, event));
              },
              controller.signal,
              cursor,
            );
          } catch (error) {
            if (
              error instanceof DOMException &&
              error.name === "AbortError"
            ) {
              return;
            }
            setStreamError({
              taskRunId: activeTaskRunId,
              message: errorMessage(error),
            });
          }
          if (!terminalSeen && !stopped) await waitBeforeReconnect();
        }
      } catch (error) {
        if (
          error instanceof DOMException &&
          error.name === "AbortError"
        ) {
          return;
        }
        setStreamError({
          taskRunId: activeTaskRunId,
          message: errorMessage(error),
        });
      }
    }

    void connect();
    return () => {
      stopped = true;
      controller.abort();
    };
  }, [actorId, projectId, retryToken, taskRunId]);

  return {
    events: events.filter((event) => event.task_run_id === taskRunId),
    streamError:
      streamError?.taskRunId === taskRunId
        ? streamError.message
        : null,
    reconnect: () => setRetryToken((current) => current + 1),
  };
}
