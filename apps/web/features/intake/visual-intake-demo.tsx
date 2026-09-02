"use client";

import { type CSSProperties, useEffect, useMemo, useRef, useState } from "react";

import {
  ideaAtmosphereOptions,
  conclusionModeLabels,
  ideaEraOptions,
  ideaSettingOptions,
  polishIdea,
  polishModes,
  reasoningTypeLabels,
  type IntakePolishMode,
} from "./intake-model";
import ideaStageStyles from "./idea-candidates-stage.module.css";
import styles from "./visual-intake-demo.module.css";

type RouteCode = "A" | "B" | "C";
type Scene = "home" | "start" | "questions" | "confirmation" | "frozen";
type DialogKind = "revise" | "restart";
type ConfirmationPhase = "review" | "processing" | "completed";
type ConclusionMode = "open" | "unique" | "agent";
type ScopeMode = "focused" | "ensemble" | "expansive";
type IdeaPreferenceGroup = "eras" | "settings" | "atmospheres";
type IdeaPreferences = {
  eras: string[];
  settings: string[];
  atmospheres: string[];
  keywords: string;
};

const EMPTY_IDEA_PREFERENCES: IdeaPreferences = {
  eras: [],
  settings: [],
  atmospheres: [],
  keywords: "",
};

const DEFAULT_SOURCE =
  "一名档案修复师发现，三份彼此独立且可靠的记录，都指向一段从未存在过的时间。";

const routes: Array<{
  code: RouteCode;
  title: string;
  summary: string;
  action: string;
  detail: string;
}> = [
  {
    code: "A",
    title: "我有一个想法",
    summary: "把脑海里的想法整理成可推演的创作方向",
    action: "开始记录",
    detail: "一句话也可以。CaseFile 会先保留原文，再询问真正影响方向的问题。",
  },
  {
    code: "B",
    title: "帮我想一个",
    summary: "从几个方向开始，找到值得展开的故事",
    action: "生成方向",
    detail: "先选择时代、场所与气质，再从三个互不重复的方向中挑一个。",
  },
  {
    code: "C",
    title: "我有已有内容",
    summary: "从现有稿件、设定或资料中提取案件",
    action: "导入内容",
    detail: "原文和提取结果会分开保存；任何 Agent 补充都会明确标注。",
  },
];

const recentCases = [
  { title: "雪夜失踪", meta: "追问中", time: "2 小时前" },
  { title: "档案修复师", meta: "Brief V1", time: "昨天" },
  { title: "无名幸存者", meta: "起案草稿", time: "8 月 28 日" },
];

const generatedIdeas = [
  {
    id: "signal",
    ordinal: 1,
    body: "每位乘客都记得自己下过车，但车站从未出现在任何线路图上。",
    content: {
      concept: "最后一班不存在的列车",
      core_suspense: "每位乘客都记得自己下过车，但车站从未出现在任何线路图上。",
      reasoning_type: "abductive",
      conclusion_mode: "agent_proposed",
      target_experience: "从互相印证的乘车记忆中，逐步发现一座被集体遗忘的车站。",
      design_risk: "群体证词容易趋同，需要为每位乘客保留可核对的独立偏差。",
      scale_estimate: "6—8 位关键人物 · 12—16 条核心线索",
    },
  },
  {
    id: "archive",
    ordinal: 2,
    body: "城市的全部监控在同一分钟里，留下了十三分钟互相矛盾的记录。",
    content: {
      concept: "被删除的第十三分钟",
      core_suspense: "城市的全部监控在同一分钟里，留下了十三分钟互相矛盾的记录。",
      reasoning_type: "hybrid",
      conclusion_mode: "author_anchored",
      target_experience: "像修复损坏档案一样拼合时间缺口，辨认哪一份记录被人为改写。",
      design_risk: "时间线密度较高，需要避免技术设定掩盖人物动机。",
      scale_estimate: "4—6 位关键人物 · 3 组互证时间线",
    },
  },
  {
    id: "island",
    ordinal: 3,
    body: "两份笔迹相同的遗嘱，分别要求在涨潮和退潮时公布真相。",
    content: {
      concept: "潮汐带回了第二份遗嘱",
      core_suspense: "两份笔迹相同的遗嘱，分别要求在涨潮和退潮时公布真相。",
      reasoning_type: "deductive",
      conclusion_mode: "open",
      target_experience: "在潮汐限定的封闭时段中验证遗嘱来源，并决定哪一种真相可以成立。",
      design_risk: "双遗嘱结构必须让两套解释都拥有充分而不重复的证据。",
      scale_estimate: "5 位关键人物 · 单地点 · 24 小时",
    },
  },
];

function BrandMark() {
  return (
    <span aria-hidden="true" className={styles.brandMark}>
      <i />
      <b>CF</b>
    </span>
  );
}

function ArrowIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24">
      <path d="M5 12h13M13 6l6 6-6 6" />
    </svg>
  );
}

function FileIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24">
      <path d="M7 3.5h7l4 4V21H7zM14 3.5V8h4M10 12h5M10 15h5" />
    </svg>
  );
}

function routeLabel(route: RouteCode | null) {
  return routes.find((item) => item.code === route)?.title ?? "";
}

function scopeModeLabel(mode: ScopeMode) {
  if (mode === "ensemble") return "多人物并行";
  if (mode === "expansive") return "开放世界延展";
  return "单一核心冲突";
}

