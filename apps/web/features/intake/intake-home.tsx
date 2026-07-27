"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import {
  CaseSpine,
  DocumentHeader,
  PanelHeader,
  StatusBadge,
} from "@/components/prototype-ui";
import { usePrototype } from "@/store/prototype-store";

import motionStyles from "./intake-motion.module.css";
import styles from "./intake.module.css";

const intakeRoutes = [
  {
    code: "A",
    title: "我有一个想法",
    detail: "从一句创意整理为 Brief",
    enabled: true,
  },
  {
    code: "B",
    title: "帮我想一个",
    detail: "比较三个创意候选",
    enabled: false,
  },
  {
    code: "C",
    title: "整理已有内容",
    detail: "抽取实体、事件与信息",
    enabled: false,
  },
  {
    code: "D",
    title: "专业模板起稿",
    detail: "从结构化空白卷宗开始",
    enabled: false,
  },
];

const INTAKE_OPENING_MOTION_KEY = "casefile:intake-opening-motion:v1";

export function claimIntakeOpeningMotion(storage: {
  getItem: (key: string) => string | null;
  setItem: (key: string, value: string) => void;
}) {
  if (storage.getItem(INTAKE_OPENING_MOTION_KEY)) return false;

  storage.setItem(INTAKE_OPENING_MOTION_KEY, "played");
  return true;
}

