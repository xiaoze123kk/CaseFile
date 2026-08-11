"use client";

import type { IdeaCandidateView } from "./intake-model";
import { reasoningTypeLabels, conclusionModeLabels } from "./intake-model";
import styles from "./idea-candidates-stage.module.css";

interface Props {
  ideas: IdeaCandidateView[];
  pastBatches: Record<string, IdeaCandidateView[]>;
  generating: boolean;
  onSelect: (ideaId: number) => void;
  onBookmark: (ideaId: number) => void;
  onArchive: (ideaId: number) => void;
  onRegenerate: (ideaId: number) => void;
  onGenerateAll: () => void;
}

export default function IdeaCandidatesStage({
  ideas,
  pastBatches,
  generating,
  onSelect,
  onBookmark,
  onArchive,
  onRegenerate,
  onGenerateAll,
}: Props) {
  const activeIdeas = ideas.filter((i) => i.status !== "archived");
  const archivedIdeas = ideas.filter((i) => i.status === "archived");
  const sortedPastBatches = Object.entries(pastBatches)
    .sort(([a], [b]) => b.localeCompare(a))
    .map(([batchId, batchIdeas], index) => ({
      no: index + 1,
      batchId,
      ideas: batchIdeas,
    }));

  if (generating) {
    return (
      <section className={styles.stage}>
        <div className={styles.header}>
          <h2 className={styles.title}>正在生成创意方向...</h2>
          <p className={styles.subtitle}>Agent 正在构思三个推理内容方向，请稍候。</p>
        </div>
        <div className={styles.loading}>
          <div className={styles.spinner} />
          <span>生成中...</span>
        </div>
      </section>
    );
  }

  if (activeIdeas.length === 0) {
    return (
      <section className={styles.stage}>
        <div className={styles.header}>
          <h2 className={styles.title}>创意方向</h2>
          <p className={styles.subtitle}>让 Agent 为你生成三个差异明确的创意方向。</p>
        </div>
        <div className={styles.empty}>
          <button className={styles.primaryBtn} onClick={onGenerateAll}>
            生成创意候选
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className={styles.stage}>
      <div className={styles.header}>
        <h2 className={styles.title}>选择一个创意方向</h2>
        <p className={styles.subtitle}>
          以下是三个差异明确的创作方向，比较后选择其一继续建案。
        </p>
      </div>
      <div className={styles.grid}>
        {activeIdeas.map((idea) => (
          <Card
            key={idea.id}
            idea={idea}
            onSelect={onSelect}
            onBookmark={onBookmark}
            onArchive={onArchive}
            onRegenerate={onRegenerate}
          />
        ))}
      </div>
      {archivedIdeas.length > 0 && (
        <details className={styles.archived}>
          <summary>已淘汰的灵感（{archivedIdeas.length}）</summary>
          <div className={styles.grid}>
            {archivedIdeas.map((idea) => (
              <Card key={idea.id} idea={idea} onSelect={onSelect} onBookmark={onBookmark}
                onArchive={onArchive} onRegenerate={onRegenerate} />
            ))}
          </div>
        </details>
      )}
      {sortedPastBatches.length > 0 && (
        <details className={styles.archived}>
          <summary>
            灵感资产（{sortedPastBatches.reduce((s, b) => s + b.ideas.length, 0)} 个历史候选）
          </summary>
          {sortedPastBatches.map(({ no, batchId, ideas: batchIdeas }) => (
            <details key={batchId} className={styles.archived} style={{ marginLeft: "1rem" }}>
              <summary>第 {no} 批（{batchIdeas.length} 个候选）</summary>
              <div className={styles.grid}>
                {batchIdeas.map((idea) => (
                  <Card key={idea.id} idea={idea} readonly onSelect={onSelect} onBookmark={onBookmark}
                    onArchive={onArchive} onRegenerate={onRegenerate} />
                ))}
              </div>
            </details>
          ))}
        </details>
      )}
    </section>
  );
}

function Card({
  idea,
  onSelect,
  onBookmark,
  onArchive,
  onRegenerate,
  readonly = false,
}: {
  idea: IdeaCandidateView;
  onSelect: (id: number) => void;
  onBookmark: (id: number) => void;
  onArchive: (id: number) => void;
  onRegenerate: (id: number) => void;
  readonly?: boolean;
}) {
  const archived = idea.status === "archived";
  const c = idea.content;

  return (
    <article
      className={`${styles.card} ${archived ? styles.archivedCard : ""} ${idea.status === "bookmarked" ? styles.bookmarked : ""}`}
    >
      <div className={styles.cardTop}>
        <span className={styles.ordinal}>#{idea.ordinal}</span>
        {idea.bookmarked && (
          <span className={styles.starBadge}>已收藏</span>
        )}
      </div>
      <h3 className={styles.concept}>{c.concept}</h3>
      <dl className={styles.fields}>
        <div className={styles.field}>
          <dt className={styles.fieldLabel}>核心悬念</dt>
          <dd>{c.core_suspense}</dd>
        </div>
        <div className={styles.field}>
          <dt className={styles.fieldLabel}>推理类型</dt>
          <dd>{reasoningTypeLabels[c.reasoning_type] ?? c.reasoning_type}</dd>
        </div>
        <div className={styles.field}>
          <dt className={styles.fieldLabel}>结论模式</dt>
          <dd>{conclusionModeLabels[c.conclusion_mode] ?? c.conclusion_mode}</dd>
        </div>
        <div className={styles.field}>
          <dt className={styles.fieldLabel}>目标体验</dt>
          <dd>{c.target_experience}</dd>
        </div>
        <div className={styles.field}>
          <dt className={styles.fieldLabel}>设计风险</dt>
          <dd>{c.design_risk}</dd>
        </div>
        <div className={styles.field}>
          <dt className={styles.fieldLabel}>预计规模</dt>
          <dd>{c.scale_estimate}</dd>
        </div>
      </dl>
      {!archived && (
        <div className={styles.actions}>
          {!readonly && (
            <>
              <button className={styles.selectBtn} onClick={() => onSelect(idea.id)}>
                选择此方向
              </button>
              <button className={styles.iconBtn} onClick={() => onRegenerate(idea.id)} title="重新生成">
                ↻
              </button>
            </>
          )}
          <button className={styles.iconBtn} onClick={() => onBookmark(idea.id)} title="收藏">
            {idea.bookmarked ? "★" : "☆"}
          </button>
          <button className={styles.iconBtn} onClick={() => onArchive(idea.id)} title="淘汰">
            ✕
          </button>
        </div>
      )}
    </article>
  );
}