export function VisualIntakeDemo() {
  const [scene, setScene] = useState<Scene>("home");
  const [activeRoute, setActiveRoute] = useState<RouteCode | null>(null);
  const [sourceText, setSourceText] = useState(DEFAULT_SOURCE);
  const [selectedIdea, setSelectedIdea] = useState("archive");
  const [ideaPreferences, setIdeaPreferences] = useState<IdeaPreferences>(
    EMPTY_IDEA_PREFERENCES,
  );
  const [ideasGenerated, setIdeasGenerated] = useState(false);
  const [importReady, setImportReady] = useState(true);
  const [answer, setAnswer] = useState(
    "找出是谁制造了那段不存在的时间，以及三份可靠记录为什么会同时失真。",
  );
  const [briefAnswer, setBriefAnswer] = useState(answer);
  const [conclusionMode, setConclusionMode] = useState<ConclusionMode>("open");
  const [scopeMode, setScopeMode] = useState<ScopeMode>("focused");
  const [questionIndex, setQuestionIndex] = useState(0);
  const [questionNotes, setQuestionNotes] = useState(["", "", ""]);
  const [confirmationPhase, setConfirmationPhase] = useState<ConfirmationPhase>("review");
  const [workingVersion, setWorkingVersion] = useState(1);
  const [frozenVersions, setFrozenVersions] = useState<number[]>([]);
  const [dialog, setDialog] = useState<DialogKind | null>(null);
  const [retainedVersion, setRetainedVersion] = useState<number | null>(null);
  const [notice, setNotice] = useState("");
  const dialogPrimaryRef = useRef<HTMLButtonElement>(null);
  const reviseButtonRef = useRef<HTMLButtonElement>(null);
  const restartButtonRef = useRef<HTMLButtonElement>(null);
  const demoRef = useRef<HTMLDivElement>(null);

  const briefStale = scene === "confirmation" && answer.trim() !== briefAnswer.trim();
  const activeRouteDefinition = routes.find((item) => item.code === activeRoute) ?? null;
  const routeSource = useMemo(() => {
    if (activeRoute === "B") {
      return generatedIdeas.find((idea) => idea.id === selectedIdea)?.body ?? DEFAULT_SOURCE;
    }
    if (activeRoute === "C") {
      return "现有稿件《雪夜来信》：十年前火灾当晚，幸存者、值班员与报社底片留下了互相冲突的时间。";
    }
    return sourceText.trim() || DEFAULT_SOURCE;
  }, [activeRoute, selectedIdea, sourceText]);

  useEffect(() => {
    if (!dialog) return;
    dialogPrimaryRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setDialog(null);
      window.setTimeout(() => {
        (dialog === "revise" ? reviseButtonRef : restartButtonRef).current?.focus();
      }, 0);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [dialog]);

  useEffect(() => {
    const demo = demoRef.current;
    if (!demo) return;
    if (typeof demo.scrollTo === "function") {
      demo.scrollTo({ top: 0, left: 0, behavior: "auto" });
    }
    demo.querySelector<HTMLElement>("main h1")?.focus();
  }, [activeRoute, scene, workingVersion]);

  useEffect(() => {
    if (scene !== "confirmation" || confirmationPhase === "review") return;
    const timer = window.setTimeout(() => {
      if (confirmationPhase === "processing") {
        setConfirmationPhase("completed");
        setNotice("建案内容与创作边界已经整理完成。");
        return;
      }
      setFrozenVersions((versions) =>
        versions.includes(workingVersion) ? versions : [...versions, workingVersion],
      );
      setScene("frozen");
      setConfirmationPhase("review");
      setNotice(`Brief V${workingVersion} 已确认并冻结。`);
    }, confirmationPhase === "processing" ? 700 : 650);
    return () => window.clearTimeout(timer);
  }, [confirmationPhase, scene, workingVersion]);

  function resetDemo() {
    setScene("home");
    setActiveRoute(null);
    setSourceText(DEFAULT_SOURCE);
    setSelectedIdea("archive");
    setIdeaPreferences(EMPTY_IDEA_PREFERENCES);
    setIdeasGenerated(false);
    setImportReady(true);
    const initialAnswer =
      "找出是谁制造了那段不存在的时间，以及三份可靠记录为什么会同时失真。";
    setAnswer(initialAnswer);
    setBriefAnswer(initialAnswer);
    setConclusionMode("open");
    setScopeMode("focused");
    setQuestionIndex(0);
    setQuestionNotes(["", "", ""]);
    setConfirmationPhase("review");
    setWorkingVersion(1);
    setFrozenVersions([]);
    setDialog(null);
    setRetainedVersion(null);
    setNotice("演示已重置。");
  }

  function openRoute(code: RouteCode) {
    if (frozenVersions.length > 0 && retainedVersion === null) {
      setDialog("restart");
      setNotice("当前 Brief 已冻结；更换起案方式前，需要先保留旧版并重新起案。");
      return;
    }
    setActiveRoute(code);
    setScene("start");
    setNotice(`已选择“${routeLabel(code)}”。`);
  }

  function continueToQuestions() {
    if (activeRoute === "A" && !sourceText.trim()) return;
    if (activeRoute === "B" && !ideasGenerated) return;
    if (activeRoute === "C" && !importReady) return;
    setQuestionIndex(0);
    setScene("questions");
    setNotice("起案内容已保留，可以继续回答关键问题。");
  }

  function openConfirmation() {
    setConfirmationPhase("review");
    setScene("confirmation");
    setNotice(
      answer.trim() === briefAnswer.trim()
        ? "已进入建案确认。"
        : "回答发生变化，原 Brief 已保留并标记为需要更新。",
    );
  }

  function refreshBrief() {
    setBriefAnswer(answer);
    setNotice("Brief 已依据新的回答重新整理，原内容没有被删除。");
  }

  function freezeBrief() {
    setConfirmationPhase("processing");
    setNotice("正在确认建案。");
  }

  function beginRevision() {
    const nextVersion = Math.max(...frozenVersions, workingVersion) + 1;
    setWorkingVersion(nextVersion);
    setBriefAnswer(answer);
    setDialog(null);
    setConfirmationPhase("review");
    setScene("confirmation");
    setNotice(`已从 V${nextVersion - 1} 创建可编辑的 V${nextVersion}。`);
  }

  function restartLineage() {
    const latestFrozen = Math.max(...frozenVersions, workingVersion);
    setRetainedVersion(latestFrozen);
    setDialog(null);
    setScene("home");
    setActiveRoute(null);
    setWorkingVersion(1);
    setFrozenVersions([]);
    setNotice(`原建案 V${latestFrozen} 已保留，请选择新的起案方式。`);
  }

  function closeDialog() {
    const kind = dialog;
    setDialog(null);
    window.setTimeout(() => {
      (kind === "revise" ? reviseButtonRef : restartButtonRef).current?.focus();
    }, 0);
  }

  return (
    <div className={styles.demo} data-scene={scene} ref={demoRef}>
      {scene === "home" ? (
        <header className={styles.topbar}>
          <button
            aria-label="返回视觉 Demo 首页"
            className={styles.brand}
            onClick={() => setScene("home")}
            type="button"
          >
            <BrandMark />
            <span>
              <strong>CaseFile</strong>
              <small>建案中心</small>
            </span>
          </button>
          <nav aria-label="Demo 页面导航" className={styles.topNav}>
            <span aria-current="page">建案</span>
            <span>工作台</span>
            <button onClick={resetDemo} type="button">重置演示</button>
            <i aria-hidden="true">A</i>
          </nav>
        </header>
      ) : null}

      {scene === "home" ? (
        <HomeScene
          notice={notice}
          onOpenRoute={openRoute}
          retainedVersion={retainedVersion}
        />
      ) : (
        <div className={`${styles.flowShell} ${scene === "start" ? styles.startFlowShell : ""}`}>
          <DossierSpine
            briefStale={briefStale}
            frozen={scene === "frozen"}
            onHome={() => setScene("home")}
            onNavigate={(target) => {
              setConfirmationPhase("review");
              if (target === "source") setScene("start");
              if (target === "decisions") setScene("questions");
              if (target === "brief") setScene("confirmation");
            }}
            onReset={resetDemo}
            scene={scene}
          />
          <div
            aria-live="polite"
            className={scene === "start" && activeRoute === "A" ? styles.srOnly : styles.notice}
            role="status"
          >{notice}</div>
          {scene === "start" && activeRouteDefinition ? (
            <StartScene
              activeRoute={activeRoute}
              activeRouteDefinition={activeRouteDefinition}
              importReady={importReady}
              ideaPreferences={ideaPreferences}
              ideasGenerated={ideasGenerated}
              onBack={() => setScene("home")}
              onContinue={continueToQuestions}
              onGenerateIdeas={() => {
                setIdeasGenerated(true);
                setNotice("已依据当前偏好生成 3 个创意方向。");
              }}
              onImportReady={setImportReady}
              onKeywordChange={(keywords) =>
                setIdeaPreferences((current) => ({ ...current, keywords }))
              }
              onPreferenceToggle={(group, value) =>
                setIdeaPreferences((current) => ({
                  ...current,
                  [group]: current[group].includes(value)
                    ? current[group].filter((item) => item !== value)
                    : [...current[group], value],
                }))
              }
              onNotice={setNotice}
              onSelectIdea={setSelectedIdea}
              onSourceChange={setSourceText}
              selectedIdea={selectedIdea}
              sourceText={sourceText}
            />
          ) : null}
          {scene === "questions" ? (
            <QuestionsScene
              answer={answer}
              conclusionMode={conclusionMode}
              onAnswerChange={setAnswer}
              onConclusionModeChange={setConclusionMode}
              onContinue={openConfirmation}
              onNoteChange={(index, value) =>
                setQuestionNotes((current) =>
                  current.map((note, noteIndex) => noteIndex === index ? value : note),
                )
              }
              onPrevious={() => {
                if (questionIndex === 0) setScene("start");
                else setQuestionIndex((index) => index - 1);
              }}
              onScopeModeChange={setScopeMode}
              onStepChange={setQuestionIndex}
              questionIndex={questionIndex}
              questionNotes={questionNotes}
              routeSource={routeSource}
              scopeMode={scopeMode}
            />
          ) : null}
          {scene === "confirmation" ? (
            <ConfirmationScene
              answer={answer}
              briefAnswer={briefAnswer}
              confirmationPhase={confirmationPhase}
              conclusionMode={conclusionMode}
              frozenVersions={frozenVersions}
              onBack={() => {
                setQuestionIndex(2);
                setScene("questions");
              }}
              onConclusionModeChange={setConclusionMode}
              onEditQuestion={() => {
                setQuestionIndex(1);
                setScene("questions");
              }}
              onEditSource={() => setScene("start")}
              onFreeze={freezeBrief}
              onRefresh={refreshBrief}
              routeLabel={routeLabel(activeRoute)}
              routeSource={routeSource}
              scopeMode={scopeMode}
              stale={briefStale}
              workingVersion={workingVersion}
            />
          ) : null}
          {scene === "frozen" ? (
            <FrozenScene
              answer={briefAnswer}
              frozenVersions={frozenVersions}
              onOpenRestart={() => setDialog("restart")}
              onOpenRevision={() => setDialog("revise")}
              restartButtonRef={restartButtonRef}
              reviseButtonRef={reviseButtonRef}
              routeSource={routeSource}
              workingVersion={workingVersion}
            />
          ) : null}
        </div>
      )}

      {dialog ? (
        <div className={styles.dialogBackdrop} role="presentation">
          <section
            aria-describedby="version-dialog-description"
            aria-labelledby="version-dialog-title"
            aria-modal="true"
            className={styles.dialog}
            role="dialog"
          >
            <span className={styles.dialogIndex}>REVISION / {dialog === "revise" ? "02" : "NEW"}</span>
            <h2 id="version-dialog-title">
              {dialog === "revise" ? "创建建案修订" : "从新的方向重新起案？"}
            </h2>
            <p id="version-dialog-description">
              {dialog === "revise"
                ? `当前 V${workingVersion} 会继续保留。CaseFile 将复制它，创建一个可编辑的新版本。`
                : `当前 Brief V${workingVersion} 和版本历史不会丢失。新的起案方式会建立另一条建案线。`}
            </p>
            <div className={styles.dialogVersionPreview}>
              <span>Brief V{workingVersion}</span>
              <i aria-hidden="true" />
              <strong>{dialog === "revise" ? `Brief V${workingVersion + 1} · 编辑中` : "选择新的起点"}</strong>
            </div>
            <footer>
              <button className={styles.textButton} onClick={closeDialog} type="button">取消</button>
              <button
                className={styles.primaryButton}
                onClick={dialog === "revise" ? beginRevision : restartLineage}
                ref={dialogPrimaryRef}
                type="button"
              >
                {dialog === "revise" ? `创建 V${workingVersion + 1}` : "保留旧版并重新起案"}
                <ArrowIcon />
              </button>
            </footer>
          </section>
        </div>
      ) : null}
    </div>
  );
}

function HomeScene({
  notice,
  onOpenRoute,
  retainedVersion,
}: {
  notice: string;
  onOpenRoute: (code: RouteCode) => void;
  retainedVersion: number | null;
}) {
  return (
    <main className={styles.home}>
      <section className={styles.homeHero}>
        <p className={styles.eyebrow}>CASE INTAKE / 建立创作依据</p>
        <h1 tabIndex={-1}>从哪里开始？</h1>
        <p>选择最接近你当前状态的起点。之后随时可以返回，已有内容不会消失。</p>
        {retainedVersion ? (
          <div className={styles.retainedNotice} role="status">
            <span>已归档</span>
            原建案 Brief V{retainedVersion} 已保留，现在可以建立新的方向。
          </div>
        ) : notice ? (
          <div aria-live="polite" className={styles.srOnly} role="status">{notice}</div>
        ) : null}
      </section>

      <section aria-label="起案方式" className={styles.routeGrid}>
        {routes.map((route, index) => (
          <button
            className={styles.routeCard}
            key={route.code}
            onClick={() => onOpenRoute(route.code)}
            style={{ "--route-order": index } as CSSProperties}
            type="button"
          >
            <span className={styles.routeNumber}>0{index + 1}</span>
            <div className={styles.registrationMark} aria-hidden="true"><i /></div>
            <h2>{route.title}</h2>
            <p>{route.summary}</p>
            <span className={styles.routeAction}>{route.action}<ArrowIcon /></span>
          </button>
        ))}
      </section>

      <section className={styles.recentSection}>
        <header>
          <h2>最近建案</h2>
          <span>查看全部 <ArrowIcon /></span>
        </header>
        <div className={styles.recentGrid}>
          {recentCases.map((item) => (
            <article className={styles.recentCase} key={item.title}>
              <FileIcon />
              <span><strong>{item.title}</strong><small>{item.meta}</small></span>
              <time>{item.time}</time>
              <b aria-hidden="true">›</b>
            </article>
          ))}
        </div>
      </section>
    </main>
  );
}

function DossierSpine({
  briefStale,
  frozen,
  onHome,
  onNavigate,
  onReset,
  scene,
}: {
  briefStale: boolean;
  frozen: boolean;
  onHome: () => void;
  onNavigate: (target: "source" | "decisions" | "brief") => void;
  onReset: () => void;
  scene: Scene;
}) {
  const sceneOrder: Record<Scene, number> = {
    home: -1,
    start: 0,
    questions: 1,
    confirmation: 2,
    frozen: 3,
  };
  const current = sceneOrder[scene];
  const stages = [
    { id: "source" as const, no: "01", label: "起案", caption: "Source" },
    { id: "decisions" as const, no: "02", label: "关键追问", caption: "Decisions" },
    { id: "brief" as const, no: "03", label: frozen ? "已冻结" : "建案确认", caption: "Brief" },
  ];
  return (
    <nav
      aria-label="建案依赖进度"
      className={styles.spine}
      data-with-brand="true"
    >
      <button
        aria-label="返回视觉 Demo 首页"
        className={styles.spineBrand}
        onClick={onHome}
        type="button"
      >
        <BrandMark />
        <span><strong>CaseFile</strong><small>建案中心</small></span>
      </button>
      <ol>
        {stages.map((stage, index) => {
          const stale = stage.id === "brief" && briefStale;
          const reached = index <= current;
          const active = index === Math.min(current, 2);
          const canNavigate = !frozen && reached;
          return (
            <li
              data-active={active || undefined}
              data-complete={(index < current || frozen) && !stale || undefined}
              data-stale={stale || undefined}
              key={stage.id}
            >
              <button
                aria-current={active ? "step" : undefined}
                disabled={!canNavigate}
                onClick={() => onNavigate(stage.id)}
                type="button"
              >
                <span>{stale ? "!" : stage.no}</span>
                <div><small>{stage.caption}</small><strong>{stale ? "需要更新" : stage.label}</strong></div>
              </button>
              {index < stages.length - 1 ? <i aria-hidden="true" /> : null}
            </li>
          );
        })}
      </ol>
      <button className={styles.spineReset} onClick={onReset} type="button">
        重置演示
      </button>
    </nav>
  );
}

function SceneHeading({
  eyebrow,
  title,
  description,
  onBack,
  backLabel,
}: {
  eyebrow: string;
  title: string;
  description: string;
  onBack: () => void;
  backLabel: string;
}) {
  return (
    <header className={styles.sceneHeading}>
      <button className={styles.backButton} onClick={onBack} type="button">← {backLabel}</button>
      <span>{eyebrow}</span>
      <h1 tabIndex={-1}>{title}</h1>
      <p>{description}</p>
    </header>
  );
}

function IdeaPreferenceRow({
  group,
  label,
  onToggle,
  options,
  selected,
}: {
  group: IdeaPreferenceGroup;
  label: string;
  onToggle: (group: IdeaPreferenceGroup, value: string) => void;
  options: readonly string[];
  selected: string[];
}) {
  return (
    <div className={ideaStageStyles.prefRow}>
      <span className={ideaStageStyles.prefLabel}>{label}</span>
      <div className={ideaStageStyles.chips}>
        {options.map((option) => (
          <button
            aria-pressed={selected.includes(option)}
            className={`${ideaStageStyles.chip} ${
              selected.includes(option) ? ideaStageStyles.chipActive : ""
            }`}
            key={option}
            onClick={() => onToggle(group, option)}
            type="button"
          >
            {option}
          </button>
        ))}
      </div>
    </div>
  );
}

function DemoIdeaCard({
  historical = false,
  idea,
  onNotice,
  onSelect,
  readonly = false,
  selected,
}: {
  historical?: boolean;
  idea: (typeof generatedIdeas)[number];
  onNotice: (message: string) => void;
  onSelect: (idea: string) => void;
  readonly?: boolean;
  selected: boolean;
}) {
  const content = idea.content;

  return (
    <article
      aria-label={`${historical ? "历史" : ""}创意方向 ${idea.ordinal}：${content.concept}`}
      className={`${ideaStageStyles.card} ${selected ? styles.ideaCardSelected : ""}`}
    >
      <div className={ideaStageStyles.cardTop}>
        <span className={ideaStageStyles.ordinal}>#{idea.ordinal}</span>
      </div>
      <h3 className={ideaStageStyles.concept}>{content.concept}</h3>
      <dl className={ideaStageStyles.fields}>
        <div className={ideaStageStyles.field}>
          <dt className={ideaStageStyles.fieldLabel}>核心悬念</dt>
          <dd>{content.core_suspense}</dd>
        </div>
        <div className={ideaStageStyles.field}>
          <dt className={ideaStageStyles.fieldLabel}>推理类型</dt>
          <dd>{reasoningTypeLabels[content.reasoning_type] ?? content.reasoning_type}</dd>
        </div>
        <div className={ideaStageStyles.field}>
          <dt className={ideaStageStyles.fieldLabel}>结论模式</dt>
          <dd>{conclusionModeLabels[content.conclusion_mode] ?? content.conclusion_mode}</dd>
        </div>
        <div className={ideaStageStyles.field}>
          <dt className={ideaStageStyles.fieldLabel}>目标体验</dt>
          <dd>{content.target_experience}</dd>
        </div>
        <div className={ideaStageStyles.field}>
          <dt className={ideaStageStyles.fieldLabel}>设计风险</dt>
          <dd>{content.design_risk}</dd>
        </div>
        <div className={ideaStageStyles.field}>
          <dt className={ideaStageStyles.fieldLabel}>预计规模</dt>
          <dd>{content.scale_estimate}</dd>
        </div>
      </dl>
      {!readonly ? (
        <div className={ideaStageStyles.actions}>
          <button
            aria-label={`选择此方向：${content.concept}`}
            aria-pressed={selected}
            className={ideaStageStyles.selectBtn}
            onClick={() => onSelect(idea.id)}
            type="button"
          >
            选择此方向
          </button>
          <button
            aria-label={`重新生成：${content.concept}`}
            className={ideaStageStyles.iconBtn}
            onClick={() => onNotice(`已请求重新生成“${content.concept}”。`)}
            title="重新生成"
            type="button"
          >
            ↻
          </button>
          <button
            aria-label={`收藏：${content.concept}`}
            className={ideaStageStyles.iconBtn}
            onClick={() => onNotice(`已收藏“${content.concept}”。`)}
            title="收藏"
            type="button"
          >
            ☆
          </button>
          <button
            aria-label={`淘汰：${content.concept}`}
            className={ideaStageStyles.iconBtn}
            onClick={() => onNotice(`已标记“${content.concept}”为待淘汰方向。`)}
            title="淘汰"
            type="button"
          >
            ✕
          </button>
        </div>
      ) : null}
    </article>
  );
}

function StartScene({
  activeRoute,
  activeRouteDefinition,
  importReady,
  ideaPreferences,
  ideasGenerated,
  onBack,
  onContinue,
  onGenerateIdeas,
  onImportReady,
  onKeywordChange,
  onNotice,
  onPreferenceToggle,
  onSelectIdea,
  onSourceChange,
  selectedIdea,
  sourceText,
}: {
  activeRoute: RouteCode | null;
  activeRouteDefinition: (typeof routes)[number];
  importReady: boolean;
  ideaPreferences: IdeaPreferences;
  ideasGenerated: boolean;
  onBack: () => void;
  onContinue: () => void;
  onGenerateIdeas: () => void;
  onImportReady: (ready: boolean) => void;
  onKeywordChange: (keywords: string) => void;
  onNotice: (message: string) => void;
  onPreferenceToggle: (group: IdeaPreferenceGroup, value: string) => void;
  onSelectIdea: (idea: string) => void;
  onSourceChange: (value: string) => void;
  selectedIdea: string;
  sourceText: string;
}) {
  const [polishOpen, setPolishOpen] = useState(false);
  const [polishMode, setPolishMode] = useState<IntakePolishMode>("proofread");
  const [polishOriginal, setPolishOriginal] = useState(sourceText);
  const [polishDraft, setPolishDraft] = useState("");
  const [polishNotes, setPolishNotes] = useState<string[]>([]);
  const [introducedDetails, setIntroducedDetails] = useState<string[]>([]);
  const polishPanelRef = useRef<HTMLElement>(null);
  const polishTriggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!polishOpen) return;
    polishPanelRef.current?.querySelector<HTMLElement>("h2")?.focus();
  }, [polishOpen]);

  function generatePolish(mode: IntakePolishMode, original = polishOriginal) {
    const result = polishIdea(original, mode);
    setPolishMode(mode);
    setPolishDraft(result.text);
    setPolishNotes(result.notes);
    setIntroducedDetails(result.introducedDetails);
  }

  function openPolish() {
    if (!sourceText.trim()) return;
    setPolishOriginal(sourceText);
    generatePolish(polishMode, sourceText);
    setPolishOpen(true);
    onNotice("Agent 润色校样已展开，原文保持不变。");
  }

  function closePolish() {
    setPolishOpen(false);
    onNotice("已保留原文，没有采用 Agent 校样。");
    window.setTimeout(() => polishTriggerRef.current?.focus(), 0);
  }

  function adoptPolish() {
    const nextSource = polishDraft.trim();
    if (!nextSource || nextSource === sourceText.trim()) return;
    onSourceChange(nextSource);
    setPolishOpen(false);
    onNotice("已采用 Agent 润色校样；原始版本仍作为本次对校依据。 ");
  }

  const polishChangeCount = Math.max(
    1,
    polishNotes.length + introducedDetails.length + 1,
  );

  if (activeRoute === "A") {
    return (
      <main className={`${styles.scene} ${styles.aStartScene}`}>
        <section className={styles.caseStartSheet}>
          <header className={styles.caseStartRoute}>
            <div>
              <span>起案方式</span>
              <strong><i aria-hidden="true" />我有一个想法</strong>
            </div>
            <button onClick={onBack} type="button">更改起案方式</button>
          </header>
          {polishOpen ? (
            <section
              aria-labelledby="visual-polish-title"
              className={styles.polishPanel}
              ref={polishPanelRef}
            >
                <header>
                  <div>
                    <i aria-hidden="true">☷</i>
                    <span>
                      <small>原稿对校 / 独立润色候选</small>
                      <h2 id="visual-polish-title" tabIndex={-1}>逐字确认 Agent 改了什么。</h2>
                    </span>
                  </div>
                  <em>{polishModes.find((mode) => mode.value === polishMode)?.label}</em>
                </header>
                <div className={styles.polishComparison}>
                  <section>
                    <header><strong>原始来源</strong><span>■ 你的原文</span></header>
                    <textarea aria-label="当前作者原稿" readOnly value={polishOriginal} />
                  </section>
                  <section>
                    <header><strong>可编辑校样</strong><span>■ Agent 建议</span></header>
                    <textarea
                      aria-label="编辑 Agent 润色工作稿"
                      onChange={(event) => setPolishDraft(event.target.value)}
                      value={polishDraft}
                    />
                  </section>
                </div>
                <div className={styles.polishAudit}>
                  <section>
                    <strong>修改说明</strong>
                    <p>本次修改了 <b>{polishChangeCount}</b> 处表达。</p>
                    <ul>{polishNotes.map((note) => <li key={note}>{note}</li>)}</ul>
                  </section>
                  <section data-warning={introducedDetails.length > 0 || undefined}>
                    <strong>新增细节审阅</strong>
                    {introducedDetails.length ? (
                      <ul>{introducedDetails.map((detail) => <li key={detail}>{detail}</li>)}</ul>
                    ) : (
                      <p>本次校样没有新增情节事实。</p>
                    )}
                  </section>
                </div>
                <footer>
                  <button onClick={closePolish} type="button">保留原文</button>
                  <button
                    disabled={!polishDraft.trim() || polishDraft.trim() === sourceText.trim()}
                    onClick={adoptPolish}
                    type="button"
                  >
                    采用这版
                  </button>
                </footer>
            </section>
          ) : (
            <div className={styles.caseStartComposer}>
              <header className={styles.composerIntro}>
                <div>
                  <span>PATH A / ORIGINAL SOURCE</span>
                  <h1 tabIndex={-1}>把你的想法告诉我</h1>
                  <p>不需要完整。一个人物、一个谜题、一个场景，甚至一句话都可以。</p>
                </div>
              </header>
              <section className={styles.sourceEditorCard}>
                <header>
                  <div>
                    <i aria-hidden="true">◎</i>
                    <span><strong>最初想法</strong><small>你的输入会作为不可替换的原始来源</small></span>
                  </div>
                  <button onClick={() => onSourceChange(DEFAULT_SOURCE)} type="button">示例范文</button>
                </header>
                <label>
                  <span className={styles.srOnly}>最初想法</span>
                  <textarea
                    aria-label="最初想法"
                    maxLength={2000}
                    onChange={(event) => onSourceChange(event.target.value)}
                    value={sourceText}
                  />
                </label>
                <footer>
                  <div>
                    <button onClick={() => onSourceChange(DEFAULT_SOURCE)} type="button">输入示例</button>
                    <button onClick={() => onSourceChange("")} type="button">清空</button>
                  </div>
                  <span>{sourceText.length} / 2000</span>
                </footer>
              </section>
              <section
                aria-labelledby="visual-polish-control-title"
                className={styles.polishControl}
              >
                <header>
                  <div>
                    <i aria-hidden="true">✦</i>
                    <span>
                      <h2 id="visual-polish-control-title">需要 Agent 帮你整理表达吗？</h2>
                      <p>先生成独立校样，再由你逐字审阅是否采用。</p>
                    </span>
                  </div>
                  <button
                    aria-expanded="false"
                    disabled={!sourceText.trim()}
                    onClick={openPolish}
                    ref={polishTriggerRef}
                    type="button"
                  >
                    生成润色校样
                  </button>
                </header>
                <fieldset className={styles.polishModes}>
                  <legend className={styles.srOnly}>选择润色模式</legend>
                  {polishModes.map((mode) => (
                    <label data-selected={polishMode === mode.value || undefined} key={mode.value}>
                      <input
                        checked={polishMode === mode.value}
                        name="visual-polish-mode-control"
                        onChange={() => setPolishMode(mode.value)}
                        type="radio"
                      />
                      <i aria-hidden="true">
                        {mode.value === "proofread" ? "✦" : mode.value === "rewrite" ? "⌁" : "▣"}
                      </i>
                      <span><strong>{mode.label}</strong><small>{mode.hint}</small></span>
                    </label>
                  ))}
                </fieldset>
              </section>
              <div className={styles.composerActions}>
                <button
                  className={styles.organizeButton}
                  disabled={!sourceText.trim()}
                  onClick={onContinue}
                  type="button"
                >
                  整理这个想法 <ArrowIcon />
                </button>
              </div>
            </div>
          )}
        </section>
      </main>
    );
  }

  return (
    <main className={`${styles.scene} ${activeRoute === "B" ? styles.ideaScene : ""}`}>
      <SceneHeading
        backLabel="重新选择起点"
        description={activeRouteDefinition.detail}
        eyebrow={`PATH ${activeRouteDefinition.code} / SOURCE`}
        onBack={onBack}
        title={activeRouteDefinition.title}
      />
      {activeRoute === "B" ? (
        <section className={styles.ideaStudio}>
          <section aria-labelledby="demo-idea-stage-title" className={ideaStageStyles.stage}>
            <header className={ideaStageStyles.header}>
              <h2 className={ideaStageStyles.title} id="demo-idea-stage-title">
                {ideasGenerated ? "选择一个创意方向" : "创意方向"}
              </h2>
              <p className={ideaStageStyles.subtitle}>
                可按时代、场景、氛围与关键词自由组合，或留空由 Agent 自主发挥。
              </p>
            </header>

            <div className={ideaStageStyles.preferences}>
              <IdeaPreferenceRow
                group="eras"
                label="时代"
                onToggle={onPreferenceToggle}
                options={ideaEraOptions}
                selected={ideaPreferences.eras}
              />
              <IdeaPreferenceRow
                group="settings"
                label="场景"
                onToggle={onPreferenceToggle}
                options={ideaSettingOptions}
                selected={ideaPreferences.settings}
              />
              <IdeaPreferenceRow
                group="atmospheres"
                label="氛围"
                onToggle={onPreferenceToggle}
                options={ideaAtmosphereOptions}
                selected={ideaPreferences.atmospheres}
              />
              <div className={ideaStageStyles.prefRow}>
                <label className={ideaStageStyles.prefLabel} htmlFor="demo-idea-keywords">关键词</label>
                <input
                  className={ideaStageStyles.prefInput}
                  id="demo-idea-keywords"
                  onChange={(event) => onKeywordChange(event.target.value)}
                  placeholder="例如：时间循环、双胞胎（用逗号分隔）"
                  value={ideaPreferences.keywords}
                />
              </div>
            </div>

            {!ideasGenerated ? (
              <div className={ideaStageStyles.empty}>
                <button className={ideaStageStyles.primaryBtn} onClick={onGenerateIdeas} type="button">
                  生成创意候选
                </button>
              </div>
            ) : (
              <>
                <div aria-label="选择一个值得继续追问的方向" className={ideaStageStyles.grid} role="group">
                  {generatedIdeas.map((idea) => (
                    <DemoIdeaCard
                      idea={idea}
                      key={idea.id}
                      onNotice={onNotice}
                      onSelect={onSelectIdea}
                      selected={selectedIdea === idea.id}
                    />
                  ))}
                </div>
                <details className={ideaStageStyles.archived}>
                  <summary>灵感资产（3 个历史候选）</summary>
                  <details className={`${ideaStageStyles.archived} ${styles.ideaHistoryBatch}`}>
                    <summary>第 1 批（3 个候选）</summary>
                    <div className={ideaStageStyles.grid}>
                      {generatedIdeas.map((idea) => (
                        <DemoIdeaCard
                          historical
                          idea={idea}
                          key={`history-${idea.id}`}
                          onNotice={onNotice}
                          onSelect={onSelectIdea}
                          readonly
                          selected={false}
                        />
                      ))}
                    </div>
                  </details>
                </details>
              </>
            )}
          </section>
        </section>
      ) : null}

      {activeRoute === "C" ? (
        <section className={styles.importLayout}>
          <div className={styles.importDrop} data-ready={importReady || undefined}>
            <FileIcon />
            <span>{importReady ? "雪夜来信.txt" : "选择一份稿件作为来源"}</span>
            <p>{importReady ? "12,480 字 · 已识别 3 位人物、2 条时间冲突" : "本 Demo 不会读取真实文件。使用示例即可预览提取后的视觉效果。"}</p>
            <button onClick={() => onImportReady(!importReady)} type="button">
              {importReady ? "移除示例" : "使用示例稿件"}
            </button>
          </div>
          <div className={styles.extractPreview}>
            <header><span>提取预览</span><small>{importReady ? "等待你确认" : "尚未载入"}</small></header>
            <dl>
              <div><dt>核心异常</dt><dd>{importReady ? "三份记录对应不上同一段时间" : "—"}</dd></div>
              <div><dt>关键人物</dt><dd>{importReady ? "幸存者 / 值班员 / 档案修复师" : "—"}</dd></div>
              <div><dt>来源边界</dt><dd>{importReady ? "只使用示例稿件中的明确内容" : "—"}</dd></div>
            </dl>
          </div>
        </section>
      ) : null}

      <footer className={styles.sceneActions}>
        <p><span>下一步</span> 只追问会改变故事方向的判断。</p>
        <button
          className={styles.primaryButton}
          disabled={(activeRoute === "B" && !ideasGenerated) || (activeRoute === "C" && !importReady)}
          onClick={onContinue}
          type="button"
        >
          继续关键追问 <ArrowIcon />
        </button>
      </footer>
    </main>
  );
}

