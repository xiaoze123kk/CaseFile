"use client";

import { useState } from "react";
import type { IdeaGenerationPreferences } from "@/features/case-session/case-session-api";
import {
  ideaAtmosphereOptions,
  ideaEraOptions,
  ideaSettingOptions,
  reasoningTypeLabels,
  conclusionModeLabels,
  type IdeaCandidateView,
} from "./intake-model";
import styles from "./idea-candidates-stage.module.css";

interface Props {
  ideas: IdeaCandidateView[];
  pastBatches: Record<string, IdeaCandidateView[]>;
  generating: boolean;
  regeneratingIds: number[];
  onSelect: (ideaId: number) => void;
  onBookmark: (ideaId: number) => void;
  onArchive: (ideaId: number) => void;
  onRegenerate: (ideaId: number) => void;
  onGenerateAll: (prefs: IdeaGenerationPreferences) => void;
}

export default function IdeaCandidatesStage({
  ideas,
  pastBatches,
  generating,
  regeneratingIds,
  onSelect,
  onBookmark,
  onArchive,
  onRegenerate,
  onGenerateAll,
}: Props) {
  const [eras, setEras] = useState<string[]>([]);
  const [settings, setSettings] = useState<string[]>([]);
  const [atmospheres, setAtmospheres] = useState<string[]>([]);
  const [keywords, setKeywords] = useState("");

  const activeIdeas = ideas.filter((i) => i.status !== "archived");
  const archivedIdeas = ideas.filter((i) => i.status === "archived");
  const sortedPastBatches = Object.entries(pastBatches)
    .sort(([a], [b]) => b.localeCompare(a))
    .map(([batchId, batchIdeas], index) => ({
      no: index + 1,
      batchId,
      ideas: batchIdeas,
    }));

  function toggle(list: string[], setList: (next: string[]) => void, value: string) {
    setList(list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);
  }

  function submitGenerate() {
    onGenerateAll({
      eras,
      settings,
      atmospheres,
      keywords: keywords
        .split(/[、,，\s]+/u)
        .map((keyword) => keyword.trim())
        .filter(Boolean),
    });
  }

  return (
    <section className={styles.stage}>
      <header className={styles.header}>
        <h2 className={styles.title}>
          {generating
            ? "正在生成创意方向..."
            : activeIdeas.length === 0
              ? "创意方向"
              : "选择一个创意方向"}
        </h2>
        <p className={styles.subtitle}>
          {generating
            ? "Agent 正在构思三个推理内容方向，请稍候。"
            : "可按时代、场景、氛围与关键词自由组合，或留空由 Agent 自主发挥。"}
        </p>
      </header>

      <div className={styles.preferences}>
        <div className={styles.prefRow}>
          <span className={styles.prefLabel}>时代</span>
          <div className={styles.chips}>
            {ideaEraOptions.map((option) => (
              <button
                className={`${styles.chip} ${eras.includes(option) ? styles.chipActive : ""}`}
                disabled={generating}
                key={option}
                onClick={() => toggle(eras, setEras, option)}
                type="button"
              >
                {option}
              </button>
            ))}
          </div>
        </div>
        <div className={styles.prefRow}>
          <span className={styles.prefLabel}>场景</span>
          <div className={styles.chips}>
            {ideaSettingOptions.map((option) => (
              <button
                className={`${styles.chip} ${settings.includes(option) ? styles.chipActive : ""}`}
                disabled={generating}
                key={option}
                onClick={() => toggle(settings, setSettings, option)}
                type="button"
              >
                {option}
              </button>
            ))}
          </div>
        </div>
        <div className={styles.prefRow}>
          <span className={styles.prefLabel}>氛围</span>
          <div className={styles.chips}>
            {ideaAtmosphereOptions.map((option) => (
              <button
                className={`${styles.chip} ${atmospheres.includes(option) ? styles.chipActive : ""}`}
                disabled={generating}
                key={option}
                onClick={() => toggle(atmospheres, setAtmospheres, option)}
                type="button"
              >
                {option}
              </button>
            ))}
          </div>
        </div>
        <div className={styles.prefRow}>
          <span className={styles.prefLabel}>关键词</span>
          <input
            className={styles.prefInput}
            disabled={generating}
            onChange={(event) => setKeywords(event.target.value)}
            placeholder="例如：时间循环、双胞胎（用逗号分隔）"
            value={keywords}
          />
        </div>
      </div>

      {generating ? (
        <div className={styles.loading}>
          <div className={styles.spinner} />
          <span>生成中...</span>
        </div>
      ) : activeIdeas.length === 0 ? (
        <div className={styles.empty}>
          <button className={styles.primaryBtn} onClick={submitGenerate} type="button">
            生成创意候选
          </button>
        </div>
      ) : (
        <>
          <div className={styles.grid}>
            {activeIdeas.map((idea) => (
              <Card
                idea={idea}
                key={idea.id}
                onArchive={onArchive}
                onBookmark={onBookmark}
                onRegenerate={onRegenerate}
                onSelect={onSelect}
                regenerating={regeneratingIds.includes(idea.id)}
              />
            ))}
          </div>
          {archivedIdeas.length > 0 && (
            <details className={styles.archived}>
              <summary>已淘汰的灵感（{archivedIdeas.length}）</summary>
              <div className={styles.grid}>
                {archivedIdeas.map((idea) => (
                  <Card
                    idea={idea}
                    key={idea.id}
                    onArchive={onArchive}
                    onBookmark={onBookmark}
                    onRegenerate={onRegenerate}
                    onSelect={onSelect}
                  />
                ))}
              </div>
            </details>
          )}
          {sortedPastBatches.length > 0 && (
            <details className={styles.archived}>
              <summary>
                灵感资产（{sortedPastBatches.reduce((sum, batch) => sum + batch.ideas.length, 0)} 个历史候选）
              </summary>
              {sortedPastBatches.map(({ no, batchId, ideas: batchIdeas }) => (
                <details key={batchId} className={styles.archived} style={{ marginLeft: "1rem" }}>
                  <summary>第 {no} 批（{batchIdeas.length} 个候选）</summary>
                  <div className={styles.grid}>
                    {batchIdeas.map((idea) => (
                      <Card
                        idea={idea}
                        key={idea.id}
                        onArchive={onArchive}
                        onBookmark={onBookmark}
                        onRegenerate={onRegenerate}
                        onSelect={onSelect}
                        readonly
                      />
                    ))}
                  </div>
                </details>
              ))}
            </details>
          )}
          <div className={styles.toolbar}>
            <button
              className={styles.primaryBtn}
              disabled={generating}
              onClick={submitGenerate}
              type="button"
            >
              生成全部
            </button>
          </div>
        </>
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
  regenerating = false,
}: {
  idea: IdeaCandidateView;
  onSelect: (id: number) => void;
  onBookmark: (id: number) => void;
  onArchive: (id: number) => void;
  onRegenerate: (id: number) => void;
  readonly?: boolean;
  regenerating?: boolean;
}) {
  const archived = idea.status === "archived";
  const c = idea.content;

  return (
    <article
      className={`${styles.card} ${archived ? styles.archivedCard : ""} ${idea.status === "bookmarked" ? styles.bookmarked : ""}`}
    >
      <div className={styles.cardTop}>
        <span className={styles.ordinal}>#{idea.ordinal}</span>
        {regenerating ? (
          <span className={styles.regenerating}>重新生成中…</span>
        ) : idea.bookmarked ? (
          <span className={styles.starBadge}>已收藏</span>
        ) : null}
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
              <button
                className={styles.selectBtn}
                disabled={regenerating}
                onClick={() => onSelect(idea.id)}
              >
                选择此方向
              </button>
              <button
                className={styles.iconBtn}
                disabled={regenerating}
                onClick={() => onRegenerate(idea.id)}
                title="重新生成"
              >
                ↻
              </button>
            </>
          )}
          <button
            className={styles.iconBtn}
            disabled={regenerating}
            onClick={() => onBookmark(idea.id)}
            title="收藏"
          >
            {idea.bookmarked ? "★" : "☆"}
          </button>
          <button
            className={styles.iconBtn}
            disabled={regenerating}
            onClick={() => onArchive(idea.id)}
            title="淘汰"
          >
            ✕
          </button>
        </div>
      )}
    </article>
  );
}
