import type { CaseHistoryEntry } from "./case-history-drawer";
import { Glyph } from "./intake-glyph";
import { intakeRoutes } from "./intake-model";
import styles from "./intake-center.module.css";

const landingActions = {
  A: "开始记录",
  B: "生成方向",
  C: "导入内容",
} as const;

export function IntakeLanding({
  hasRetainedCase,
  historyEntries,
  historyLoading,
  onOpenHistory,
  onOpenRoute,
  onRestore,
}: {
  hasRetainedCase: boolean;
  historyEntries: CaseHistoryEntry[] | null;
  historyLoading: boolean;
  onOpenHistory: () => void;
  onOpenRoute: (code: "A" | "B" | "C") => void;
  onRestore: (projectId: number) => void;
}) {
  const recentEntries = (historyEntries ?? [])
    .filter((entry) => entry.status !== "archived")
    .slice(0, 3);

  return (
    <main className={styles.landing}>
      <header className={styles.landingHero}>
        <span>CASE INTAKE / 故事从此落笔</span>
        <h1>你的故事，想从哪里开始？</h1>
        <p>不必急着抵达答案。选一个最贴近此刻的入口，走过的线索都会替你留在案卷里。</p>
        {hasRetainedCase ? (
          <div className={styles.retainedCaseNotice} role="status">
            <strong>已保留</strong>
            原建案仍在档案柜中，现在可以建立新的方向。
          </div>
        ) : null}
      </header>

      <section aria-label="选择建案方式" className={styles.landingRoutes}>
        {intakeRoutes
          .filter((route): route is (typeof intakeRoutes)[number] & { code: "A" | "B" | "C" } =>
            route.state === "available",
          )
          .map((route, index) => (
            <button
              className={styles.landingRouteCard}
              key={route.code}
              onClick={() => onOpenRoute(route.code)}
              type="button"
            >
              <span className={styles.landingRouteNumber}>0{index + 1}</span>
              <i aria-hidden="true" className={styles.landingRouteTarget} />
              <strong>{route.label}</strong>
              <p>{route.summary}</p>
              <span className={styles.landingRouteAction}>
                {landingActions[route.code]} <b aria-hidden="true">→</b>
              </span>
            </button>
          ))}
      </section>

      <section className={styles.recentCases}>
        <header>
          <h2>最近建案</h2>
          <button onClick={onOpenHistory} type="button">查看全部 <span aria-hidden="true">→</span></button>
        </header>
        {historyLoading ? (
          <p className={styles.recentCasesEmpty}>正在翻阅档案柜…</p>
        ) : recentEntries.length > 0 ? (
          <div className={styles.recentCaseGrid}>
            {recentEntries.map((entry) => (
              <button
                className={styles.recentCase}
                key={entry.id}
                onClick={() => onRestore(entry.id)}
                type="button"
              >
                <Glyph name="archive" />
                <span><strong>{entry.title}</strong><small>{entry.stageLabel}</small></span>
                <time>{entry.touchedLabel}</time>
                <b aria-hidden="true">›</b>
              </button>
            ))}
          </div>
        ) : (
          <p className={styles.recentCasesEmpty}>档案柜还是空的。选择一种方式，建立第一份卷宗。</p>
        )}
      </section>
    </main>
  );
}