function QuestionsScene({
  answer,
  conclusionMode,
  onAnswerChange,
  onConclusionModeChange,
  onContinue,
  onNoteChange,
  onPrevious,
  onScopeModeChange,
  onStepChange,
  questionIndex,
  questionNotes,
  routeSource,
  scopeMode,
}: {
  answer: string;
  conclusionMode: ConclusionMode;
  onAnswerChange: (value: string) => void;
  onConclusionModeChange: (mode: ConclusionMode) => void;
  onContinue: () => void;
  onNoteChange: (index: number, value: string) => void;
  onPrevious: () => void;
  onScopeModeChange: (mode: ScopeMode) => void;
  onStepChange: (index: number) => void;
  questionIndex: number;
  questionNotes: string[];
  routeSource: string;
  scopeMode: ScopeMode;
}) {
  const questionTitleRef = useRef<HTMLHeadingElement>(null);
  const totalQuestions = 3;
  const isLastQuestion = questionIndex === totalQuestions - 1;
  const canAdvance =
    questionIndex === 1 || isLastQuestion ? Boolean(answer.trim()) : true;

  useEffect(() => {
    questionTitleRef.current?.focus();
  }, [questionIndex]);

  function advance() {
    if (!canAdvance) return;
    if (isLastQuestion) onContinue();
    else onStepChange(questionIndex + 1);
  }

  return (
    <main className={`${styles.scene} ${styles.questionScene}`}>
      <SceneHeading
        backLabel={questionIndex === 0 ? "返回起案" : "返回上一题"}
        description="回答可以反复修改；系统只会标记受影响的下游内容，不会静默删除。"
        eyebrow="02 / DECISIONS"
        onBack={onPrevious}
        title="只回答会改变方向的问题。"
      />
      <section aria-label={`关键追问 ${questionIndex + 1} / ${totalQuestions}`} className={styles.questionWorkspace}>
        <header className={styles.questionContextBar}>
          <div>
            <span>当前起案依据</span>
            <p>{routeSource}</p>
          </div>
          <strong>{questionIndex + 1} / {totalQuestions}</strong>
        </header>

        <div className={styles.questionPrompt}>
          <header className={styles.questionPromptHeader}>
            <div><span>关键判断 {String(questionIndex + 1).padStart(2, "0")}</span><em>会影响后续推演</em></div>
            <i aria-hidden="true">{String(questionIndex + 1).padStart(2, "0")}</i>
          </header>

          {questionIndex === 0 ? (
            <div className={styles.questionBody}>
              <h2 ref={questionTitleRef} tabIndex={-1}>真相应该怎样收束？</h2>
              <p>这个决定会影响后续证词、物证和最终揭示的组织方式。</p>
              <fieldset aria-label="选择真相收束方式" className={styles.questionOptions}>
                <label data-selected={conclusionMode === "unique" || undefined}>
                  <input checked={conclusionMode === "unique"} name="conclusion-mode" onChange={() => onConclusionModeChange("unique")} type="radio" />
                  <span><strong>作者心中已有唯一真相</strong><small>答案已经确定，后续线索需要逐步逼近它。</small></span>
                </label>
                <label data-selected={conclusionMode === "agent" || undefined}>
                  <input checked={conclusionMode === "agent"} name="conclusion-mode" onChange={() => onConclusionModeChange("agent")} type="radio" />
                  <span><strong>由 Agent 提出若干可能</strong><small>先保留多个可成立解释，之后再由你决定。</small></span>
                </label>
                <label data-selected={conclusionMode === "open" || undefined}>
                  <input checked={conclusionMode === "open"} name="conclusion-mode" onChange={() => onConclusionModeChange("open")} type="radio" />
                  <span><strong>保持开放</strong><small>故事本身不锁死唯一真相，让多种解释继续成立。</small></span>
                </label>
              </fieldset>
              <label className={styles.questionOwnAnswer}>
                <span>或写下你的补充判断 <small>选填</small></span>
                <textarea aria-label="真相收束补充" onChange={(event) => onNoteChange(0, event.target.value)} value={questionNotes[0]} />
              </label>
            </div>
          ) : null}

          {questionIndex === 1 ? (
            <div className={styles.questionBody}>
              <h2 ref={questionTitleRef} tabIndex={-1}>这份作品最终要回答哪一个核心问题？</h2>
              <p>把它写成一句可以被调查、验证或推翻的问题；它会成为后续推演的核心命题。</p>
              <label className={styles.questionOwnAnswer}>
                <span>核心问题 <small>必答</small></span>
                <textarea aria-label="核心问题答案" onChange={(event) => onAnswerChange(event.target.value)} value={answer} />
              </label>
            </div>
          ) : null}

          {questionIndex === 2 ? (
            <div className={styles.questionBody}>
              <h2 ref={questionTitleRef} tabIndex={-1}>这次建案应该先聚焦到什么范围？</h2>
              <p>选择本轮最值得完成的叙事边界，之后仍然可以创建新版本继续扩展。</p>
              <fieldset aria-label="选择叙事范围" className={styles.questionOptions}>
                <label data-selected={scopeMode === "focused" || undefined}>
                  <input checked={scopeMode === "focused"} name="scope-mode" onChange={() => onScopeModeChange("focused")} type="radio" />
                  <span><strong>单一核心冲突</strong><small>先围绕一个人物、一条谜题完成闭环。</small></span>
                </label>
                <label data-selected={scopeMode === "ensemble" || undefined}>
                  <input checked={scopeMode === "ensemble"} name="scope-mode" onChange={() => onScopeModeChange("ensemble")} type="radio" />
                  <span><strong>多人物并行</strong><small>保留多个立场，让线索在人物之间交叉。</small></span>
                </label>
                <label data-selected={scopeMode === "expansive" || undefined}>
                  <input checked={scopeMode === "expansive"} name="scope-mode" onChange={() => onScopeModeChange("expansive")} type="radio" />
                  <span><strong>开放世界延展</strong><small>允许后续继续加入地点、事件与支线。</small></span>
                </label>
              </fieldset>
              <label className={styles.questionOwnAnswer}>
                <span>或补充这次不处理的边界 <small>选填</small></span>
                <textarea aria-label="叙事范围补充" onChange={(event) => onNoteChange(2, event.target.value)} value={questionNotes[2]} />
              </label>
            </div>
          ) : null}
        </div>

        <footer className={styles.questionPager}>
          <button className={styles.secondaryButton} onClick={onPrevious} type="button">
            {questionIndex === 0 ? "返回起案" : "← 上一题"}
          </button>
          <div aria-label="追问进度" className={styles.questionDots}>
            {Array.from({ length: totalQuestions }, (_, index) => (
              <button
                aria-label={`前往第 ${index + 1} 题`}
                aria-current={index === questionIndex ? "step" : undefined}
                key={index}
                onClick={() => onStepChange(index)}
                type="button"
              />
            ))}
          </div>
          <button className={styles.primaryButton} disabled={!canAdvance} onClick={advance} type="button">
            {isLastQuestion ? "查看建案确认" : "下一题"} <ArrowIcon />
          </button>
        </footer>
      </section>
    </main>
  );
}

