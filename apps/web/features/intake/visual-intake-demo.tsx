"use client";

import { type CSSProperties, useEffect, useMemo, useRef, useState } from "react";

import styles from "./visual-intake-demo.module.css";

type RouteCode = "A" | "B" | "C";
type Scene = "home" | "start" | "questions" | "confirmation" | "frozen";
type DialogKind = "revise" | "restart";

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
    kicker: "都市 · 溯因推理",
    title: "最后一班不存在的列车",
    body: "每位乘客都记得自己下过车，但车站从未出现在任何线路图上。",
  },
  {
    id: "archive",
    kicker: "近未来 · 档案悬疑",
    title: "被删除的第十三分钟",
    body: "城市的全部监控在同一分钟里，留下了十三分钟互相矛盾的记录。",
  },
  {
    id: "island",
    kicker: "海岛 · 封闭空间",
    title: "潮汐带回了第二份遗嘱",
    body: "两份笔迹相同的遗嘱，分别要求在涨潮和退潮时公布真相。",
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

export function VisualIntakeDemo() {
  const [scene, setScene] = useState<Scene>("home");
  const [activeRoute, setActiveRoute] = useState<RouteCode | null>(null);
  const [sourceText, setSourceText] = useState(DEFAULT_SOURCE);
  const [selectedIdea, setSelectedIdea] = useState("archive");
  const [ideaPreferences, setIdeaPreferences] = useState({
    era: "近未来",
    setting: "都市",
    mood: "悬疑",
  });
  const [importReady, setImportReady] = useState(true);
  const [answer, setAnswer] = useState(
    "找出是谁制造了那段不存在的时间，以及三份可靠记录为什么会同时失真。",
  );
  const [briefAnswer, setBriefAnswer] = useState(answer);
  const [conclusionMode, setConclusionMode] = useState<"open" | "unique">("open");
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

  function resetDemo() {
    setScene("home");
    setActiveRoute(null);
    setSourceText(DEFAULT_SOURCE);
    setSelectedIdea("archive");
    setIdeaPreferences({ era: "近未来", setting: "都市", mood: "悬疑" });
    setImportReady(true);
    const initialAnswer =
      "找出是谁制造了那段不存在的时间，以及三份可靠记录为什么会同时失真。";
    setAnswer(initialAnswer);
    setBriefAnswer(initialAnswer);
    setConclusionMode("open");
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
    if (activeRoute === "C" && !importReady) return;
    setScene("questions");
    setNotice("起案内容已保留，可以继续回答关键问题。");
  }

  function openConfirmation() {
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
    setFrozenVersions((versions) =>
      versions.includes(workingVersion) ? versions : [...versions, workingVersion],
    );
    setScene("frozen");
    setNotice(`Brief V${workingVersion} 已确认并冻结。`);
  }

  function beginRevision() {
    const nextVersion = Math.max(...frozenVersions, workingVersion) + 1;
    setWorkingVersion(nextVersion);
    setBriefAnswer(answer);
    setDialog(null);
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
            <small>{scene === "home" ? "建案中心" : "新建案件"}</small>
          </span>
        </button>
        <nav aria-label="Demo 页面导航" className={styles.topNav}>
          {scene === "home" ? (
            <>
              <span aria-current="page">建案</span>
              <span>工作台</span>
            </>
          ) : (
            <>
              <span className={styles.localDraftStatus}><i aria-hidden="true" />本地草稿</span>
              <span>历史</span>
            </>
          )}
          <button onClick={resetDemo} type="button">重置演示</button>
          <i aria-hidden="true">A</i>
        </nav>
      </header>

      {scene === "home" ? (
        <HomeScene
          notice={notice}
          onOpenRoute={openRoute}
          retainedVersion={retainedVersion}
        />
      ) : (
        <div className={styles.flowShell}>
          {scene === "start" && activeRoute === "A" ? null : (
            <DossierSpine
              briefStale={briefStale}
              frozen={scene === "frozen"}
              onNavigate={(target) => {
                if (target === "source") setScene("start");
                if (target === "decisions") setScene("questions");
                if (target === "brief") setScene("confirmation");
              }}
              scene={scene}
            />
          )}
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
              onBack={() => setScene("home")}
              onContinue={continueToQuestions}
              onImportReady={setImportReady}
              onPreferenceChange={(key, value) =>
                setIdeaPreferences((current) => ({ ...current, [key]: value }))
              }
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
              onBack={() => setScene("start")}
              onConclusionModeChange={setConclusionMode}
              onContinue={openConfirmation}
              routeSource={routeSource}
            />
          ) : null}
          {scene === "confirmation" ? (
            <ConfirmationScene
              answer={answer}
              briefAnswer={briefAnswer}
              conclusionMode={conclusionMode}
              frozenVersions={frozenVersions}
              onBack={() => setScene("questions")}
              onFreeze={freezeBrief}
              onRefresh={refreshBrief}
              routeLabel={routeLabel(activeRoute)}
              routeSource={routeSource}
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
  onNavigate,
  scene,
}: {
  briefStale: boolean;
  frozen: boolean;
  onNavigate: (target: "source" | "decisions" | "brief") => void;
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
    <nav aria-label="建案依赖进度" className={styles.spine}>
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

function StartScene({
  activeRoute,
  activeRouteDefinition,
  importReady,
  ideaPreferences,
  onBack,
  onContinue,
  onImportReady,
  onPreferenceChange,
  onSelectIdea,
  onSourceChange,
  selectedIdea,
  sourceText,
}: {
  activeRoute: RouteCode | null;
  activeRouteDefinition: (typeof routes)[number];
  importReady: boolean;
  ideaPreferences: { era: string; setting: string; mood: string };
  onBack: () => void;
  onContinue: () => void;
  onImportReady: (ready: boolean) => void;
  onPreferenceChange: (
    key: "era" | "setting" | "mood",
    value: string,
  ) => void;
  onSelectIdea: (idea: string) => void;
  onSourceChange: (value: string) => void;
  selectedIdea: string;
  sourceText: string;
}) {
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
          <div className={styles.caseStartComposer}>
            <span>PATH A / ORIGINAL SOURCE</span>
            <h1 tabIndex={-1}>把你的想法告诉我</h1>
            <p>不需要完整。一个人物、一个谜题、一个场景，甚至一句话都可以。</p>
            <label>
              <span className={styles.srOnly}>最初想法</span>
              <textarea
                aria-label="最初想法"
                maxLength={2000}
                onChange={(event) => onSourceChange(event.target.value)}
                value={sourceText}
              />
            </label>
            <div className={styles.composerMeta}>
              <span>{sourceText.length} / 2000</span>
              <button onClick={() => onSourceChange(DEFAULT_SOURCE)} type="button">恢复示例</button>
            </div>
            <button
              className={styles.organizeButton}
              disabled={!sourceText.trim()}
              onClick={onContinue}
              type="button"
            >
              整理这个想法 <ArrowIcon />
            </button>
          </div>
        </section>
      </main>
    );
  }

  return (
    <main className={styles.scene}>
      <SceneHeading
        backLabel="重新选择起点"
        description={activeRouteDefinition.detail}
        eyebrow={`PATH ${activeRouteDefinition.code} / SOURCE`}
        onBack={onBack}
        title={activeRouteDefinition.title}
      />
      {activeRoute === "B" ? (
        <section className={styles.ideaStudio}>
          <div className={styles.preferenceBar}>
            <span>创意偏好</span>
            <div>
              {["近未来", "现代"].map((value) => (
                <button
                  aria-pressed={ideaPreferences.era === value}
                  data-selected={ideaPreferences.era === value || undefined}
                  key={value}
                  onClick={() => onPreferenceChange("era", value)}
                  type="button"
                >{value}</button>
              ))}
            </div>
            <div>
              {["都市", "海岛"].map((value) => (
                <button
                  aria-pressed={ideaPreferences.setting === value}
                  data-selected={ideaPreferences.setting === value || undefined}
                  key={value}
                  onClick={() => onPreferenceChange("setting", value)}
                  type="button"
                >{value}</button>
              ))}
            </div>
            <div>
              {["悬疑", "温暖"].map((value) => (
                <button
                  aria-pressed={ideaPreferences.mood === value}
                  data-selected={ideaPreferences.mood === value || undefined}
                  key={value}
                  onClick={() => onPreferenceChange("mood", value)}
                  type="button"
                >{value}</button>
              ))}
            </div>
          </div>
          <fieldset className={styles.ideaChoices}>
            <legend>选择一个值得继续追问的方向</legend>
            {generatedIdeas.map((idea, index) => (
              <label data-selected={selectedIdea === idea.id || undefined} key={idea.id}>
                <input
                  checked={selectedIdea === idea.id}
                  name="idea-direction"
                  onChange={() => onSelectIdea(idea.id)}
                  type="radio"
                />
                <span>0{index + 1}</span>
                <small>{idea.kicker}</small>
                <strong>{idea.title}</strong>
                <p>{idea.body}</p>
              </label>
            ))}
          </fieldset>
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
          disabled={activeRoute === "C" && !importReady}
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
  onBack,
  onConclusionModeChange,
  onContinue,
  routeSource,
}: {
  answer: string;
  conclusionMode: "open" | "unique";
  onAnswerChange: (value: string) => void;
  onBack: () => void;
  onConclusionModeChange: (mode: "open" | "unique") => void;
  onContinue: () => void;
  routeSource: string;
}) {
  return (
    <main className={styles.scene}>
      <SceneHeading
        backLabel="返回起案"
        description="回答可以反复修改；系统只会标记受影响的下游内容，不会静默删除。"
        eyebrow="02 / DECISIONS"
        onBack={onBack}
        title="只回答会改变方向的问题。"
      />
      <section className={styles.questionLayout}>
        <div className={styles.sourceExcerpt}>
          <span>当前起案依据</span>
          <blockquote>{routeSource}</blockquote>
        </div>
        <div className={styles.questionStack}>
          <article className={styles.questionCard}>
            <header><span>关键判断 01</span><em>必答</em></header>
            <h2>这份作品最终要回答哪一个核心问题？</h2>
            <p>这个答案将决定线索如何组织，也会成为后续验证的核心命题。</p>
            <textarea aria-label="核心问题答案" onChange={(event) => onAnswerChange(event.target.value)} value={answer} />
          </article>
          <fieldset className={styles.modeQuestion}>
            <legend><span>关键判断 02</span> 真相应该怎样收束？</legend>
            <label data-selected={conclusionMode === "open" || undefined}>
              <input checked={conclusionMode === "open"} name="conclusion-mode" onChange={() => onConclusionModeChange("open")} type="radio" />
              <strong>保持开放</strong><small>保留多种解释，不暗中锁定唯一答案。</small>
            </label>
            <label data-selected={conclusionMode === "unique" || undefined}>
              <input checked={conclusionMode === "unique"} name="conclusion-mode" onChange={() => onConclusionModeChange("unique")} type="radio" />
              <strong>唯一真相</strong><small>深稿需要证明信息充分，排除同样成立的解释。</small>
            </label>
          </fieldset>
        </div>
      </section>
      <footer className={styles.sceneActions}>
        <p><span>已有内容会保留</span> 修改回答后，原 Brief 只会标记为需要更新。</p>
        <button className={styles.primaryButton} disabled={!answer.trim()} onClick={onContinue} type="button">查看建案确认 <ArrowIcon /></button>
      </footer>
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
  conclusionMode,
  frozenVersions,
  onBack,
  onFreeze,
  onRefresh,
  routeLabel,
  routeSource,
  stale,
  workingVersion,
}: {
  answer: string;
  briefAnswer: string;
  conclusionMode: "open" | "unique";
  frozenVersions: number[];
  onBack: () => void;
  onFreeze: () => void;
  onRefresh: () => void;
  routeLabel: string;
  routeSource: string;
  stale: boolean;
  workingVersion: number;
}) {
  return (
    <main className={styles.scene}>
      <SceneHeading
        backLabel="返回关键追问"
        description="只确认会约束后续生成的内容。确认后，这一版将成为不可静默修改的创作依据。"
        eyebrow={`03 / BRIEF V${workingVersion}`}
        onBack={onBack}
        title={workingVersion > 1 ? `审阅建案修订 V${workingVersion}` : "建案确认"}
      />
      {stale ? (
        <section className={styles.staleBanner} role="alert">
          <span>!</span>
          <div><strong>关键回答已经变化</strong><p>原 Brief 仍在下方。重新整理后才会用新的判断替换相关内容。</p></div>
          <button onClick={onRefresh} type="button">重新整理简报 <ArrowIcon /></button>
        </section>
      ) : null}
      <div className={styles.briefLayout}>
        <article className={styles.briefSheet} data-stale={stale || undefined}>
          <header>
            <div><span>CASE BRIEF</span><h2>不存在的时间</h2></div>
            <em>{stale ? "需要更新" : `V${workingVersion} · 待确认`}</em>
          </header>
          <section className={styles.briefGrid}>
            <div className={styles.briefBlock} data-wide="true"><span>核心概念</span><p>{routeSource}</p><small>来自起案原文</small></div>
            <div className={styles.briefBlock}><span>起案方式</span><p>{routeLabel}</p><small>由你选择</small></div>
            <div className={styles.briefBlock}><span>真相处理</span><p>{conclusionMode === "open" ? "保持开放" : "唯一真相"}</p><small>由你确认</small></div>
            <div className={styles.briefBlock} data-wide="true"><span>核心问题 / 推理目标</span><p>{briefAnswer}</p><small>{stale ? `新回答待同步：${answer}` : "来自关键追问"}</small></div>
          </section>
          <section className={styles.outlineBlock}>
            <header><span>内容骨架</span><small>拟议结构</small></header>
            <ol><li><b>01</b><div><strong>记录中的空白</strong><p>发现三份可靠材料共享同一个时间缺口。</p></div></li><li><b>02</b><div><strong>互相证明的谎言</strong><p>比对记录来源，确认矛盾并非简单伪造。</p></div></li><li><b>03</b><div><strong>封存之前</strong><p>在保留多种解释与锁定真相之间作出选择。</p></div></li></ol>
          </section>
        </article>
        <VersionRail frozenVersions={frozenVersions} workingVersion={workingVersion} />
      </div>
      <footer className={styles.sceneActions}>
        <p><span>{stale ? "先更新 Brief" : "确认即冻结"}</span> {stale ? "当前版本仍保留旧判断。" : "后续修改会创建新版本。"}</p>
        <button className={styles.primaryButton} disabled={stale} onClick={onFreeze} type="button">确认 Brief V{workingVersion} <ArrowIcon /></button>
      </footer>
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
