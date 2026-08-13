"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useCaseSession } from "@/features/case-session/case-session-provider";
import {
  uploadReverseParseDocument,
  fetchReverseParseDocuments,
  fetchReverseParseDocument,
  fetchReverseParseBlocks,
  confirmReverseParseItem,
  retryReverseParse,
  formBriefFromReverseParse,
  waitForTask,
  createCaseProject,
  type ReverseParseDocumentView,
  type ReverseParseItemView,
} from "@/features/case-session/case-session-api";
import styles from "./reverse-parse-stage.module.css";

type Filter = "all" | "unconfirmed" | "confirmed" | "rejected";

const ITEM_TYPE_LABELS: Record<string, string> = {
  entity_alias: "实体与别名",
  event: "事件",
  information_unit: "信息单元",
  knowledge_state: "知识状态",
  relationship_causality: "关系与因果",
  candidate_question: "候选目标问题",
  candidate_conclusion: "候选结论",
};

const FILTER_LABELS: Record<Filter, string> = {
  all: "全部",
  unconfirmed: "待处理",
  confirmed: "已确认",
  rejected: "已驳回",
};

const CONFIRM_STATUS_LABELS: Record<ReverseParseItemView["confirm_status"], string> = {
  unconfirmed: "待处理",
  confirmed: "已确认",
  rejected: "已驳回",
};

const CONTENT_KEY_LABELS: Record<string, string> = {
  name: "名称",
  aliases: "别名",
  description: "描述",
  title: "标题",
  order_index: "顺序",
  question: "问题",
  conclusion: "结论",
  mode: "模式",
  statement: "陈述",
  subject: "主体",
  object: "对象",
  relation: "关系",
  state: "状态",
  evidence: "依据",
  summary: "摘要",
  text: "内容",
};

const HIGH_RISK_GRADINGS = new Set(["conflicting", "missing_important"]);

function gradingBadgeClass(grading: ReverseParseItemView["grading"]) {
  switch (grading) {
    case "explicit":
      return styles.badgeExplicit;
    case "inferred":
      return styles.badgeInferred;
    case "needs_confirmation":
      return styles.badgeNeedsConfirmation;
    case "conflicting":
      return styles.badgeConflicting;
    default:
      return styles.badgeMissing;
  }
}

function contentEntries(content: Record<string, unknown>) {
  return Object.entries(content)
    .filter(([, value]) => value !== null && value !== undefined && value !== "")
    .map(([key, value]) => ({
      key,
      label: CONTENT_KEY_LABELS[key] ?? key,
      text: Array.isArray(value) ? value.map(String).join("、") : String(value),
    }));
}

function docStatusLabel(doc: ReverseParseDocumentView) {
  switch (doc.parse_status) {
    case "queued":
      return "排队中";
    case "running":
      return "解析中";
    case "failed":
      return "解析失败";
    default:
      return "已完成";
  }
}