function VersionRail({ frozenVersions, workingVersion }: { frozenVersions: number[]; workingVersion: number }) {
  const versionNumbers = Array.from(new Set([...frozenVersions, workingVersion])).sort((a, b) => a - b);
  return (
    <aside aria-label="Brief 版本" className={styles.versionRail}>
      <header><span>VERSION LINE</span><strong>版本记录</strong></header>
      <ol>
        {versionNumbers.map((version) => {
          const frozen = frozenVersions.includes(version);
          return (
            <li data-current={version === workingVersion || undefined} data-frozen={frozen || undefined} key={version}>
              <i aria-hidden="true" />
              <div><strong>Brief V{version}</strong><span>{frozen ? "已确认 · 冻结" : "编辑中"}</span></div>
            </li>
          );
        })}
      </ol>
      <p>已确认版本不会被后续修改覆盖。</p>
    </aside>
  );
}

function ConfirmationScene({
  answer,
  briefAnswer,
  confirmationPhase,
  conclusionMode,
  frozenVersions,
  onBack,
  onConclusionModeChange,
  onEditQuestion,
  onEditSource,
  onFreeze,
  onRefresh,
  routeLabel,
  routeSource,
  scopeMode,
  stale,
  workingVersion,
}: {
  answer: string;
  briefAnswer: string;
  confirmationPhase: ConfirmationPhase;
  conclusionMode: ConclusionMode;
  frozenVersions: number[];
  onBack: () => void;
  onConclusionModeChange: (mode: ConclusionMode) => void;
  onEditQuestion: () => void;
  onEditSource: () => void;
  onFreeze: () => void;
  onRefresh: () => void;
  routeLabel: string;
  routeSource: string;
  scopeMode: ScopeMode;
  stale: boolean;
  workingVersion: number;
}) {
  if (confirmationPhase !== "review") {
    const completed = confirmationPhase === "completed";
    return (
      <main className={`${styles.scene} ${styles.confirmationTransition}`}>
        <section aria-live="assertive" className={styles.transitionCard} role="status">
          <span className={styles.transitionEyebrow}>CASE BRIEF / 03</span>
          <i aria-hidden="true" data-completed={completed || undefined}>
            {completed ? "✓" : null}
          </i>
          <h1 tabIndex={-1}>{completed ? "建案完成" : "正在确认建案"}</h1>
          <p>
            {completed
              ? "CaseFile 已准备好进入深稿阶段。"
              : "正在整理创作边界与生成依据……"}
          </p>
        </section>
      </main>
    );
  }

  return (
    <main className={`${styles.scene} ${styles.confirmationScene}`}>
      <SceneHeading
        backLabel="返回关键追问"
        description="只确认会约束后续生成的内容。"
        eyebrow={`03 / BRIEF V${workingVersion}`}
        onBack={onBack}
        title={workingVersion > 1 ? `审阅建案修订 V${workingVersion}` : "建案确认"}
      />
      <p className={styles.agentPreparedNotice}>
        <span aria-hidden="true">✦</span>
        Agent 已经把你的起案内容与回答整理成了一份 Case Brief。
      </p>
      {stale ? (
        <section className={styles.staleBanner} role="alert">
          <span>!</span>
          <div><strong>关键回答已经变化</strong><p>原 Brief 仍在下方。重新整理后才会用新的判断替换相关内容。</p></div>
          <button onClick={onRefresh} type="button">重新整理简报 <ArrowIcon /></button>
        </section>
      ) : null}
      <article className={styles.confirmationSheet} data-stale={stale || undefined}>
        <div aria-hidden="true" className={styles.confirmationPaperIndex}>
          <span>BRIEF</span><b>03</b>
        </div>
        <header className={styles.confirmationSheetHeader}>
          <div><span>CASE BRIEF</span><strong>不存在的时间</strong></div>
          <em>{stale ? "需要更新" : `V${workingVersion} · 待确认`}</em>
        </header>

        <section className={styles.confirmationField}>
          <header><h2>核心概念</h2><button onClick={onEditSource} type="button">返回起案编辑</button></header>
          <p>{routeSource}</p>
          <small><span aria-hidden="true">●</span> 来自你的原文</small>
        </section>

        <section className={styles.confirmationField}>
          <header><h2>核心问题</h2><button onClick={onEditQuestion} type="button">返回第 2 题编辑</button></header>
          <p>{briefAnswer}</p>
          <small>{stale ? `新回答待同步：${answer}` : "来自关键追问"}</small>
        </section>

        <fieldset className={styles.truthModes}>
          <legend>真相处理方式</legend>
          <label data-selected={conclusionMode === "open" || undefined}>
            <input checked={conclusionMode === "open"} name="confirmation-truth-mode" onChange={() => onConclusionModeChange("open")} type="radio" />
            <span><strong>保持开放</strong><small>不提前锁死唯一真相；深稿可以保留多个有证据支持的解释。</small></span>
          </label>
          <label data-selected={conclusionMode === "unique" || undefined}>
            <input checked={conclusionMode === "unique"} name="confirmation-truth-mode" onChange={() => onConclusionModeChange("unique")} type="radio" />
            <span><strong>作者提供答案</strong><small>以作者心中的答案为准，让后续线索逐步逼近它。</small></span>
          </label>
          <label data-selected={conclusionMode === "agent" || undefined}>
            <input checked={conclusionMode === "agent"} name="confirmation-truth-mode" onChange={() => onConclusionModeChange("agent")} type="radio" />
            <span><strong>让 Agent 在深稿中提出答案</strong><small>让 Agent 根据证据提出候选答案，再由你决定是否采用。</small></span>
          </label>
        </fieldset>

        <section className={styles.confirmationOutline}>
          <header><h2>内容骨架</h2><button onClick={onEditQuestion} type="button">编辑</button></header>
          <ol>
            <li><b>01</b><div><strong>进入档案</strong><p>主角接触起案中的异常记录，确认它并非简单误记。</p></div></li>
            <li><b>02</b><div><strong>记录冲突</strong><p>多组可信来源开始互相矛盾，却又留下能够互相印证的细节。</p></div></li>
            <li><b>03</b><div><strong>重建缺口</strong><p>从证词、物证与时间线中寻找所有记录共同避开的部分。</p></div></li>
            <li><b>04</b><div><strong>真相逼近</strong><p>不同解释逐步竞争，直到证据无法继续共存。</p></div></li>
          </ol>
        </section>

        <section className={styles.briefSummaries} aria-label="建案摘要">
          <details>
            <summary><span>核心卖点</span><strong>3 项</strong><small>展开</small></summary>
            <ul><li>可靠记录彼此冲突</li><li>调查过程可逐步验证</li><li>真相方式由作者掌控</li></ul>
          </details>
          <details className={styles.boundaryDetails}>
            <summary><span>创作边界</span><strong>4 项</strong><small>展开</small></summary>
            <div className={styles.boundaryIntro}>决定后续 Agent 哪些内容不能擅自改变。</div>
            <div className={styles.boundaryList}>
              <article><header><strong>必须保留</strong><em>必须</em></header><p>三份记录彼此独立，并且在各自来源中都足够可靠。</p></article>
              <article><header><strong>禁止出现</strong><em>必须</em></header><p>不使用超自然力量直接解释所有记录的冲突。</p></article>
              <article><header><strong>氛围</strong><em>偏好</em></header><p>克制、冷峻，让不安来自证据与认知之间的缝隙。</p></article>
              <article><header><strong>叙事范围</strong><em>偏好</em></header><p>{scopeModeLabel(scopeMode)}，本轮先完成可追溯的推理闭环。</p></article>
            </div>
            <button type="button">＋ 添加创作边界</button>
          </details>
          <div className={styles.summaryRow}><span>预计规模</span><strong>中篇 · 5–8 小时</strong><button type="button">修改</button></div>
          <details>
            <summary><span>风险提示</span><strong>2 项</strong><small>查看</small></summary>
            <ul><li>需要让记录差异持续可核对，避免只靠结尾解释。</li><li>线索密度过高时，应优先保护核心问题的可读性。</li></ul>
          </details>
        </section>

        <section className={styles.briefUtilityLinks} aria-label="建案辅助信息">
          <details><summary>··· 高级设置</summary><p>保留生成依据；关闭未经确认的自动扩写。</p></details>
          <details><summary>版本历史 {Math.max(frozenVersions.length, workingVersion)}</summary><VersionRail frozenVersions={frozenVersions} workingVersion={workingVersion} /></details>
          <details><summary>来源信息</summary><p>{routeLabel} · 起案原文与关键追问</p></details>
        </section>

        <footer className={styles.confirmationActions}>
          <p><span>{stale ? "先更新 Brief" : "确认即冻结"}</span> {stale ? "当前版本仍保留旧判断。" : "后续修改会创建新版本，已确认内容不会被覆盖。"}</p>
          <button className={styles.primaryButton} disabled={stale} onClick={onFreeze} type="button">确认建案并继续 <ArrowIcon /></button>
        </footer>
      </article>
    </main>
  );
}

