import type { NovelPlanPreview } from "./novel-compiler-api";
import styles from "./novel-compiler.module.css";

const purposes: Record<string, string> = { hook: "悬念开场", setup: "铺垫", investigation: "调查",
  discovery: "发现线索", reversal: "转折", confrontation: "对峙", reveal: "揭晓", false_resolution: "假解答",
  climax: "高潮", resolution: "收束", transition: "衔接" };

export function NovelPlanOutline({ preview }: { preview: NovelPlanPreview }) {
  const { plan, names } = preview;
  const label = (ref: Record<string, unknown> | null) => ref && typeof ref.object_id === "string" ? names[ref.object_id] : undefined;
  return <section className={styles.outline} aria-label="章节与场景方案">
    <div className={styles.planSummary}><strong>{plan.chapters.length} 章 · {plan.scenes.length} 个场景</strong>
      <span>预计 {(plan.scenes.length * 300).toLocaleString()}–{(plan.scenes.length * 1200).toLocaleString()} 字</span></div>
    <p>场景是一段连续的故事行动。展开可查看谁在场、在哪里、发生什么，以及它在小说中的作用。</p>
    {[...plan.chapters].sort((a, b) => a.ordinal - b.ordinal).map((chapter) => <section key={chapter.chapter_id} className={styles.chapter}>
      <h4><span>第 {chapter.ordinal} 章</span>{chapter.title}</h4>
      {[...plan.scenes].filter((scene) => scene.chapter_id === chapter.chapter_id).sort((a, b) => a.discourse_order - b.discourse_order).map((scene) => {
        const people = scene.participant_refs.map(label).filter(Boolean).join("、");
        const events = scene.event_refs.map(label).filter(Boolean);
        return <details key={scene.scene_id} className={styles.scene} open>
          <summary><span>场景 {scene.discourse_order}</span><strong>{scene.intent}</strong><small>{purposes[scene.purpose] ?? "推进故事"}</small></summary>
          <dl><div><dt>地点</dt><dd>{label(scene.location_ref) ?? "未指定固定地点"}</dd></div>
            <div><dt>人物</dt><dd>{people || "未指定出场人物"}</dd></div>
            <div><dt>视角</dt><dd>{label(scene.pov_ref) ?? "未指定视角人物"}</dd></div>
            <div><dt>发生什么</dt><dd>{events.length ? events.join("；") : scene.intent}</dd></div>
            <div><dt>叙事作用</dt><dd>{purposes[scene.purpose] ?? "推进故事"}。{scene.intent}</dd></div>
          </dl>
        </details>;
      })}
    </section>)}
  </section>;
}
