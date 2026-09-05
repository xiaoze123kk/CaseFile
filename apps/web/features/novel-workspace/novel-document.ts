/** The editor owns a copy of a manuscript, never the compiler artifact or CaseFile. */
export interface NovelChapter {
  id: string;
  title: string;
  text: string;
}

export interface NovelManuscript {
  id: string;
  title: string;
  sourceLabel: string;
  chapters: NovelChapter[];
}

export interface NovelDraft {
  original: NovelManuscript;
  chapters: NovelChapter[];
  revision: number;
  selectedChapterId: string;
}

export interface NovelChange {
  chapterId: string;
  before: string;
  after: string;
}

export interface NovelReply {
  message: string;
  changes?: NovelChange[];
}

export interface NovelCollaborationRequest {
  instruction: string;
  mode: "discuss" | "rewrite" | "polish";
  scope: "chapter" | "book" | "selection";
  selection: string;
  chapterId: string;
  manuscript: NovelManuscript;
  revision: number;
}

// The compiler integration supplies completed manuscripts and a dedicated prose
// collaboration adapter. CaseFile's object mutation chat must not be used here.
export type NovelCollaborator = (
  request: NovelCollaborationRequest,
) => Promise<NovelReply>;

export function manuscriptFromText(
  text: string,
  title: string,
): NovelManuscript {
  const chapters: NovelChapter[] = [];
  const sourceLines = text.replace(/\r\n?/gu, "\n").split("\n");
  const firstLine = sourceLines.findIndex((line) => line.trim());
  const bookHeading = sourceLines[firstLine]?.trim().match(/^#\s+(.+)$/u);
  if (
    bookHeading &&
    sourceLines.slice(firstLine + 1).some((line) => /^##\s+/u.test(line.trim()))
  ) {
    title = bookHeading[1];
    sourceLines.splice(firstLine, 1);
  }
  let heading = "正文";
  let lines: string[] = [];
  function flush() {
    if (!lines.join("\n").trim() && !chapters.length && heading === "正文")
      return;
    chapters.push({
      id: `chapter-${chapters.length + 1}`,
      title: heading,
      text: lines.join("\n").trim(),
    });
  }
  for (const line of sourceLines) {
    if (
      /^(?:#{1,3}\s+|第[零〇一二三四五六七八九十百千万\d]+[章节卷部](?:\s|[·：:、.．]|$)|chapter\s+\d+\b)/iu.test(
        line.trim(),
      )
    ) {
      flush();
      heading = line.trim().replace(/^#{1,3}\s+/u, "");
      lines = [];
    } else lines.push(line);
  }
  flush();
  return { id: crypto.randomUUID(), title, sourceLabel: "导入初稿", chapters };
}

export function createNovelDraft(original: NovelManuscript): NovelDraft {
  return {
    original,
    chapters: original.chapters.map((chapter) => ({ ...chapter })),
    revision: 1,
    selectedChapterId: original.chapters[0]?.id ?? "",
  };
}

export function wordCount(text: string) {
  return Array.from(text.replace(/\s/gu, "")).length;
}

export function exportNovel(title: string, chapters: NovelChapter[]) {
  return [
    `# ${title}`,
    ...chapters.map((chapter) => `## ${chapter.title}\n\n${chapter.text}`),
  ].join("\n\n");
}

export function applyNovelChanges(
  draft: NovelDraft,
  changes: NovelChange[],
  expectedRevision: number,
): NovelDraft | null {
  if (draft.revision !== expectedRevision || changes.length === 0) return null;
  const ids = new Set<string>();
  for (const change of changes) {
    if (
      ids.has(change.chapterId) ||
      draft.chapters.find((chapter) => chapter.id === change.chapterId)
        ?.text !== change.before
    )
      return null;
    ids.add(change.chapterId);
  }
  return {
    ...draft,
    revision: draft.revision + 1,
    chapters: draft.chapters.map((chapter) => ({
      ...chapter,
      text:
        changes.find((change) => change.chapterId === chapter.id)?.after ??
        chapter.text,
    })),
  };
}

function isChapter(value: unknown): value is NovelChapter {
  if (!value || typeof value !== "object") return false;
  const chapter = value as Partial<NovelChapter>;
  return (
    typeof chapter.id === "string" &&
    typeof chapter.title === "string" &&
    typeof chapter.text === "string"
  );
}

export function parseSavedDraft(raw: string): NovelDraft {
  const value = JSON.parse(raw) as Partial<NovelDraft>;
  if (
    !value.original ||
    typeof value.original.id !== "string" ||
    typeof value.original.title !== "string" ||
    typeof value.original.sourceLabel !== "string" ||
    !Array.isArray(value.original.chapters) ||
    !value.original.chapters.every(isChapter) ||
    !Array.isArray(value.chapters) ||
    !value.chapters.every(isChapter) ||
    !value.chapters.length ||
    new Set(value.chapters.map((chapter) => chapter.id)).size !==
      value.chapters.length ||
    !Number.isSafeInteger(value.revision) ||
    (value.revision ?? 0) < 1 ||
    typeof value.selectedChapterId !== "string" ||
    !value.chapters.some((chapter) => chapter.id === value.selectedChapterId)
  ) {
    throw new Error("无法读取本地编辑稿，请导出备份后重试。");
  }
  return value as NovelDraft;
}
