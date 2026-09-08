import type {
  NovelEditorView,
  NovelEditorCreate,
  NovelEditorSave,
  NovelEditorRequest,
  NovelEditorExchange,
  NovelEditorVersion,
  NovelEditorVersionDetail,
} from "@casefile/contracts";
import { apiRequest, streamTaskEvents } from "@/lib/api-client";
import { LOCAL_ACTOR_ID } from "@/lib/local-session";
const call = <T>(path: string, method = "GET", body?: unknown): Promise<T> =>
  apiRequest(path, {
    actorId: LOCAL_ACTOR_ID,
    method,
    ...(body === undefined ? {} : { body }),
  });
const root = (project: number) => `/projects/${project}/novels`;
export const novelEditorApi = {
  list: (p: number, draft: number) =>
    call<{ id: number; title: string; source_key: string; revision: number }[]>(
      `${root(p)}?draft_id=${draft}`,
    ),
  create: (p: number, data: NovelEditorCreate) =>
    call<NovelEditorView>(root(p), "POST", data),
  get: (p: number, id: number) => call<NovelEditorView>(`${root(p)}/${id}`),
  save: (p: number, id: number, data: NovelEditorSave) =>
    call<NovelEditorView>(`${root(p)}/${id}`, "PUT", data),
  checkpoint: (p: number, id: number, data: NovelEditorSave) =>
    call<NovelEditorView>(`${root(p)}/${id}/versions`, "POST", data),
  history: (p: number, id: number) =>
    call<NovelEditorVersion[]>(`${root(p)}/${id}/versions`),
  version: (p: number, id: number, revision: number) =>
    call<NovelEditorVersionDetail>(`${root(p)}/${id}/versions/${revision}`),
  restore: (p: number, id: number, expected: number, revision: number) =>
    call<NovelEditorView>(`${root(p)}/${id}/restore`, "POST", {
      expected_revision: expected,
      revision,
    }),
  submit: (p: number, id: number, data: NovelEditorRequest) =>
    call<NovelEditorExchange>(`${root(p)}/${id}/collaborations`, "POST", data),
  decide: (
    p: number,
    id: number,
    exchange: number,
    expected: number,
    edits: number[],
    action: "accept" | "reject",
  ) =>
    call<NovelEditorView>(
      `${root(p)}/${id}/collaborations/${exchange}/decisions`,
      "POST",
      { expected_revision: expected, edit_ids: edits, action },
    ),
  cancel: (p: number, task: number) =>
    call(`/projects/${p}/tasks/${task}/cancel`, "POST"),
  stream: (
    p: number,
    task: number,
    onText: (text: string) => void,
    signal: AbortSignal,
    onActivity?: (text: string) => void,
  ) =>
    streamTaskEvents(
      `/projects/${p}/tasks/${task}/stream`,
      LOCAL_ACTOR_ID,
      (event) => {
        if (event.event_type === "novel.activity" && typeof event.payload.text === "string")
          onActivity?.(event.payload.text);
        if (
          event.event_type === "novel.answer.delta" &&
          typeof event.payload.text === "string"
        )
          onText(event.payload.text);
      },
      signal,
    ),
};
