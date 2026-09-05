import type { ReactNode } from "react";
import type { PublicFinding } from "@casefile/contracts";
import type { CaseFileDocument, WorkbenchContextView } from "@/lib/api-client";
import type { ValidationIssue } from "./analyst-fixture";
import type { CollaborationDetail } from "./workbench-collaboration-state";
import type { ContextItem } from "./workbench-agent-context";
import { findWorkbenchDetailObject } from "./workbench-object-detail-model";
import { buildContextProvenanceModel } from "./workbench-provenance-model";
import { buildContextRelations } from "./workbench-relation-model";
import { WorkbenchRelationDetail } from "./workbench-relation-detail";
import styles from "./workbench-collaboration-detail.module.css";

export interface CollaborationDetailData {
  document: CaseFileDocument | null;
  context: WorkbenchContextView | null;
  issues: ValidationIssue[];
}

const titles = { patch: "修改建议", validation: "验证详情", provenance: "来源详情", relation: "关系详情" };

export function WorkbenchCollaborationDetail({ detail, data, finding, patch, loading, onBack, onLocate, onAddContext, onOpenDetail }: {
  detail: CollaborationDetail;
  data: CollaborationDetailData;
  finding?: PublicFinding;
  patch?: ReactNode;
  loading?: boolean;
  onBack: () => void;
  onLocate: (id: string) => void;
  onAddContext: (items: ContextItem[]) => void;
  onOpenDetail: (detail: CollaborationDetail) => void;
}) {
  let content: ReactNode = null;
  if (detail.kind === "patch") content = patch;
  if (detail.kind === "validation") {
    const issue = data.issues.find((item) => item.id === detail.findingId);
    const value = issue ?? finding;
    const refs = issue ? [...new Set([issue.targetObjectId, issue.eventId, ...issue.evidenceIds].filter((id): id is string => Boolean(id)))] : [];
    if (value) content = <>
      <h3>{value.title}</h3>
      <p>{issue?.summary ?? finding?.statement}</p>
      <dl><dt>来源</dt><dd>{issue?.source ?? "Agent"}</dd><dt>严重度</dt><dd>{{ blocker: "阻断", warning: "提醒", note: "记录" }[value.severity as "blocker" | "warning" | "note"] ?? value.severity}</dd>
        <dt>规则</dt><dd>{issue?.rule ?? "该发现未记录规则"}</dd>
        <dt>定位</dt><dd>{issue?.fieldPath ?? issue?.jsonPath ?? "未记录字段定位"}</dd></dl>
      {refs.map((id) => <button key={id} type="button" onClick={() => onLocate(id)}>打开对象 {id}</button>)}
      {issue?.fixHint ? <p>{issue.fixHint}</p> : null}
      {issue?.explanation ? <p>{issue.explanation}</p> : null}
      {issue ? <button type="button" onClick={() => onAddContext([{ kind: "validation_issue", id: issue.id, label: issue.title }])}>添加到问题</button> : <p>此发现仅供审阅，不代表已授权修改。</p>}
    </>;
  }
  if (data.document && detail.kind === "provenance" && findWorkbenchDetailObject(data.document, detail.objectId)) {
    const model = buildContextProvenanceModel(data.document, detail.objectId, data.context);
    content = <>
      <p>字段命中是文本对应证据，不推断未记录的精确来源映射。</p>
      {model.citations.map((citation, index) => <section key={index}><h3>{citation.fieldLabel}</h3>{citation.matches.map((match) => <blockquote key={match.sourceRecordId}>{match.span.before}<mark>{match.span.match}</mark>{match.span.after}<footer>{match.sourceLabel} · 记录 #{match.sourceRecordId} · 段落 {match.span.paragraphNo}</footer></blockquote>)}</section>)}
      {!model.citations.length ? <p>没有可确定的字段命中片段。</p> : null}
      <h3>来源记录与派生链</h3>
      {model.derivations.map((item) => <section key={item.source.source_record_id}><p>{item.label} · {item.kindLabel} · #{item.source.source_record_id}<br />{item.originNote}{item.parentRecordId !== null ? ` · 来源记录 #${item.parentRecordId}` : ""}</p><details><summary>查看记录正文</summary><p style={{ whiteSpace: "pre-wrap" }}>{item.source.content_text}</p></details></section>)}
      <h3>声明的 Source Fragment</h3>
      {model.fragments.map((fragment) => <p key={fragment.fragmentId}>{fragment.fragmentId}<br />{fragment.paths.join("、") || "未记录精确映射"}</p>)}
      {!model.fragments.length ? <p>未声明来源片段。</p> : null}
    </>;
  }
  if (data.document && detail.kind === "relation") {
    const relation = buildContextRelations(data.document, detail.objectId).groups.flatMap((group) => group.relations).find((item) => item.id === detail.relationId);
    if (relation) content = <WorkbenchRelationDetail relation={relation} document={data.document} onLocate={onLocate} onOpenDetail={onOpenDetail} onAddContext={onAddContext} />;
  }
  return <section className={styles.detail} data-kind={detail.kind} aria-label={titles[detail.kind]}>
    <header><button type="button" onClick={onBack}>返回</button><h2>{titles[detail.kind]}</h2></header>
    <div>{content ?? <p role="status">{loading ? "正在读取详情…" : "内容已变化，当前工作稿或对话中已找不到该内容。请返回。"}</p>}</div>
  </section>;
}
