import type { NovelEditorialReview } from "@casefile/contracts";
import styles from "./novel-collaboration.module.css";

export function NovelEditorialSummary({ review }: { review: NovelEditorialReview }) {
  const last = review.reports.at(-1);
  const current = last?.candidate_hash === review.candidate_hash ? last.review : null;
  const categories = { intent: "改写要求", preservation: "保留项", meaning: "含义变化", continuity: "前后衔接" };
  return <section className={styles.editorialSummary} aria-label="编辑审阅结果">
    <strong>{review.status === "revision_failed" ? "自动修订未完成"
      : review.status !== "completed" ? "编辑核对尚未完成"
      : current?.action === "accept" ? "编辑已核对候选" : "仍有事项需要你判断"}</strong>
    <p>{review.message}</p>
    {review.revision_count ? <p>已进行 {review.revision_count} 次自动修订。</p> : null}
    {review.stages?.length ? <details open>
      <summary>查看编辑流程与逐项结果</summary>
      {review.stages.map((stage, index) => <details key={index}>
        <summary>{stage.label} · {stage.status === "completed" ? "已完成" : "未完成"}</summary>
        <p>{stage.summary}</p>
        {stage.findings.map((finding, item) => <p key={item}>{finding}</p>)}
      </details>)}
    </details> : null}
    {current ? <>
      <p>{current.summary}</p>
      {current.findings.map((finding, i) => <details key={i}>
        <summary>{categories[finding.category]} · {finding.severity === "major" ? "重要问题"
          : finding.severity === "warning" ? "需留意" : "说明"}：{finding.message}</summary>
        {finding.source_quote ? <p>原章依据：<q>{finding.source_quote}</q></p> : null}
        {finding.candidate_quote ? <p>候选依据：<q>{finding.candidate_quote}</q></p> : null}
        {finding.suggestion ? <p>建议：{finding.suggestion}</p> : null}
      </details>)}
    </> : null}
    {review.reports.length > (current ? 1 : 0) ? <details>
      <summary>查看修订前的审阅记录</summary>
      {review.reports.slice(0, current ? -1 : undefined).map((report) => <p key={report.round}>{report.review.summary}</p>)}
    </details> : null}
  </section>;
}