function FrozenScene({
  answer,
  frozenVersions,
  onOpenRestart,
  onOpenRevision,
  restartButtonRef,
  reviseButtonRef,
  routeSource,
  workingVersion,
}: {
  answer: string;
  frozenVersions: number[];
  onOpenRestart: () => void;
  onOpenRevision: () => void;
  restartButtonRef: React.RefObject<HTMLButtonElement | null>;
  reviseButtonRef: React.RefObject<HTMLButtonElement | null>;
  routeSource: string;
  workingVersion: number;
}) {
  return (
    <main className={styles.scene}>
      <header className={styles.frozenHeading}>
        <span>CASE BRIEF / FROZEN</span>
        <h1 tabIndex={-1}>这一版建案已经归档。</h1>
        <p>它会继续作为后续推演的依据。如果方向变化，请创建修订，而不是覆盖已经确认的判断。</p>
      </header>
      <div className={styles.briefLayout}>
        <article className={styles.frozenSheet}>
          <div aria-label={`Brief V${workingVersion} 已冻结`} className={styles.archiveStamp} key={workingVersion}>
            <span>CONFIRMED</span><strong>V{workingVersion}</strong><small>已确认</small>
          </div>
          <header><span>当前建案</span><strong>不存在的时间</strong><em>Brief V{workingVersion}</em></header>
          <dl>
            <div><dt>核心概念</dt><dd>{routeSource}</dd></div>
            <div><dt>推理目标</dt><dd>{answer}</dd></div>
            <div><dt>版本状态</dt><dd>已确认 · 内容锁定 · 可创建修订</dd></div>
          </dl>
          <footer><span>下一步</span><strong>进入深稿方案，或先修订建案</strong></footer>
        </article>
        <VersionRail frozenVersions={frozenVersions} workingVersion={workingVersion} />
      </div>
      <section className={styles.frozenActions}>
        <div><span>在同一建案中继续</span><h2>想调整判断？创建 V{workingVersion + 1}。</h2><p>当前版本和历史仍然可查，新版本从这里继续编辑。</p></div>
        <div>
          <button className={styles.secondaryButton} onClick={onOpenRestart} ref={restartButtonRef} type="button">重新起案</button>
          <button className={styles.primaryButton} onClick={onOpenRevision} ref={reviseButtonRef} type="button">修改建案 <ArrowIcon /></button>
        </div>
      </section>
    </main>
  );
}
