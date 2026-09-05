import { CompileTargetIcon, type CompileTarget } from "./compile-target-icon";
import styles from "./compile-center.module.css";

const targets: Array<{
  id: CompileTarget;
  label: string;
  description: string;
}> = [
  { id: "novel", label: "小说", description: "章节叙事，与 AI 一起打磨全文" },
  { id: "script", label: "剧本", description: "角色手册、场景与线索卡" },
  {
    id: "interactive",
    label: "互动脚本",
    description: "分支选择、任务与角色对话",
  },
  {
    id: "dossier",
    label: "作者卷宗",
    description: "故事设定、时间线与创作笔记",
  },
  {
    id: "test",
    label: "测试材料",
    description: "推理验证、检查清单与测试用例",
  },
];

export function CompileCenterView({
  title,
  onOpenNovel,
}: {
  title: string;
  onOpenNovel: () => void;
}) {
  return (
    <section className={styles.center} aria-labelledby="compile-heading">
      <header className={styles.heading}>
        <span className={styles.project}>《{title}》</span>
        <h1 id="compile-heading">选择作品形式</h1>
        <p>从同一份卷宗出发，进入不同的创作空间。</p>
      </header>
      <div className={styles.entries} aria-label="作品入口">
        {targets.map((target) => (
          <button
            className={styles.entry}
            data-target={target.id}
            key={target.id}
            disabled={target.id !== "novel"}
            onClick={target.id === "novel" ? onOpenNovel : undefined}
            type="button"
          >
            <span className={styles.illustration}>
              <CompileTargetIcon target={target.id} />
            </span>
            <strong>{target.label}</strong>
            <span className={styles.description}>{target.description}</span>
            <span className={styles.action}>
              {target.id === "novel" ? (
                <>
                  进入工作台 <span aria-hidden="true">↗</span>
                </>
              ) : (
                "即将开放"
              )}
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}