export function IntakeHome() {
  const router = useRouter();
  const { state, dispatch, ready } = usePrototype();
  const [planningNotice, setPlanningNotice] = useState<string | null>(null);
  const [isPolishing, setIsPolishing] = useState(false);
  const [openingMotion, setOpeningMotion] = useState(false);
  const openingMotionDecision = useRef<boolean | null>(null);
  const polishTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (openingMotionDecision.current === null) {
      try {
        openingMotionDecision.current = claimIntakeOpeningMotion(
          window.sessionStorage,
        );
      } catch {
        openingMotionDecision.current = true;
      }
    }

    if (!openingMotionDecision.current) return;

    const frame = window.requestAnimationFrame(() => setOpeningMotion(true));
    return () => window.cancelAnimationFrame(frame);
  }, []);

  useEffect(
    () => () => {
      if (polishTimer.current) {
        clearTimeout(polishTimer.current);
      }
    },
    [],
  );

  const suggestionIsVisible = state.idea.suggestionStatus !== "idle";
  const originalLength = state.idea.original.trim().length;

  function requestPolish() {
    if (isPolishing || originalLength === 0) return;
    setIsPolishing(true);
    setPlanningNotice(null);
    polishTimer.current = setTimeout(() => {
      dispatch({ type: "generate-suggestion" });
      setIsPolishing(false);
    }, 520);
  }

  function adoptSuggestion() {
    dispatch({ type: "adopt-suggestion" });
  }

  function rejectSuggestion() {
    dispatch({ type: "reject-suggestion" });
  }

  return (
    <main
      aria-busy={!ready}
      className={`document ${styles.homeDocument} ${
        openingMotion ? motionStyles.openingMotion : ""
      }`}
    >
      <DocumentHeader
        eyebrow="建案中心 / CASE OPENING"
        meta={[
          { label: "记录编号", value: "REC-0001" },
          { label: "数据位置", value: "LOCAL" },
          { label: "保存状态", value: ready ? "已保存" : "读取中" },
        ]}
        title="CF-NEW : ORBIT_07"
      />

      <CaseSpine current="idea" />

      <div className={styles.homeGrid}>
        <aside className={styles.ledgerColumn}>
          <section className={`paper-panel ${styles.intakeLedger}`}>
            <PanelHeader
              code="INTAKE / 01"
              title="建案入口"
              trailing={<StatusBadge tone="red">当前路径</StatusBadge>}
            />
            <div className={styles.routeList}>
              {intakeRoutes.map((route) => (
                <button
                  aria-label={
                    route.enabled
                      ? `选择${route.title}`
                      : `查看${route.title}的规划说明`
                  }
                  className={`${styles.routeRow} ${
                    route.enabled ? styles.routeRowActive : ""
                  }`}
                  key={route.code}
                  onClick={() =>
                    route.enabled
                      ? setPlanningNotice("当前正在使用“我有一个想法”路径。")
                      : setPlanningNotice(
                          `${route.title}已进入产品地图，本轮原型暂不展开。`,
                        )
                  }
                  type="button"
                >
                  <span className={styles.routeCode}>{route.code}</span>
                  <span>
                    <strong>{route.title}</strong>
                    <small>{route.detail}</small>
                  </span>
                  <em>{route.enabled ? "已选" : "规划"}</em>
                </button>
              ))}
            </div>
          </section>

          <section className={`paper-panel ${styles.agentLedger}`}>
            <PanelHeader
              code="AGENT / PATCH"
              title="Agent 审阅记录"
              trailing={
                <StatusBadge
                  tone={suggestionIsVisible ? "red" : "neutral"}
                >
                  {suggestionIsVisible ? "候选待审" : "等待发起"}
                </StatusBadge>
              }
            />
            <div className={styles.agentLog} aria-live="polite">
              <p>
                <b>&gt;</b> 原始创意始终保留为作者记录。
              </p>
              <p>
                <b>&gt;</b> Agent 只能生成独立候选，不会覆盖原文。
              </p>
              <p>
                <b>&gt;</b> 采纳后仅更新工作稿与 Brief 来源。
              </p>
              <p className={styles.cursorLine}>
                <b>&gt;</b>{" "}
                {isPolishing
                  ? "正在整理叙事钩子与推理目标"
                  : suggestionIsVisible
                    ? "已生成 01 个候选，等待人工决定。"
                    : "等待操作员发起润色_"}
                {!isPolishing && !suggestionIsVisible ? <i /> : null}
              </p>
            </div>
            <button
              aria-label="生成一个不会覆盖原文的 Agent 润色候选"
              className={styles.agentTrigger}
              disabled={isPolishing || originalLength === 0}
              onClick={requestPolish}
              type="button"
            >
              <span>{isPolishing ? "正在生成…" : "生成润色候选"}</span>
              <b>AGENT PATCH →</b>
            </button>
          </section>

          <section className={`paper-panel ${styles.recentCase}`}>
            <PanelHeader
              code={state.project.projectId}
              title="最近卷宗"
              trailing={<StatusBadge tone="warning">1 个阻断</StatusBadge>}
            />
            <div className={styles.recentCaseBody}>
              <span className={styles.miniOrbit} aria-hidden="true" />
              <span>
                <strong>{state.project.displayName}</strong>
                <small>编辑草稿 · {state.project.version}</small>
              </span>
              <b>68%</b>
            </div>
            <Link
              aria-label={`打开${state.project.displayName}工作台`}
              className={styles.openRecent}
              href="/workbench"
            >
              <span>继续上次编辑</span>
              <b>打开工作台 ↗</b>
            </Link>
          </section>
        </aside>

        <section className={styles.blueprintStage} aria-label="创意建案工作区">
          <div className={styles.blueprint} aria-hidden="true">
            <span className={styles.orbitOne} />
            <span className={styles.orbitTwo} />
            <span className={styles.stationAxis} />
            <span className={styles.stationCore}>07</span>
            <i className={styles.crosshairOne} />
            <i className={styles.crosshairTwo} />
          </div>
          <span className={styles.coordinateTop}>
            GRID 07-A / SCALE 1:240
          </span>
          <span className={styles.coordinateSide}>
            ORBITAL RESEARCH STATION / MEMORY LOOP
          </span>

          <div className={styles.heroCopy}>
            <span>DOCUMENT PURPOSE / 001</span>
            <h2>
              把一句念头，
              <br />
              立成一份卷宗。
            </h2>
            <p>所有 Agent 补全内容都以可审阅候选存在，决定权始终属于作者。</p>
          </div>

          <div className={styles.recordComparison}>
            <section
              className={`${styles.ideaRecord} ${motionStyles.ideaPaper}`}
            >
              <header>
                <span>原始创意 / AUTHOR SOURCE</span>
                <StatusBadge tone="dark">人工原稿</StatusBadge>
              </header>
              <label htmlFor="casefile-original-idea">作者输入</label>
              <textarea
                aria-label="原始创意内容"
                id="casefile-original-idea"
                onChange={(event) =>
                  dispatch({
                    type: "set-idea-original",
                    value: event.target.value,
                  })
                }
                rows={4}
                value={state.idea.original}
              />
              <footer>
                <span>字符：{originalLength}</span>
                <span>来源：人工输入</span>
                <span>记录：永久保留</span>
              </footer>
              <div
                className={`${styles.recordActions} ${motionStyles.recordActions}`}
              >
                <button
                  aria-label="让 Agent 润色当前原始创意"
                  disabled={isPolishing || originalLength === 0}
                  onClick={requestPolish}
                  type="button"
                >
                  A / Agent 润色
                </button>
                <button
                  aria-label="使用当前内容进入 Brief 确认页"
                  disabled={originalLength === 0}
                  onClick={() => router.push("/brief")}
                  type="button"
                >
                  <span>进入 Brief</span>
                  <b>→</b>
                </button>
              </div>
            </section>

            <section
              aria-live="polite"
              className={`${styles.suggestionRecord} ${
                suggestionIsVisible ? styles.suggestionVisible : ""
              }`}
            >
              <header>
                <span>润色提案 / AGENT CANDIDATE</span>
                <StatusBadge
                  tone={
                    state.idea.suggestionStatus === "adopted"
                      ? "dark"
                      : state.idea.suggestionStatus === "rejected"
                        ? "neutral"
                        : "red"
                  }
                >
                  {state.idea.suggestionStatus === "adopted"
                    ? "已采纳"
                    : state.idea.suggestionStatus === "rejected"
                      ? "已拒绝"
                      : suggestionIsVisible
                        ? "待审阅"
                        : "尚未生成"}
                </StatusBadge>
              </header>
              {suggestionIsVisible ? (
                <>
                  <p>{state.idea.suggestion}</p>
                  <div className={styles.candidateNote}>
                    <b>补全内容</b>
                    <span>前六次失败 / 四名玩家 / 保护协议</span>
                  </div>
                  <footer>
                    <button
                      aria-label="拒绝 Agent 润色提案并保留原文"
                      onClick={rejectSuggestion}
                      type="button"
                    >
                      拒绝
                    </button>
                    <button
                      aria-label="采纳 Agent 润色提案到工作稿，不覆盖原文"
                      onClick={adoptSuggestion}
                      type="button"
                    >
                      采纳到工作稿 →
                    </button>
                  </footer>
                </>
              ) : (
                <div className={styles.emptyCandidate}>
                  <span aria-hidden="true">＋</span>
                  <strong>尚无 Agent 候选</strong>
                  <p>点击“Agent 润色”后，候选会在这里与原稿并列出现。</p>
                </div>
              )}
            </section>
          </div>
        </section>
      </div>

      <footer className={styles.documentFooter}>
        <span>
          <b>原型状态：</b>建案黄金路径可用
        </span>
        <span>自动保存：开启</span>
        <span className={styles.footerNotice} role="status">
          {planningNotice ??
            (state.idea.suggestionStatus === "adopted"
              ? "已采纳候选；作者原稿保持不变。"
              : "LOCAL-FIRST / AUTHOR IN CONTROL")}
        </span>
        <span>CASEFILE / PROTOTYPE-V1</span>
      </footer>
    </main>
  );
}