function ItemCard({
  item,
  onConfirm,
  onReject,
  onShowSource,
}: {
  item: ReverseParseItemView;
  onConfirm: () => void;
  onReject: () => void;
  onShowSource: () => void;
}) {
  const entries = contentEntries(item.content);
  return (
    <article
      className={styles.itemCard}
      data-confirm-status={item.confirm_status}
      onClick={onShowSource}
    >
      <div className={styles.itemTop}>
        <span className={styles.itemType}>
          {ITEM_TYPE_LABELS[item.item_type] ?? item.item_type}
        </span>
        <span className={`${styles.gradingBadge} ${gradingBadgeClass(item.grading)}`}>
          {item.grading_label || item.grading}
        </span>
      </div>
      {entries.length > 0 ? (
        <dl className={styles.itemContent}>
          {entries.map((entry) => (
            <div className={styles.itemField} key={entry.key}>
              <dt>{entry.label}</dt>
              <dd>{entry.text}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {item.source_quote ? <p className={styles.quote}>“{item.source_quote}”</p> : null}
      <div className={styles.itemActions} onClick={(event) => event.stopPropagation()}>
        {item.confirm_status === "unconfirmed" ? (
          <>
            <button className={styles.confirmBtn} onClick={onConfirm} type="button">
              确认
            </button>
            <button className={styles.rejectBtn} onClick={onReject} type="button">
              驳回
            </button>
          </>
        ) : (
          <span className={styles.statusTag} data-status={item.confirm_status}>
            {CONFIRM_STATUS_LABELS[item.confirm_status]}
          </span>
        )}
        <button
          className={styles.sourceBtn}
          disabled={item.source_block_refs.length === 0}
          onClick={onShowSource}
          type="button"
        >
          查看来源
        </button>
      </div>
    </article>
  );
}

interface ReverseParseStageProps {
  onFormed?: () => void;
}

export default function ReverseParseStage({ onFormed }: ReverseParseStageProps) {
  const { activeProjectId, loadProject } = useCaseSession();
  const [createdProjectId, setCreatedProjectId] = useState<number | null>(null);
  const projectId = activeProjectId ?? createdProjectId;
  const [documents, setDocuments] = useState<ReverseParseDocumentView[]>([]);
  const [activeDoc, setActiveDoc] = useState<ReverseParseDocumentView | null>(null);
  const [items, setItems] = useState<ReverseParseItemView[]>([]);
  const [loadedDocId, setLoadedDocId] = useState<number | null>(null);
  const [blocks, setBlocks] = useState<Array<{ block_no: number; text: string }>>([]);
  const [filter, setFilter] = useState<Filter>("all");
  const [uploading, setUploading] = useState(false);
  const [forming, setForming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [highlightBlock, setHighlightBlock] = useState<number | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const sourcePaneRef = useRef<HTMLElement>(null);

  // ensureProject：复用当前活动项目，否则在首次操作时创建项目。
  const ensureProject = useCallback(async (): Promise<number | null> => {
    if (activeProjectId !== null) return activeProjectId;
    if (createdProjectId !== null) return createdProjectId;
    const project = await createCaseProject("已有内容反向解析");
    setCreatedProjectId(project.id);
    return project.id;
  }, [activeProjectId, createdProjectId]);

  useEffect(() => {
    if (projectId === null) return;
    let cancelled = false;
    void (async () => {
      try {
        const { documents: docs } = await fetchReverseParseDocuments(projectId);
        if (cancelled) return;
        setDocuments(docs);
        setActiveDoc((current) => {
          if (current) return current;
          return (
            docs.find((doc) => doc.parse_status === "succeeded") ?? docs[0] ?? null
          );
        });
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "文档列表加载失败。");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // 当前文档的来源块与抽取条目。
  useEffect(() => {
    if (projectId === null || activeDoc === null) return;
    if (loadedDocId === activeDoc.id) return;
    let cancelled = false;
    void (async () => {
      try {
        const blockData = await fetchReverseParseBlocks(projectId, activeDoc.id);
        if (!cancelled) setBlocks(blockData.blocks);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "来源块加载失败。");
        }
      }
      if (activeDoc.parse_status !== "succeeded") return;
      try {
        const detail = await fetchReverseParseDocument(projectId, activeDoc.id);
        if (!cancelled) {
          setItems(detail.items);
          setLoadedDocId(activeDoc.id);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "抽取条目加载失败。");
          setLoadedDocId(activeDoc.id);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId, activeDoc, loadedDocId]);

  // 选中正在解析的文档时继续轮询任务，完成后刷新文档详情。
  useEffect(() => {
    if (projectId === null || activeDoc === null) return;
    if (activeDoc.parse_status !== "queued" && activeDoc.parse_status !== "running") {
      return;
    }
    if (activeDoc.current_task_run_id === null) return;
    const taskRunId = activeDoc.current_task_run_id;
    let cancelled = false;
    void (async () => {
      try {
        await waitForTask(projectId, taskRunId);
        if (cancelled) return;
        const [detail, blockData] = await Promise.all([
          fetchReverseParseDocument(projectId, activeDoc.id),
          fetchReverseParseBlocks(projectId, activeDoc.id),
        ]);
        if (cancelled) return;
        setDocuments((prev) =>
          prev.map((doc) => (doc.id === activeDoc.id ? detail.document : doc)),
        );
        setActiveDoc(detail.document);
        setItems(detail.items);
        setBlocks(blockData.blocks);
        setLoadedDocId(activeDoc.id);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "解析任务未完成。");
        const failed: ReverseParseDocumentView = {
          ...activeDoc,
          parse_status: "failed",
        };
        setDocuments((prev) =>
          prev.map((doc) => (doc.id === activeDoc.id ? failed : doc)),
        );
        setActiveDoc(failed);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId, activeDoc]);

  const handleFileChange = (event: { target: HTMLInputElement }) => {
    const file = event.target.files?.[0];
    if (file) void handleUpload(file);
    event.target.value = "";
  };

  // handleUpload：上传文件 → 轮询 task_run → 刷新文档详情与条目。
  async function handleUpload(file: File) {
    if (uploading) return;
    const pid = await ensureProject();
    if (pid === null) return;
    setUploading(true);
    setError(null);
    try {
      const { document, task } = await uploadReverseParseDocument(pid, file);
      setDocuments((prev) => [
        document,
        ...prev.filter((doc) => doc.id !== document.id),
      ]);
      await waitForTask(pid, task.task_run_id);
      const [detail, blockData] = await Promise.all([
        fetchReverseParseDocument(pid, document.id),
        fetchReverseParseBlocks(pid, document.id),
      ]);
      setDocuments((prev) =>
        prev.map((doc) => (doc.id === document.id ? detail.document : doc)),
      );
      setActiveDoc(detail.document);
      setItems(detail.items);
      setBlocks(blockData.blocks);
      setLoadedDocId(document.id);
      setHighlightBlock(null);
      setFilter("all");
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传解析失败。");
    } finally {
      setUploading(false);
    }
  }

  async function handleRetry() {
    if (projectId === null || activeDoc === null) return;
    setError(null);
    try {
      const { task } = await retryReverseParse(projectId, activeDoc.id);
      const running: ReverseParseDocumentView = {
        ...activeDoc,
        parse_status: "running",
        current_task_run_id: task.task_run_id,
      };
      setLoadedDocId(null);
      setActiveDoc(running);
      setDocuments((prev) =>
        prev.map((doc) => (doc.id === activeDoc.id ? running : doc)),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "重新解析失败。");
    }
  }

  async function handleConfirm(itemId: number, action: "confirm" | "reject") {
    if (projectId === null) return;
    setError(null);
    try {
      const updated = await confirmReverseParseItem(projectId, itemId, action);
      setItems((prev) => prev.map((item) => (item.id === itemId ? updated : item)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "条目操作失败。");
    }
  }

  const itemsLoading = activeDoc !== null && loadedDocId !== activeDoc.id;
  const highRiskPending = items.filter(
    (item) =>
      item.confirm_status === "unconfirmed" && HIGH_RISK_GRADINGS.has(item.grading),
  );
  const canFormBrief = Boolean(
    activeDoc &&
      activeDoc.parse_status === "succeeded" &&
      !itemsLoading &&
      !uploading &&
      !forming &&
      highRiskPending.length === 0,
  );

  // handleFormBrief：后端 409（高风险未处理）兜底展示错误消息。
  async function handleFormBrief() {
    if (projectId === null || activeDoc === null || !canFormBrief) return;
    setError(null);
    setForming(true);
    try {
      await formBriefFromReverseParse(projectId, activeDoc.id);
      await loadProject(projectId);
      setForming(false);
      onFormed?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "形成创作简报失败。");
      setForming(false);
    }
  }

  function handleSelectDoc(doc: ReverseParseDocumentView) {
    if (activeDoc?.id === doc.id) {
      setHighlightBlock(null);
      setError(null);
      return;
    }
    setActiveDoc(doc);
    setLoadedDocId(null);
    setHighlightBlock(null);
    setError(null);
  }

  function handleShowSource(item: ReverseParseItemView) {
    const blockNo = item.source_block_refs[0];
    const resolved = typeof blockNo === "number" ? blockNo : null;
    setHighlightBlock(resolved);
    // 高亮后自动把来源面板滚动到对应块，避免用户手动寻找。
    if (resolved !== null) {
      requestAnimationFrame(() => {
        sourcePaneRef.current
          ?.querySelector(`[data-block-no="${resolved}"]`)
          ?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
    }
  }

  const filteredItems =
    filter === "all"
      ? items
      : items.filter((item) => item.confirm_status === filter);
  const groupEntries = (() => {
    const map = new Map<string, ReverseParseItemView[]>();
    for (const item of filteredItems) {
      const list = map.get(item.item_type) ?? [];
      list.push(item);
      map.set(item.item_type, list);
    }
    return [...map.entries()];
  })();

  function filterCount(value: Filter) {
    if (value === "all") return items.length;
    return items.filter((item) => item.confirm_status === value).length;
  }

  const parsingActive = Boolean(
    activeDoc &&
      (activeDoc.parse_status === "queued" || activeDoc.parse_status === "running"),
  );

  return (
    <section className={styles.stage}>
      <header className={styles.header}>
        <h2 className={styles.title}>反向解析审阅</h2>
        <p className={styles.subtitle}>
          上传现成内容，Agent 会抽取实体、事件与候选问题；逐项确认后拼装创作简报。
        </p>
      </header>

      <input
        aria-label="选择要解析的文件"
        className={styles.hiddenInput}
        onChange={handleFileChange}
        ref={fileInputRef}
        type="file"
      />

      {error ? (
        <p className={styles.error} role="alert">
          {error}
        </p>
      ) : null}

      {documents.length === 0 ? (
        <div className={styles.uploadZone}>
          <p>还没有上传任何内容。选择一份已有文档，Agent 会先分块再逐条抽取。</p>
          <button
            className={styles.uploadBtn}
            disabled={uploading}
            onClick={() => fileInputRef.current?.click()}
            type="button"
          >
            {uploading ? "上传中…" : "上传文档并解析"}
          </button>
          {uploading ? (
            <div className={styles.loading}>
              <div className={styles.spinner} />
              <span>正在上传并解析，完成后条目会出现在这里。</span>
            </div>
          ) : null}
        </div>
      ) : (
        <div className={styles.body}>
          <aside aria-label="文档列表" className={styles.docList}>
            <div className={styles.docListHeader}>
              <span>已上传文档</span>
              <button
                className={styles.addBtn}
                disabled={uploading}
                onClick={() => fileInputRef.current?.click()}
                type="button"
              >
                ＋ 上传
              </button>
            </div>
            {documents.map((doc) => (
              <button
                className={`${styles.docCard} ${activeDoc?.id === doc.id ? styles.docCardActive : ""}`}
                key={doc.id}
                onClick={() => handleSelectDoc(doc)}
                type="button"
              >
                <span className={styles.docName}>{doc.filename}</span>
                <span className={styles.docMeta}>{docStatusLabel(doc)}</span>
              </button>
            ))}
          </aside>

          <div className={styles.itemsPane}>
            {uploading ? (
              <div className={styles.loading}>
                <div className={styles.spinner} />
                <span>正在上传并解析新文档…</span>
              </div>
            ) : null}
            {activeDoc === null ? (
              <p className={styles.subtitle}>从左侧选择一份文档开始审阅。</p>
            ) : activeDoc.parse_status === "failed" ? (
              <div className={styles.docStatusPanel}>
                <p>这份文档解析失败。可以重新发起解析，已有确认记录不会丢失。</p>
                <button
                  className={styles.uploadBtn}
                  onClick={() => void handleRetry()}
                  type="button"
                >
                  重新解析
                </button>
              </div>
            ) : parsingActive ? (
              <div className={styles.loading}>
                <div className={styles.spinner} />
                <span>解析中…</span>
              </div>
            ) : itemsLoading ? (
              <div className={styles.loading}>
                <div className={styles.spinner} />
                <span>条目加载中…</span>
              </div>
            ) : (
              <>
                <div className={styles.filterBar}>
                  {(Object.keys(FILTER_LABELS) as Filter[]).map((value) => (
                    <button
                      className={`${styles.filterBtn} ${filter === value ? styles.filterBtnActive : ""}`}
                      key={value}
                      onClick={() => setFilter(value)}
                      type="button"
                    >
                      {FILTER_LABELS[value]} {filterCount(value)}
                    </button>
                  ))}
                </div>
                {groupEntries.length === 0 ? (
                  <p className={styles.subtitle}>这份文档还没有抽取到条目。</p>
                ) : (
                  groupEntries.map(([itemType, groupItems]) => (
                    <section className={styles.group} key={itemType}>
                      <header className={styles.groupHeader}>
                        <h3 className={styles.groupTitle}>
                          {ITEM_TYPE_LABELS[itemType] ?? itemType}
                        </h3>
                        <span className={styles.groupCount}>{groupItems.length}</span>
                      </header>
                      <div className={styles.itemList}>
                        {groupItems.map((item) => (
                          <ItemCard
                            item={item}
                            key={item.id}
                            onConfirm={() => void handleConfirm(item.id, "confirm")}
                            onReject={() => void handleConfirm(item.id, "reject")}
                            onShowSource={() => handleShowSource(item)}
                          />
                        ))}
                      </div>
                    </section>
                  ))
                )}
                <footer className={styles.footer}>
                  <p className={styles.hint}>
                    {highRiskPending.length > 0
                      ? `还有 ${highRiskPending.length} 项高风险条目未处理（前后冲突或缺失但可能重要）。`
                      : "高风险条目已全部处理，可以拼装创作简报。"}
                  </p>
                  <button
                    className={styles.formBriefBtn}
                    disabled={!canFormBrief}
                    onClick={() => void handleFormBrief()}
                    type="button"
                  >
                    {forming ? "正在形成…" : "形成创作简报"}
                  </button>
                </footer>
              </>
            )}
          </div>

          <aside aria-label="来源块" className={styles.sourcePane} ref={sourcePaneRef}>
            <header className={styles.groupHeader}>
              <h3 className={styles.groupTitle}>来源块</h3>
              <span className={styles.groupCount}>{blocks.length}</span>
            </header>
            {blocks.length === 0 ? (
              <p className={styles.subtitle}>暂无来源块。</p>
            ) : (
              blocks.map((block) => (
                <div
                  className={`${styles.block} ${highlightBlock === block.block_no ? styles.blockHighlight : ""}`}
                  data-block-no={block.block_no}
                  key={block.block_no}
                >
                  <span className={styles.blockNo}>[{block.block_no}]</span>
                  <p>{block.text}</p>
                </div>
              ))
            )}
          </aside>
        </div>
      )}
    </section>
  );
}
