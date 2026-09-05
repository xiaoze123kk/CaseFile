"use client";

import { useMemo, useState } from "react";

import type { WorkbenchModel } from "./workbench-real-data-types";
import {
  classificationLabel,
  objectSubtypeLabel,
  reasoningOutcomeLabels,
  reliabilityLabel,
} from "./workbench-presenters";
import styles from "./analyst-workbench.module.css";

const assessmentEffectLabels = {
  supports: "支持",
  contradicts: "冲突",
  neutral: "不区分",
  unassessed: "未评估",
} as const;

const assessmentStrengthLabels = {
  weak: "弱",
  moderate: "中",
  strong: "强",
} as const;

interface EvidenceCell {
  hypothesisId: string;
  informationId: string;
}

interface InformationFacts {
  classification: string | null;
  informationType: string | null;
  supportsClaimIds: string[];
  refutesClaimIds: string[];
}

function readObjectIds(refs: unknown): string[] {
  if (!Array.isArray(refs)) return [];
  const ids: string[] = [];
  for (const ref of refs) {
    if (
      typeof ref === "object" &&
      ref !== null &&
      typeof (ref as { object_id?: unknown }).object_id === "string"
    ) {
      ids.push((ref as { object_id: string }).object_id);
    }
  }
  return ids;
}

export function EvidenceComparisonView({
  seed,
  selectedObjectId,
  onSelectObject,
}: {
  seed: WorkbenchModel;
  selectedObjectId: string | null;
  onSelectObject: (objectId: string) => void;
}) {
  const groups = seed.reasoningGroups;
  const comparisonGroups = useMemo(
    () => (groups ?? []).filter((group) => group.hypotheses.length > 0),
    [groups],
  );
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);
  const [selectedCell, setSelectedCell] = useState<EvidenceCell | null>(null);

  const activeGroup =
    comparisonGroups.find(
      (group) =>
        group.hypotheses.some((item) => item.id === selectedObjectId) ||
        group.information.some((item) => item.id === selectedObjectId),
    ) ??
    comparisonGroups.find(
      (group) => group.resolutionSpecId === selectedGroupId,
    ) ??
    comparisonGroups[0] ??
    null;

  const caseFile = seed.caseFile;

  const informationFacts = useMemo(() => {
    const facts = new Map<string, InformationFacts>();
    if (!caseFile) return facts;
    for (const unit of caseFile.information_units) {
      facts.set(unit.id, {
        classification: unit.classification ?? null,
        informationType: unit.information_type ?? null,
        supportsClaimIds: readObjectIds(unit.supports_claim_refs),
        refutesClaimIds: readObjectIds(unit.refutes_claim_refs),
      });
    }
    return facts;
  }, [caseFile]);

  const claimTitles = useMemo(() => {
    const titles = new Map<string, string>();
    if (!caseFile) return titles;
    for (const claim of caseFile.claims) titles.set(claim.id, claim.title);
    return titles;
  }, [caseFile]);

  if (activeGroup === null) {
    return (
      <section
        aria-labelledby="evidence-matrix-heading"
        className={styles.realEmptyState}
      >
        <span>线索对比</span>
        <strong id="evidence-matrix-heading">
          当前工作稿还没有可比较的假设。
        </strong>
        <p>
          采用包含不同解释与线索判断的工作稿后，这里会展示每条线索更支持哪一种解释。
        </p>
      </section>
    );
  }

  const group = activeGroup;
  const conclusion = group.conclusion ?? null;

  const assessmentFor = (hypothesisId: string, informationId: string) =>
    group.assessments.find(
      (assessment) =>
        assessment.hypothesisId === hypothesisId &&
        assessment.informationId === informationId,
    ) ?? null;

  const cellFor = (cell: EvidenceCell) => {
    const hypothesis = group.hypotheses.find(
      (item) => item.id === cell.hypothesisId,
    );
    const information = group.information.find(
      (item) => item.id === cell.informationId,
    );
    if (!hypothesis || !information) return null;
    return {
      hypothesis,
      information,
      assessment: assessmentFor(hypothesis.id, information.id),
    };
  };

  const selected = selectedCell ? cellFor(selectedCell) : null;
  const selectedInformationFacts = selected
    ? (informationFacts.get(selected.information.id) ?? null)
    : null;

  const claimNames = (ids: string[]) =>
    ids.map((id) => claimTitles.get(id) ?? id);
  const supportsClaimNames = selectedInformationFacts
    ? claimNames(selectedInformationFacts.supportsClaimIds)
    : [];
  const refutesClaimNames = selectedInformationFacts
    ? claimNames(selectedInformationFacts.refutesClaimIds)
    : [];

  return (
    <section
      aria-labelledby="evidence-matrix-heading"
      className={styles.evidenceMatrixView}
    >
      <header className={styles.sectionHeader}>
        <div>
          <span>线索如何支持不同解释</span>
          <h2 id="evidence-matrix-heading">{group.question}</h2>
        </div>
        <div className={styles.evidenceMatrixControls}>
          {comparisonGroups.length > 1 ? (
            <label className={styles.evidenceGroupPicker}>
              <span>核心问题</span>
              <select
                onChange={(event) => {
                  setSelectedGroupId(event.target.value);
                  setSelectedCell(null);
                }}
                value={group.resolutionSpecId}
              >
                {comparisonGroups.map((item) => (
                  <option
                    key={item.resolutionSpecId}
                    value={item.resolutionSpecId}
                  >
                    {item.question}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {conclusion ? (
            <small>
              {conclusion.reviewStatus === "confirmed"
                ? "作者已确认"
                : "待作者确认"}
            </small>
          ) : null}
        </div>
      </header>

      {group.hypotheses.length < 2 && group.information.length > 0 ? (
        <p className={styles.evidenceMatrixNotice}>
          当前问题只有一个假设，至少需要两个解释才能比较。
        </p>
      ) : null}

      {group.information.length === 0 ? (
        <div className={styles.evidenceMatrixEmpty}>
          <strong>
            {group.hypotheses.length < 2
              ? "当前问题只有一个假设，至少需要两个解释才能比较。"
              : "已有竞争解释，但尚未生成显式证据评估。"}
          </strong>
          <p>
            生成或采用深稿后，这里会逐格展示每条证据对每个假设的支持、冲突与理由。
          </p>
        </div>
      ) : (
        <div className={styles.evidenceMatrixWrap}>
          <table className={styles.evidenceMatrix}>
            <thead>
              <tr>
                <th className={styles.evidenceMatrixCorner} scope="col">
                  线索
                </th>
                {group.hypotheses.map((hypothesis) => (
                  <th key={hypothesis.id} scope="col" data-agent-object-id={hypothesis.id}>
                    <button
                      aria-label={`假设：${hypothesis.title}`}
                      aria-pressed={hypothesis.id === selectedObjectId}
                      className={styles.evidenceMatrixHead}
                      onClick={() => onSelectObject(hypothesis.id)}
                      type="button"
                    >
                      <strong>{hypothesis.title}</strong>
                      <small>{reasoningOutcomeLabels[hypothesis.outcome]}</small>
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {group.information.map((information) => (
                <tr key={information.id} data-agent-object-id={information.id}>
                  <th scope="row">
                    <button
                      aria-label={`信息：${information.title}`}
                      aria-pressed={information.id === selectedObjectId}
                      className={styles.evidenceMatrixRowHead}
                      onClick={() => onSelectObject(information.id)}
                      type="button"
                    >
                      <strong>{information.title}</strong>
                      <small>
                        可靠度 {reliabilityLabel(information.reliability)}
                      </small>
                    </button>
                  </th>
                  {group.hypotheses.map((hypothesis) => {
                    const assessment = assessmentFor(
                      hypothesis.id,
                      information.id,
                    );
                    const active =
                      selectedCell?.hypothesisId === hypothesis.id &&
                      selectedCell?.informationId === information.id;
                    const label = assessment
                      ? `${assessmentEffectLabels[assessment.effect]} · ${assessmentStrengthLabels[assessment.strength]}`
                      : "未评估";
                    return (
                      <td key={hypothesis.id}>
                        <button
                          aria-label={`${information.title} 对 ${hypothesis.title}：${label}`}
                          aria-pressed={active}
                          className={styles.evidenceMatrixCell}
                          data-effect={assessment?.effect ?? "unassessed"}
                          onClick={() =>
                            setSelectedCell({
                              hypothesisId: hypothesis.id,
                              informationId: information.id,
                            })
                          }
                          type="button"
                        >
                          {label}
                        </button>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {selected ? (
        <aside
          aria-label="证据判定依据"
          className={styles.evidenceCellDetail}
          data-effect={selected.assessment?.effect ?? "unassessed"}
        >
          <header>
            <div>
              <span>判断依据 · {selected.hypothesis.title}</span>
              <strong>{selected.information.title}</strong>
            </div>
            <button
              aria-label="关闭判定依据"
              onClick={() => setSelectedCell(null)}
              type="button"
            >
              ×
            </button>
          </header>
          <dl className={styles.evidenceCellFacts}>
            <div>
              <dt>可靠度</dt>
              <dd>{reliabilityLabel(selected.information.reliability)}</dd>
            </div>
            <div>
              <dt>故事作用</dt>
              <dd>
                {selectedInformationFacts?.classification
                  ? classificationLabel(selectedInformationFacts.classification)
                  : "—"}
              </dd>
            </div>
            <div>
              <dt>线索类型</dt>
              <dd>
                {selectedInformationFacts?.informationType
                  ? objectSubtypeLabel(selectedInformationFacts.informationType)
                  : "—"}
              </dd>
            </div>
            <div>
              <dt>解释状态</dt>
              <dd>{reasoningOutcomeLabels[selected.hypothesis.outcome]}</dd>
            </div>
          </dl>
          {selected.assessment ? (
            <>
              <p className={styles.evidenceCellEffect}>
                {assessmentEffectLabels[selected.assessment.effect]} ·{" "}
                {assessmentStrengthLabels[selected.assessment.strength]}
              </p>
              <p>{selected.assessment.rationale}</p>
            </>
          ) : (
            <p>该信息与此假设尚未评估；系统不会根据其他引用推断结论。</p>
          )}
          {supportsClaimNames.length ? (
            <div className={styles.evidenceCellClaims}>
              <span>支持的主张</span>
              <p>{supportsClaimNames.join("、")}</p>
            </div>
          ) : null}
          {refutesClaimNames.length ? (
            <div className={styles.evidenceCellClaims}>
              <span>反驳的主张</span>
              <p>{refutesClaimNames.join("、")}</p>
            </div>
          ) : null}
        </aside>
      ) : null}
    </section>
  );
}
