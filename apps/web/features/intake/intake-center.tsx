"use client";

import Link from "next/link";
import {
  type SetStateAction,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { useSessionUiOperation } from "@/features/case-session/use-session-ui-operation";
import { useCaseSession } from "@/features/case-session/case-session-provider";
import {
  isBriefConfirmationRevisionConflict,
} from "@/features/case-session/case-session-api";

import {
  candidateOriginLabels,
  candidateHistoryVersions,
  conclusionModes,
  applyBriefResolutionDecision,
  firstBriefConfirmationIssue,
  missingHardFields,
  polishModes,
  intakeSteps,
  resolutionModes,
  sampleIdea,
  type IntakeAnswer,
  type IntakeBrief,
  type BriefCandidate,
  type BriefConfirmationField,
  type BriefConfirmationIssue,
  type BriefResolutionDecision,
  type IntakeConstraint,
  type FieldSource,
  type IntakePolishMode,
  type ConclusionMode,
  type ResolutionMode,
  type IntakeStep,
} from "./intake-model";
import {
  CaseHistoryDrawer,
  loadCaseHistoryEntries,
  type CaseHistoryEntry,
} from "./case-history-drawer";
import { DraftCandidatesStage } from "./draft-candidates-stage";
import IdeaCandidatesStage from "./idea-candidates-stage";
import { useIdeaCandidates } from "./use-idea-candidates";
import { FieldShell, SourceBadge } from "./intake-field-shell";
import { Glyph } from "./intake-glyph";
import { IntakeLanding } from "./intake-landing";
import ReverseParseStage from "./reverse-parse-stage";
import {
  OutlineStagesEditor,
  SellingPointsEditor,
} from "./structured-list-editor";
import {
  BriefConfirmationInterruption,
  BriefConfirmationTransition,
  BriefRevisionDialog,
} from "./brief-confirmation-feedback";
import feedbackStyles from "./brief-confirmation-feedback.module.css";
import stageStyles from "./intake-early-stages.module.css";
import revisionStyles from "./intake-revision.module.css";
import styles from "./intake-center.module.css";

type BriefTextField =
  | "concept"
  | "sellingPoints"
  | "outline"
  | "reasoningGoal"
  | "authorAnswer"
  | "scopeEstimate"
  | "riskNotes";


type BriefConfirmationPhase =
  | "idle"
  | "processing"
  | "needs_input"
  | "success"
  | "failed";

type BriefRevisionChangeKey =
  | "concept"
  | "reasoningGoal"
  | "conclusionMode"
  | "resolutionMode"
  | "authorAnswer"
  | "sellingPoints"
  | "outline"
  | "scopeEstimate"
  | "riskNotes"
  | "constraints";

interface BriefRevisionChange {
  key: BriefRevisionChangeKey;
  label: string;
  before: string;
  after: string;
}

interface DialogueRevisionReceipt {
  projectId: number | null;
  instruction: string;
  candidateLabel: string;
  changes: BriefRevisionChange[];
}

interface IntakeDependencyInvalidation {
  projectId: number | null;
  questions: boolean;
  brief: boolean;
  changedAnswerKeys: string[];
}

const taskTypeLabels: Record<string, string> = {
  brief_polish: "原稿润色",
  brief_anchor_extract: "整理答案与规则",
  brief_intake_questions: "关键追问",
  brief_intake_synthesize: "创作简报候选",
  brief_strategy_options: "策略分析",
  brief_to_draft: "深稿生成",
  casefile_chat: "Agent 对话",
};

function taskTypeLabel(taskType: string) {
  return taskTypeLabels[taskType] ?? "任务";
}

function formatCandidateTimestamp(value: string) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  const pad = (part: number) => String(part).padStart(2, "0");
  return [
    `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())}`,
    `${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`,
  ].join(" ");
}

function compactRevisionText(value: string) {
  const compact = value.trim().replace(/\s+/gu, " ");
  if (!compact) return "未填写";
  return compact.length > 58 ? compact.slice(0, 58) + "…" : compact;
}

function briefListSummary(value: string, unit: string) {
  const items = value
    .split(/\r?\n/u)
    .map((item) => item.trim())
    .filter(Boolean);
  return items.length
    ? `${items.length} ${unit} · ${compactRevisionText(items[0] ?? "")}`
    : `0 ${unit}`;
}

function briefConstraintSummary(brief: IntakeBrief) {
  const items = brief.constraints.filter((constraint) =>
    constraint.statement.trim(),
  );
  if (!items.length) return "0 条";
  return `${items.length} 条 · ${items
    .slice(0, 2)
    .map((constraint) => constraint.label)
    .join("、")}`;
}

function describeBriefRevision(
  before: IntakeBrief,
  after: IntakeBrief,
): BriefRevisionChange[] {
  const changes: BriefRevisionChange[] = [];
  const addTextChange = (
    key: Exclude<
      BriefRevisionChangeKey,
      "conclusionMode" | "resolutionMode" | "sellingPoints" | "outline" | "constraints"
    >,
    label: string,
  ) => {
    if (before[key].trim() === after[key].trim()) return;
    changes.push({
      key,
      label,
      before: compactRevisionText(before[key]),
      after: compactRevisionText(after[key]),
    });
  };

  addTextChange("concept", "一句话概念");
  addTextChange("reasoningGoal", "推理目标");

  if (before.conclusionMode !== after.conclusionMode) {
    changes.push({
      key: "conclusionMode",
      label: "结论模式",
      before:
        conclusionModes.find((mode) => mode.value === before.conclusionMode)
          ?.label ?? before.conclusionMode,
      after:
        conclusionModes.find((mode) => mode.value === after.conclusionMode)
          ?.label ?? after.conclusionMode,
    });
  }
  if (before.resolutionMode !== after.resolutionMode) {
    changes.push({
      key: "resolutionMode",
      label: "结论处理方式",
      before:
        resolutionModes.find((mode) => mode.value === before.resolutionMode)
          ?.label ?? before.resolutionMode,
      after:
        resolutionModes.find((mode) => mode.value === after.resolutionMode)
          ?.label ?? after.resolutionMode,
    });
  }

  addTextChange("authorAnswer", "作者答案");

  if (before.sellingPoints.trim() !== after.sellingPoints.trim()) {
    changes.push({
      key: "sellingPoints",
      label: "核心卖点",
      before: briefListSummary(before.sellingPoints, "项"),
      after: briefListSummary(after.sellingPoints, "项"),
    });
  }
  if (before.outline.trim() !== after.outline.trim()) {
    changes.push({
      key: "outline",
      label: "内容骨架",
      before: briefListSummary(before.outline, "阶段"),
      after: briefListSummary(after.outline, "阶段"),
    });
  }

  addTextChange("scopeEstimate", "预计规模");
  addTextChange("riskNotes", "风险提示");

  const constraintSnapshot = (brief: IntakeBrief) =>
    JSON.stringify(
      brief.constraints.map(({ key, statement, strength }) => ({
        key,
        statement: statement.trim(),
        strength,
      })),
    );
  if (constraintSnapshot(before) !== constraintSnapshot(after)) {
    changes.push({
      key: "constraints",
      label: "创作约束",
      before: briefConstraintSummary(before),
      after: briefConstraintSummary(after),
    });
  }
  return changes;
}

export function IntakeCenter() {
  const captureOperation = useSessionUiOperation();
  const {
    state,
    patchState,
    confirmBriefAndContinue,
    submitPolish,
    adoptPolish: adoptPolishDraft,
    continueToQuestions: proceedToQuestions,
    generateMoreQuestions: requestMoreQuestions,
    generateBriefFromAnswers: synthesizeBriefFromServer,
    generateAuthorAnswer,
    saveCandidateAsNew: saveCandidateToServer,
    createDialogueRevision: createDialogueRevisionFromServer,
    activateCandidate,
    beginBriefRevision,
    resetSession: resetSessionState,
    loadProject,
    stashCurrentSession,
    restoreStashedSession,
    hasStashedSession,
    activeProjectId,
    retryTask,
  } = useCaseSession();
  const {
    step,
    furthestStep,
    sourceText,
    polishMode,
    answers,
    brief,
    briefCandidates: candidates,
    currentBriefCandidateId: currentCandidateId,
  } = state;
  const { hydration, latestTasks } = state;
  const [polishReviewOpen, setPolishReviewOpen] = useState(false);
  const [polishPending, setPolishPending] = useState(false);
  const [questionGenerationMode, setQuestionGenerationMode] = useState<
    "initial" | "additional" | null
  >(null);
  const [questionPageIndex, setQuestionPageIndex] = useState(0);
  const [questionBatchStartIndex, setQuestionBatchStartIndex] = useState(0);
  const [briefGenerationPending, setBriefGenerationPending] = useState(false);
  const [authorAnswerSuggestion, setAuthorAnswerSuggestion] = useState<string | null>(
    null,
  );
  const [authorAnswerPending, setAuthorAnswerPending] = useState(false);
  const [authorAnswerError, setAuthorAnswerError] = useState<string | null>(null);
  const [polishDraft, setPolishDraft] = useState("");
  const [polishNotes, setPolishNotes] = useState<string[]>([]);
  const [introducedDetails, setIntroducedDetails] = useState<string[]>([]);
  const [polishParentSourceRecordId, setPolishParentSourceRecordId] =
    useState<number | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyDrawerOpen, setHistoryDrawerOpen] = useState(false);
  const [revisionInstruction, setRevisionInstruction] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [retryingTaskType, setRetryingTaskType] = useState<
    Parameters<typeof retryTask>[0] | null
  >(null);
  const [announcement, setAnnouncement] = useState("");
  const [sourceDirty, setSourceDirty] = useState(false);
  const [answersDirty, setAnswersDirty] = useState(false);
  const [briefDirty, setBriefDirty] = useState(false);
  const [questionGenerationFailed, setQuestionGenerationFailed] =
    useState(false);
  const [candidateSavePending, setCandidateSavePending] = useState(false);
  const [dialogueRevisionPending, setDialogueRevisionPending] =
    useState(false);
  const [dialogueRevisionReceipt, setDialogueRevisionReceipt] =
    useState<DialogueRevisionReceipt | null>(null);
  const [dependencyInvalidation, setDependencyInvalidation] =
    useState<IntakeDependencyInvalidation>({
      projectId: null,
      questions: false,
      brief: false,
      changedAnswerKeys: [],
    });
  const [confirmReturnToIdea, setConfirmReturnToIdea] = useState(false);
  const [briefRevisionDialogOpen, setBriefRevisionDialogOpen] = useState(false);
  const [briefRevisionPending, setBriefRevisionPending] = useState(false);
  const [briefConfirmationPhase, setBriefConfirmationPhase] =
    useState<BriefConfirmationPhase>("idle");
  const [briefConfirmationIssue, setBriefConfirmationIssue] =
    useState<BriefConfirmationIssue | null>(null);
  const [resolutionDecision, setResolutionDecision] =
    useState<BriefResolutionDecision | null>(null);
  const [confirmationRevisionConflict, setConfirmationRevisionConflict] =
    useState(false);
  const confirmationIssueRef = useRef<HTMLElement | null>(null);
  const briefConfirmationInFlight = useRef(false);
  const [confirmingExample, setConfirmingExample] = useState(false);

  // ── 当前建案路径（A/B/C）：决定路由高亮与步骤归属 ──────────────────
  const [activePath, setActivePath] = useState<"A" | "B" | "C">("A");
  const [showLanding, setShowLanding] = useState(() => {
    if (typeof window === "undefined") return true;
    return !new URLSearchParams(window.location.search).has("project");
  });
  const [landingEntranceActive, setLandingEntranceActive] = useState(() => {
    if (typeof window === "undefined") return true;
    return !new URLSearchParams(window.location.search).has("project");
  });
  const [landingHistory, setLandingHistory] = useState<CaseHistoryEntry[] | null>(null);
  const [landingHistoryLoading, setLandingHistoryLoading] = useState(true);

  // ── Path B: Idea Generation ───────────────────────────────────────────
  const [showIdeaGeneration, setShowIdeaGeneration] = useState(false);

  // ── Path C: Reverse Parse ────────────────────────────────────────────
  const [showReverseParse, setShowReverseParse] = useState(false);
  const {
    ideaCandidates, pastBatches, ideaGenerating, regeneratingIds,
    enterPathB, generateAll, handleSelectIdea, handleBookmarkIdea,
    handleArchiveIdea, handleRegenerateIdea, resetIdeas, clearIdeaPending,
  } = useIdeaCandidates({
    activeProjectId, hydrating: hydration.status === "loading",
    loadProject: (projectId) => { clearPendingOperations(); return loadProject(projectId); },
    setActivePath, setShowIdeaGeneration, setShowReverseParse, setError,
  });

  useEffect(() => {
    if (!showLanding) return;
    let cancelled = false;
    void loadCaseHistoryEntries()
      .then((entries) => {
        if (!cancelled) setLandingHistory(entries);
      })
      .catch(() => {
        if (!cancelled) setLandingHistory([]);
      })
      .finally(() => {
        if (!cancelled) setLandingHistoryLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [showLanding]);


  useEffect(() => {
    if (!briefDirty) return;
    const warnBeforeLeaving = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warnBeforeLeaving);
    return () => window.removeEventListener("beforeunload", warnBeforeLeaving);
  }, [briefDirty]);

  useEffect(() => {
    if (briefConfirmationPhase !== "needs_input") return;
    if (briefConfirmationIssue?.kind === "missing_field") {
      const selectors: Record<BriefConfirmationField, string> = {
        concept: '[aria-label="一句话概念"]',
        reasoningGoal: '[aria-label="推理目标"]',
        conclusionMode: 'input[name="intake-conclusion-mode"]',
        authorAnswer: '[aria-label="作者答案"]',
      };
      document
        .querySelector<HTMLElement>(selectors[briefConfirmationIssue.field])
        ?.focus();
      return;
    }
    confirmationIssueRef.current?.focus();
  }, [briefConfirmationPhase, briefConfirmationIssue]);

  useEffect(() => {
    if (briefConfirmationPhase !== "success") return;
    const transitionTimer = window.setTimeout(() => {
      patchState({ step: "candidates" });
      setBriefConfirmationPhase("idle");
      setAnnouncement("建案完成，已进入深稿候选阶段。");
    }, 700);
    return () => window.clearTimeout(transitionTimer);
  }, [briefConfirmationPhase, patchState]);

  function resolveState<T>(current: T, next: SetStateAction<T>) {
    return typeof next === "function"
      ? (next as (value: T) => T)(current)
      : next;
  }

  const setStep = (next: SetStateAction<IntakeStep>) =>
    patchState({ step: resolveState(step, next) });
  const setSourceText = (next: SetStateAction<string>) =>
    patchState({ sourceText: resolveState(sourceText, next) });
  const setPolishMode = (next: SetStateAction<IntakePolishMode>) =>
    patchState({ polishMode: resolveState(polishMode, next) });
  const setAnswers = (
    next: SetStateAction<Record<string, IntakeAnswer>>,
  ) => patchState({ answers: resolveState(answers, next) });
  const setBrief = (next: SetStateAction<IntakeBrief>) =>
    patchState({ brief: resolveState(brief, next) });

  const stepIndex = intakeSteps.findIndex((item) => item.id === step);
  const questionsPending = questionGenerationMode !== null;
  const candidateHistoryVersionById = useMemo(
    () => candidateHistoryVersions(candidates),
    [candidates],
  );
  const firstUnresolvedQuestionIndex = state.questions.findIndex((question) => {
    const answer = answers[question.key];
    return question.required && !(answer?.text.trim() && !answer.pending);
  });
  const hardQuestionsResolved = firstUnresolvedQuestionIndex === -1;
  // 新批次不得隐藏仍被成案门禁要求的旧题；保留固定起点，避免作答后题目跳走。
  const nextQuestionBatchStartIndex = hardQuestionsResolved
    ? state.questions.length
    : firstUnresolvedQuestionIndex;
  const questionsPhaseReached = furthestStep >= 1;
  const questionsComplete = !questionsPhaseReached
    ? false
    : state.questions.length === 0
      ? true
      : hardQuestionsResolved;
  const missingFields = missingHardFields(brief);
  const latestTaskList = Object.values(latestTasks).filter(Boolean);
  const activeRecoveryTasks = latestTaskList.filter((task) =>
    task && ["queued", "running", "cancelling"].includes(task.status),
  );
  const failedRecoveryTasks = latestTaskList.filter(
    (task) => task && task.status === "failed",
  );
  const completionSignals = useMemo(
    () => [
      {
        label: "起案原文",
        ready: Boolean(sourceText.trim()),
        value: sourceText.trim() ? "已记录" : "等待输入",
      },
      {
        label: "关键问题",
        ready: questionsComplete,
        value: !questionsPhaseReached
          ? "尚未生成"
          : state.questions.length === 0
            ? "无需追问"
            : hardQuestionsResolved
              ? "已回答"
              : "尚未锁定",
      },
      {
        label: "创作简报",
        ready: state.frozenBriefVersion !== null,
        value: state.frozenBriefVersion
          ? "已冻结"
          : candidates.length
            ? candidates.length + " 个候选"
            : "尚未形成",
      },
      {
        label: "当前工作稿",
        ready: Boolean(state.adoptedCandidateId),
        value: state.adoptedCandidateId ? "已采用" : "尚未采用",
      },
    ],
    [
      candidates.length,
      hardQuestionsResolved,
      questionsComplete,
      questionsPhaseReached,
      sourceText,
      state.adoptedCandidateId,
      state.frozenBriefVersion,
      state.questions.length,
    ],
  );

  const completionCount = completionSignals.filter((signal) => signal.ready).length;

  function announce(message: string) {
    setAnnouncement(message);
  }

  function markSourceDependenciesStale() {
    if (furthestStep < 1) return;
    setDependencyInvalidation({
      projectId: activeProjectId,
      questions: true,
      brief: furthestStep >= 2,
      changedAnswerKeys: [],
    });
  }

  function markBriefDependencyStale(questionKey: string) {
    if (furthestStep < 2) return;
    setDependencyInvalidation((current) => {
      const scoped =
        current.projectId === activeProjectId
          ? current
          : {
              projectId: activeProjectId,
              questions: false,
              brief: false,
              changedAnswerKeys: [],
            };
      return {
        ...scoped,
        brief: true,
        changedAnswerKeys: scoped.changedAnswerKeys.includes(questionKey)
          ? scoped.changedAnswerKeys
          : [...scoped.changedAnswerKeys, questionKey],
      };
    });
  }

  function clearDependencyInvalidation() {
    setDependencyInvalidation({
      projectId: activeProjectId,
      questions: false,
      brief: false,
      changedAnswerKeys: [],
    });
  }

  function openReachableStep(target: IntakeStep) {
    const targetIndex = intakeSteps.findIndex((item) => item.id === target);
    if (targetIndex < 0 || targetIndex > furthestStep) return;
    if (target === "candidates" && state.frozenBriefVersion === null) {
      setError("先确认并冻结创作简报，再生成深稿候选。");
      return;
    }
    if (
      target === "idea" &&
      furthestStep >= 2 &&
      state.frozenBriefVersion === null
    ) {
      setConfirmReturnToIdea(true);
      setError(null);
      return;
    }
    // 步骤条不能绕过持久化动作：上游改动未落库时阻止前跳。
    if (
      step === "idea" &&
      sourceDirty &&
      targetIndex > stepIndex
    ) {
      setError("最初想法尚未保存，请先完成新的关键追问研查。");
      return;
    }
    if (
      step === "questions" &&
      answersDirty &&
      targetIndex > stepIndex
    ) {
      setError(
        currentDependencyInvalidation.brief
          ? "新的判断尚未并入简报，请点击“更新建案简报”。"
          : "回答尚未并入创作简报，请点击“形成创作简报”。",
      );
      return;
    }
    if (
      step === "confirmation" &&
      briefDirty &&
      targetIndex > stepIndex
    ) {
      setError("创作简报有未确认修改，请先点击“确认建案并继续”。");
      return;
    }
    if (target !== "confirmation") {
      setAuthorAnswerPending(false);
      setAuthorAnswerSuggestion(null);
      setAuthorAnswerError(null);
    }
    // 步骤条切换始终退出 B/C 入口界面，只显示目标步骤自身。
    setShowIdeaGeneration(false);
    setShowReverseParse(false);
    setStep(target);
    setError(null);
    announce("已切换到" + intakeSteps[targetIndex].label + "。");
  }

  function returnToIdeaStep() {
    setConfirmReturnToIdea(false);
    setAuthorAnswerPending(false);
    setAuthorAnswerSuggestion(null);
    setAuthorAnswerError(null);
    setShowIdeaGeneration(false);
    setShowReverseParse(false);
    setStep("idea");
    setError(null);
    announce("已返回最初想法；已有问答、简报和候选仍然保留。");
  }

  function openLandingRoute(code: "A" | "B" | "C") {
    setLandingEntranceActive(false);
    setShowLanding(false);
    setError(null);
    if (code === "B") {
      void enterPathB();
      return;
    }
    setActivePath(code);
    setShowIdeaGeneration(false);
    setShowReverseParse(code === "C");
    if (code === "A") setStep("idea");
    announce(code === "A" ? "已进入想法记录。" : "已进入现有内容导入。");
  }

  function loadExample() {
    setSourceText(sampleIdea);
    setSourceDirty(true);
    markSourceDependenciesStale();
    setConfirmingExample(false);
    setPolishReviewOpen(false);
    setError(null);
    announce("示例想法已载入，可以继续编辑。");
  }

  function requestExample() {
    if (!sourceText.trim()) {
      loadExample();
      return;
    }
    setConfirmingExample(true);
    setError(null);
  }

  async function startPolishReview() {
    const isCurrent = captureOperation();
    if (!sourceText.trim()) {
      setError("先写下一句最初想法，再生成润色校样。");
      return;
    }
    setPolishReviewOpen(true);
    setPolishPending(true);
    setPolishDraft("");
    setPolishNotes([]);
    setIntroducedDetails([]);
    setError(null);
    try {
      const result = await submitPolish(polishMode);
      if (!isCurrent()) return;
      setPolishDraft(result.text);
      setPolishNotes(result.notes);
      setIntroducedDetails(result.introducedDetails);
      setPolishParentSourceRecordId(result.parentSourceRecordId);
      setSourceDirty(false);
      announce("润色校样已形成，原文仍保持不变。");
    } catch (caught) {
      if (!isCurrent()) return;
      setPolishReviewOpen(false);
      setError(caught instanceof Error ? caught.message : "润色任务未完成。");
    } finally {
      if (isCurrent()) {
        setPolishPending(false);
      }
    }
  }

  async function adoptPolish() {
    const isCurrent = captureOperation();
    if (!polishDraft.trim()) return;
    setError(null);
    try {
      await adoptPolishDraft(
        polishDraft.trim(),
        polishParentSourceRecordId,
      );
      if (!isCurrent()) return;
      setSourceText(polishDraft.trim());
      setSourceDirty(false);
      markSourceDependenciesStale();
      setPolishReviewOpen(false);
      announce("已采用润色稿，原始版本仍可在来源记录中追溯。");
    } catch (caught) {
      if (!isCurrent()) return;
      setError(caught instanceof Error ? caught.message : "采用润色稿失败。");
    }
  }

  async function continueToQuestions() {
    const isCurrent = captureOperation();
    if (!sourceText.trim()) {
      setError("请先写下最初想法。");
      return;
    }
    setError(null);
    setQuestionGenerationFailed(false);
    const isDependencyRefresh = currentDependencyInvalidation.questions;
    const firstRefreshedQuestionIndex = isDependencyRefresh
      ? nextQuestionBatchStartIndex
      : 0;
    setQuestionGenerationMode(isDependencyRefresh ? "additional" : "initial");
    setQuestionPageIndex(0);
    setQuestionBatchStartIndex(0);
    setStep("questions");
    try {
      if (isDependencyRefresh) {
        await requestMoreQuestions();
        if (!isCurrent()) return;
      } else {
        await proceedToQuestions();
        if (!isCurrent()) return;
      }
      setSourceDirty(false);
      setAnswersDirty(false);
      if (isDependencyRefresh) {
        setQuestionBatchStartIndex(firstRefreshedQuestionIndex);
        setDependencyInvalidation({
          projectId: activeProjectId,
          questions: false,
          brief: currentDependencyInvalidation.brief,
          changedAnswerKeys: [],
        });
        announce("起案变化已重新研查；原有内容保留，创作简报等待更新。");
      } else {
        announce("起案原文已记录，进入关键追问。");
      }
    } catch (caught) {
      if (!isCurrent()) return;
      setQuestionGenerationFailed(true);
      setError(caught instanceof Error ? caught.message : "追问任务未完成。");
    } finally {
      if (isCurrent()) {
        setQuestionGenerationMode(null);
      }
    }
  }

  async function generateMoreQuestions() {
    const isCurrent = captureOperation();
    if (questionsPending) return;
    setError(null);
    setQuestionGenerationMode("additional");
    const firstNewQuestionIndex = nextQuestionBatchStartIndex;
    try {
      await requestMoreQuestions();
      if (!isCurrent()) return;
      // 已回答的旧题只作为上下文；未完成的必答题继续保留在可见批次中。
      setQuestionBatchStartIndex(firstNewQuestionIndex);
      setQuestionPageIndex(0);
      setAnswersDirty(false);
      announce(hardQuestionsResolved
        ? "补充研查已完成；当前只显示本轮新增问题，已有回答保持不变。"
        : "补充研查已完成；请先完成保留的必答问题，已有回答保持不变。");
    } catch (caught) {
      if (!isCurrent()) return;
      setError(caught instanceof Error ? caught.message : "补充追问任务未完成。");
    } finally {
      if (isCurrent()) {
        setQuestionGenerationMode(null);
      }
    }
  }

  function updateAnswer(
    questionKey: string,
    text: string,
    source: FieldSource = "user_confirmed",
  ) {
    setAnswers((current) => ({
      ...current,
      [questionKey]: { text, source, pending: false },
    }));
    setAnswersDirty(true);
    markBriefDependencyStale(questionKey);
    setError(null);
  }

  function markQuestionPending(questionKey: string) {
    setAnswers((current) => ({
      ...current,
      [questionKey]: {
        text: "稍后决定",
        source: "unresolved",
        pending: true,
      },
    }));
    setAnswersDirty(true);
    markBriefDependencyStale(questionKey);
    announce("这项偏好已放入待决定队列，不会阻止继续。");
  }

  async function generateBrief() {
    const isCurrent = captureOperation();
    if (!hardQuestionsResolved) {
      setError("必须先回答关键问题，才能形成创作简报。");
      return;
    }
    if (questionGenerationFailed) {
      setError("关键追问尚未生成，请返回原稿后重试。");
      return;
    }
    setError(null);
    setAuthorAnswerSuggestion(null);
    setAuthorAnswerError(null);
    setBriefGenerationPending(true);
    setStep("confirmation");
    try {
      await synthesizeBriefFromServer();
      if (!isCurrent()) return;
      setAnswersDirty(false);
      setBriefDirty(false);
      clearDependencyInvalidation();
      announce("创作简报候选已形成，请逐项校核后采用。");
    } catch (caught) {
      if (!isCurrent()) return;
      setStep("questions");
      setError(caught instanceof Error ? caught.message : "创作简报生成未完成。");
    } finally {
      if (isCurrent()) {
        setBriefGenerationPending(false);
      }
    }
  }

  function updateBriefField(field: BriefTextField, value: string) {
    setBrief((current) => ({
      ...current,
      [field]: value,
      sources: {
        ...current.sources,
        [field]: "user_confirmed",
      },
    }));
    setBriefDirty(true);
    if (field === "authorAnswer") {
      setAuthorAnswerSuggestion(null);
      setAuthorAnswerError(null);
    }
    setError(null);
  }

  function updateResolutionMode(value: ResolutionMode) {
    setAuthorAnswerSuggestion(null);
    setAuthorAnswerError(null);
    setBrief((current) => ({
      ...current,
      resolutionMode: value,
      authorAnswer: value === "author_anchored" ? current.authorAnswer : "",
      sources: {
        ...current.sources,
        resolutionMode: "user_confirmed",
        authorAnswer:
          value === "author_anchored"
            ? current.sources.authorAnswer
            : "unresolved",
      },
    }));
    setBriefDirty(true);
    setError(null);
  }

  async function generateAuthorAnswerSuggestion() {
    const isCurrent = captureOperation();
    setAuthorAnswerPending(true);
    setAuthorAnswerSuggestion(null);
    setAuthorAnswerError(null);
    setError(null);
    try {
      const suggestion = await generateAuthorAnswer(brief);
      if (!isCurrent()) return;
      setAuthorAnswerSuggestion(suggestion);
      announce("Agent 只提供了一版答案候选；你可以采用、改写，或直接写自己的结论。");
    } catch (caught) {
      if (!isCurrent()) return;
      setAuthorAnswerError(
        caught instanceof Error
          ? caught.message
          : "答案候选生成未完成，请直接填写你的结论。",
      );
    } finally {
      if (isCurrent()) {
        setAuthorAnswerPending(false);
      }
    }
  }

  function adoptAuthorAnswerSuggestion() {
    if (!authorAnswerSuggestion) return;
    updateBriefField("authorAnswer", authorAnswerSuggestion);
    setAuthorAnswerSuggestion(null);
    announce("已把 Agent 候选放入简报草案；确认建案前仍可继续改写。 ");
  }

  function updateConclusionMode(value: ConclusionMode) {
    setBrief((current) => ({
      ...current,
      conclusionMode: value,
      sources: {
        ...current.sources,
        conclusionMode: "user_confirmed",
      },
    }));
    setBriefDirty(true);
    setError(null);
  }

  function updateConstraint(
    constraintKey: string,
    patch: Partial<Pick<IntakeConstraint, "statement" | "strength">>,
  ) {
    setBrief((current) => ({
      ...current,
      constraints: current.constraints.map((constraint) =>
        constraint.key === constraintKey
          ? { ...constraint, ...patch }
          : constraint,
      ),
    }));
    setBriefDirty(true);
    setError(null);
  }

  async function saveCandidate() {
    const isCurrent = captureOperation();
    if (candidateSavePending) return;
    if (missingFields.length) {
      setError("保存前请补齐：" + missingFields.join("、") + "。");
      return;
    }
    setCandidateSavePending(true);
    setError(null);
    try {
      await saveCandidateToServer();
      if (!isCurrent()) return;
      setBriefDirty(false);
      announce("已保存为新的独立候选，旧版本没有被覆盖。");
    } catch (caught) {
      if (!isCurrent()) return;
      setError(caught instanceof Error ? caught.message : "候选保存失败。");
    } finally {
      if (isCurrent()) {
        setCandidateSavePending(false);
      }
    }
  }

  async function createDialogueRevision() {
    const isCurrent = captureOperation();
    if (dialogueRevisionPending) return;
    const instruction = revisionInstruction.trim();
    if (!instruction) {
      setError("请先写下这一轮要修改的内容。");
      return;
    }
    setDialogueRevisionPending(true);
    setDialogueRevisionReceipt(null);
    setError(null);
    try {
      const result = await createDialogueRevisionFromServer(instruction);
      if (!isCurrent()) return;
      const changes = describeBriefRevision(
        result.baseBrief,
        result.candidate.brief,
      );
      setRevisionInstruction("");
      setBriefDirty(false);
      setDialogueRevisionReceipt({
        projectId: activeProjectId,
        instruction,
        candidateLabel: result.candidate.label,
        changes,
      });
      announce(
        changes.length
          ? `Agent 已形成修改候选，本轮调整：${changes
              .map((change) => change.label)
              .join("、")}。`
          : "Agent 已形成修改候选；未发现字段内容变化。",
      );
    } catch (caught) {
      if (!isCurrent()) return;
      setError(caught instanceof Error ? caught.message : "对话修改未完成。");
    } finally {
      if (isCurrent()) {
        setDialogueRevisionPending(false);
      }
    }
  }

  async function restoreCandidate(candidate: BriefCandidate) {
    const isCurrent = captureOperation();
    setError(null);
    try {
      await activateCandidate(candidate.id);
      if (!isCurrent()) return;
      setBriefDirty(false);
      announce("已恢复" + candidate.label + "。");
    } catch (caught) {
      if (!isCurrent()) return;
      setError(caught instanceof Error ? caught.message : "候选恢复失败。");
    }
  }

  function focusBriefField(field: BriefConfirmationField) {
    const isCurrent = captureOperation();
    const selectors: Record<BriefConfirmationField, string> = {
      concept: '[aria-label="一句话概念"]',
      reasoningGoal: '[aria-label="推理目标"]',
      conclusionMode: 'input[name="intake-conclusion-mode"]',
      authorAnswer: '[aria-label="作者答案"]',
    };
    window.setTimeout(() => {
      if (!isCurrent()) return;
      document.querySelector<HTMLElement>(selectors[field])?.focus();
    }, 0);
  }

  async function runBriefConfirmation(draft: IntakeBrief) {
    const isCurrent = captureOperation();
    if (briefConfirmationInFlight.current) return;
    briefConfirmationInFlight.current = true;
    setBriefConfirmationPhase("processing");
    setBriefConfirmationIssue(null);
    setConfirmationRevisionConflict(false);
    setError(null);
    try {
      await confirmBriefAndContinue(draft);
      if (!isCurrent()) return;
      setBriefDirty(false);
      setAuthorAnswerPending(false);
      setAuthorAnswerSuggestion(null);
      setAuthorAnswerError(null);
      setBriefConfirmationPhase("success");
    } catch (caught) {
      if (!isCurrent()) return;
      setBriefConfirmationPhase("failed");
      setConfirmationRevisionConflict(
        isBriefConfirmationRevisionConflict(caught),
      );
      setError(caught instanceof Error ? caught.message : "建案确认失败，请重试。");
    } finally {
      if (isCurrent()) {
        briefConfirmationInFlight.current = false;
      }
    }
  }

  function requestBriefConfirmation(draft: IntakeBrief = brief) {
    const issue = firstBriefConfirmationIssue(draft);
    if (issue) {
      setBriefConfirmationIssue(issue);
      setResolutionDecision(null);
      setBriefConfirmationPhase("needs_input");
      setError(null);
      return;
    }
    void runBriefConfirmation(draft);
  }

  function applyResolutionDecision() {
    if (!resolutionDecision || !briefConfirmationIssue) return;
    const next = applyBriefResolutionDecision(brief, resolutionDecision);
    setBrief(next);
    setBriefDirty(true);
    setBriefConfirmationIssue(null);
    setResolutionDecision(null);

    if (
      resolutionDecision === "author_anchored" &&
      !next.authorAnswer.trim()
    ) {
      setBriefConfirmationPhase("idle");
      focusBriefField("authorAnswer");
      announce("请填写作者答案，再确认建案。");
      return;
    }
    requestBriefConfirmation(next);
  }

  async function reopenFrozenBrief() {
    const isCurrent = captureOperation();
    if (briefRevisionPending) return;
    setBriefRevisionDialogOpen(false);
    setBriefRevisionPending(true);
    try {
      await beginBriefRevision();
      if (!isCurrent()) return;
      setBriefConfirmationPhase("idle");
      setBriefConfirmationIssue(null);
      setResolutionDecision(null);
      setBriefDirty(false);
      clearDependencyInvalidation();
      setError(null);
      announce("已从冻结版本建立新的可编辑简报修订。");
    } catch (caught) {
      if (!isCurrent()) return;
      setError(caught instanceof Error ? caught.message : "建立简报修订失败。");
    } finally {
      if (isCurrent()) {
        setBriefRevisionPending(false);
      }
    }
  }

  async function reloadLatestBrief() {
    if (activeProjectId === null) return;
    clearPendingOperations();
    const loading = loadProject(activeProjectId);
    const isCurrent = captureOperation();
    try {
      await loading;
      if (!isCurrent()) return;
      setBriefDirty(false);
      setBriefConfirmationPhase("idle");
      setBriefConfirmationIssue(null);
      setConfirmationRevisionConflict(false);
      setError(null);
      announce("已载入服务端最新的创作简报。");
    } catch (caught) {
      if (!isCurrent()) return;
      setError(caught instanceof Error ? caught.message : "载入最新版本失败。");
    }
  }

  function clearPendingOperations() {
    setPolishPending(false);
    setQuestionGenerationMode(null);
    setBriefGenerationPending(false);
    setAuthorAnswerPending(false);
    setCandidateSavePending(false);
    setDialogueRevisionPending(false);
    setBriefRevisionPending(false);
    setRetryingTaskType(null);
    briefConfirmationInFlight.current = false;
    clearIdeaPending();
  }

  function resetSession() {
    clearPendingOperations();
    resetSessionState();
    setPolishReviewOpen(false);
    setQuestionPageIndex(0);
    setQuestionBatchStartIndex(0);
    setQuestionGenerationFailed(false);
    setAuthorAnswerSuggestion(null);
    setAuthorAnswerError(null);
    setPolishDraft("");
    setPolishNotes([]);
    setIntroducedDetails([]);
    setPolishParentSourceRecordId(null);
    setHistoryOpen(false);
    setHistoryDrawerOpen(false);
    setRevisionInstruction("");
    setSourceDirty(false);
    setAnswersDirty(false);
    setBriefDirty(false);
    setBriefConfirmationPhase("idle");
    setBriefConfirmationIssue(null);
    setResolutionDecision(null);
    setConfirmationRevisionConflict(false);
    setConfirmReturnToIdea(false);
    setBriefRevisionDialogOpen(false);
    setConfirmingExample(false);
    setActivePath("A");
    setShowLanding(true);
    setShowIdeaGeneration(false);
    setShowReverseParse(false);
    resetIdeas();
    setError(null);
    announce("已恢复未建案状态；后续操作将创建新项目。");
  }

  async function restoreProject(projectId: number) {
    clearPendingOperations();
    stashCurrentSession();
    // 历史恢复统一回到路径 A 的步骤视图，退出 B/C 入口界面。
    setActivePath("A");
    setShowIdeaGeneration(false);
    setShowReverseParse(false);
    setQuestionBatchStartIndex(0);
    setQuestionPageIndex(0);
    const loading = loadProject(projectId);
    const isCurrent = captureOperation();
    try {
      await loading;
      if (!isCurrent()) return;
      setSourceDirty(false);
      setAnswersDirty(false);
      setBriefDirty(false);
      clearDependencyInvalidation();
      setBriefConfirmationPhase("idle");
      setBriefConfirmationIssue(null);
      setResolutionDecision(null);
      setConfirmationRevisionConflict(false);
      setQuestionGenerationFailed(false);
      setConfirmReturnToIdea(false);
      setBriefRevisionDialogOpen(false);
      setBriefRevisionPending(false);
      setHistoryDrawerOpen(false);
      setLandingEntranceActive(false);
      setShowLanding(false);
      setError(null);
      announce("已恢复该卷宗；服务端状态已重新同步。");
    } catch (caught) {
      if (!isCurrent()) return;
      resetSession();
      setHistoryDrawerOpen(false);
      setError(caught instanceof Error ? caught.message : "卷宗恢复失败，请重试。");
    }
  }

  async function retryLatestTask(taskType: Parameters<typeof retryTask>[0]) {
    const isCurrent = captureOperation();
    if (retryingTaskType) return;
    setRetryingTaskType(taskType);
    setError(null);
    try {
      const polished = await retryTask(taskType);
      if (!isCurrent()) return;
      if (polished) {
        setPolishDraft(polished.text);
        setPolishNotes(polished.notes);
        setIntroducedDetails(polished.introducedDetails);
        setPolishParentSourceRecordId(polished.parentSourceRecordId);
        setPolishReviewOpen(true);
      }
      announce("任务已重新提交；页面会继续显示最新执行状态。");
    } catch (caught) {
      if (!isCurrent()) return;
      setError(caught instanceof Error ? caught.message : "任务重试失败，请稍后再试。");
    } finally {
      if (isCurrent()) {
        setRetryingTaskType(null);
      }
    }
  }

  function restoreStashed() {
    clearPendingOperations();
    restoreStashedSession();
    setLandingEntranceActive(false);
    setShowLanding(false);
    setSourceDirty(false);
    setAnswersDirty(false);
    setBriefDirty(false);
    setBriefConfirmationPhase("idle");
    setBriefConfirmationIssue(null);
    setResolutionDecision(null);
    setConfirmationRevisionConflict(false);
    setQuestionGenerationFailed(false);
    setError(null);
    announce("已回到暂存的卷宗。");
  }

  const selectedResolutionMode = resolutionModes.find(
    (mode) => mode.value === brief.resolutionMode,
  );
  const activeConstraintCount = brief.constraints.filter((constraint) =>
    constraint.statement.trim(),
  ).length;
  const currentDependencyInvalidation =
    dependencyInvalidation.projectId === activeProjectId
      ? dependencyInvalidation
      : {
          projectId: activeProjectId,
          questions: false,
          brief: false,
          changedAnswerKeys: [],
        };
  const visibleDialogueRevisionReceipt =
    dialogueRevisionReceipt?.projectId === activeProjectId
      ? dialogueRevisionReceipt
      : null;
  const dialogueChangedFields = new Set(
    visibleDialogueRevisionReceipt?.changes.map((change) => change.key) ?? [],
  );
  const intakeFrozen = state.frozenBriefVersion !== null;
  // B/C 路径处于各自入口界面时，主工作区只渲染入口，不渲染 01–04 步骤视图。
  const inEntryView =
    (activePath === "B" && showIdeaGeneration) ||
    (activePath === "C" && showReverseParse);
  const visibleQuestions =
    questionGenerationMode === "additional"
      ? []
      : state.questions.slice(questionBatchStartIndex);
  const questionCount = visibleQuestions.length;
  const visibleQuestionIndex = Math.min(
    questionPageIndex,
    Math.max(0, questionCount - 1),
  );
  const currentQuestion = visibleQuestions[visibleQuestionIndex] ?? null;
  const currentQuestionAnswer = currentQuestion
    ? answers[currentQuestion.key]
    : undefined;
  const usesContentFitIdeaLayout =
    activePath === "A" && !inEntryView && step === "idea";

  return (
    <div
      className={styles.intakeCenter}
      data-casefile-surface="intake-center-v1"
      data-entrance-motion={showLanding && landingEntranceActive ? "true" : undefined}
      data-intake-view={showLanding ? "landing" : "flow"}
      data-intake-step={step}
    >
      {showLanding && landingEntranceActive ? (
        <div
          aria-hidden="true"
          className={styles.entrancePrologue}
          data-testid="landing-entrance-prologue"
          onAnimationEnd={(event) => {
            if (event.target === event.currentTarget) setLandingEntranceActive(false);
          }}
        >
          <div className={styles.entranceLeaf} data-side="top" />
          <div className={styles.entranceLeaf} data-side="bottom" />
          <div className={styles.entranceScan} />
          <div className={styles.entranceLockup}>
            <span>CASEFILE ARCHIVE / INTAKE DOSSIER</span>
            <div className={styles.entranceSeal}>
              <b>CF</b>
              <i>01</i>
            </div>
            <strong>每个故事，都从一份未解的卷宗开始。</strong>
            <small>FILE OPENED · 建案中心</small>
          </div>
        </div>
      ) : null}

      {showLanding ? (
        <header className={styles.topbar}>
        <div className={styles.brandCell}>
          <Link className={styles.brand} href="/" onClick={() => setShowLanding(true)}>
            <span aria-hidden="true" className={styles.brandMark} />
            <div>
              <strong>CaseFile</strong>
              <small>推理 · 洞察 · 行动</small>
            </div>
            <span className={styles.brandSection}>建案中心</span>
          </Link>
        </div>
        <div aria-hidden="true" className={styles.topbarContext} />
        <nav aria-label="产品页面" className={styles.topbarLinks}>
          <Link href={activeProjectId ? `/workbench?project=${activeProjectId}` : "/workbench"}>
            <span aria-hidden="true" className={styles.analyticsIcon}>⌁</span>
            分析师工作台
          </Link>
          <button
            aria-label="打开建案历史"
            className={styles.historyToggle}
            data-stashed={hasStashedSession || undefined}
            onClick={() => setHistoryDrawerOpen(true)}
            title="建案历史"
            type="button"
          >
            <Glyph name="archive" />
            <span className={styles.topbarActionLabel}>建案历史</span>
            <span aria-hidden="true" className={styles.topbarActionCompactLabel}>
              历史
            </span>
          </button>
          {hasStashedSession ? (
            <button
              aria-label="回到暂存"
              className={styles.stashButton}
              onClick={restoreStashed}
              title="回到暂存"
              type="button"
            >
              <span aria-hidden="true" className={styles.resetIcon}>↩</span>
              <span className={styles.topbarActionLabel}>回到暂存</span>
              <span aria-hidden="true" className={styles.topbarActionCompactLabel}>
                暂存
              </span>
            </button>
          ) : null}
          <button aria-label="重置会话" onClick={resetSession} title="重置会话" type="button">
            <span aria-hidden="true" className={styles.resetIcon}>↻</span>
            <span className={styles.topbarActionLabel}>重置会话</span>
            <span aria-hidden="true" className={styles.topbarActionCompactLabel}>
              重置
            </span>
          </button>
          <button
            aria-label="打开模型服务设置"
            className={styles.settingsButton}
            onClick={() => window.dispatchEvent(new Event("casefile:open-settings"))}
            title="模型服务设置"
            type="button"
          >
            <svg aria-hidden="true" viewBox="0 0 24 24">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-1.86 1.86-.06-.06A1.7 1.7 0 0 0 16 18.4a1.7 1.7 0 0 0-1 .6 1.7 1.7 0 0 0-.4 1.08V20h-5.2v-.08A1.7 1.7 0 0 0 8 18.4a1.7 1.7 0 0 0-1.88.34l-.06.06-1.86-1.86.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-.6-1 1.7 1.7 0 0 0-1.08-.4H2.8V11h.12A1.7 1.7 0 0 0 4.6 9a1.7 1.7 0 0 0-.34-1.88l-.06-.06L6.06 5.2l.06.06A1.7 1.7 0 0 0 8 5.6a1.7 1.7 0 0 0 1-.6 1.7 1.7 0 0 0 .4-1.08V3.8h5.2v.12A1.7 1.7 0 0 0 16 5.6a1.7 1.7 0 0 0 1.88-.34l.06-.06 1.86 1.86-.06.06A1.7 1.7 0 0 0 19.4 9a1.7 1.7 0 0 0 .6 1 1.7 1.7 0 0 0 1.08.4h.12V13h-.12A1.7 1.7 0 0 0 19.4 15Z" />
            </svg>
          </button>
        </nav>
        </header>
      ) : null}

      {showLanding ? (
        <IntakeLanding
          hasRetainedCase={hasStashedSession || intakeFrozen}
          historyEntries={landingHistory}
          historyLoading={landingHistoryLoading}
          onOpenHistory={() => setHistoryDrawerOpen(true)}
          onOpenRoute={openLandingRoute}
          onRestore={(projectId) => void restoreProject(projectId)}
        />
      ) : (
        <>
          <nav aria-label="建案进度" className={styles.pulseTrack}>
        <button
          aria-label="返回首页"
          className={styles.flowBrand}
          onClick={() => setShowLanding(true)}
          type="button"
        >
          <span aria-hidden="true" className={styles.brandMark} />
          <span>
            <strong>CaseFile</strong>
            <small>建案中心</small>
          </span>
        </button>
        <ol>
          {intakeSteps.map((item, index) => {
            const stepLocked =
              index > furthestStep ||
              (item.id === "candidates" && state.frozenBriefVersion === null);
            const stepNeedsUpdate =
              (item.id === "questions" &&
                currentDependencyInvalidation.questions) ||
              (item.id === "confirmation" &&
                currentDependencyInvalidation.brief);
            return (
              <li
                data-active={item.id === step}
                data-complete={
                  !stepNeedsUpdate &&
                  (index < stepIndex ||
                    (item.id === "candidates" && Boolean(state.adoptedCandidateId)))
                }
                data-needs-update={stepNeedsUpdate || undefined}
                data-reachable={!stepLocked}
                key={item.id}
              >
                <button
                  aria-current={item.id === step ? "step" : undefined}
                  aria-label={`${item.no} ${item.shortLabel} ${item.label}${
                    stepNeedsUpdate ? " 需要更新" : ""
                  }`}
                  disabled={stepLocked}
                  onClick={() => openReachableStep(item.id)}
                  type="button"
                >
                  <span>{item.no}</span>
                  <div>
                    <small>{item.shortLabel}</small>
                    <b>{item.label}</b>
                  </div>
                  {stepNeedsUpdate ? (
                    <i className={styles.stepNeedsUpdate} title="需要更新">
                      !
                    </i>
                  ) : index < stepIndex ||
                    (item.id === "candidates" && state.adoptedCandidateId) ? (
                    <i className={styles.stepCheck}>
                      <Glyph name="check" />
                    </i>
                  ) : null}
                </button>
                {item.id === step ? (
                  <i className={styles.activeScan} key={step} />
                ) : null}
              </li>
            );
          })}
        </ol>
        <div className={styles.pulseStatus}>
          <span>{String(completionCount).padStart(2, "0")} / 04</span>
          <small>建案完成度</small>
          {hasStashedSession ? (
            <button onClick={restoreStashed} type="button">回到暂存</button>
          ) : null}
        </div>
          </nav>

          <div className={styles.workspace}>
        {hydration.status === "loading" ? (
          <div aria-live="polite" className={styles.sessionStatus} role="status">
            正在从服务端恢复当前卷宗…
          </div>
        ) : null}
        {hydration.status === "error" ? (
          <div aria-live="assertive" className={styles.sessionStatusError} role="alert">
            {hydration.error ?? "当前卷宗恢复失败，请从建案历史重新调出。"}
          </div>
        ) : null}
        <div aria-live="polite" className={styles.srOnly} role="status">
          {announcement}
        </div>
        {activeRecoveryTasks.length > 0 ? (
          <div aria-live="polite" className={styles.sessionStatus} role="status">
            已恢复 {activeRecoveryTasks.length} 个进行中的任务；返回对应步骤即可继续查看进度。
          </div>
        ) : null}
        {failedRecoveryTasks.length > 0 ? (
          <div aria-live="assertive" className={styles.sessionStatusError} role="alert">
            {failedRecoveryTasks.length} 个任务上次执行失败。请回到对应步骤重新提交，已有输入和候选不会丢失。
            <div className={styles.sessionTaskList}>
              {failedRecoveryTasks.map((task) => (
                <div key={task.task_run_id}>
                  <span>
                    {taskTypeLabel(task.task_type)} · {task.failure?.message ?? "任务失败。"}
                  </span>
                  {task.failure?.retryable ? (
                    <button
                      disabled={retryingTaskType === task.task_type}
                      onClick={() => void retryLatestTask(task.task_type)}
                      type="button"
                    >
                      {retryingTaskType === task.task_type ? "重试中…" : "重试任务"}
                    </button>
                  ) : (
                    <em>请修正输入后重新提交</em>
                  )}
                </div>
              ))}
            </div>
          </div>
        ) : null}
        <main
          className={`${styles.focusPlane} ${
            usesContentFitIdeaLayout ? styles.ideaFocusPlane : ""
          }`}
          data-focus-layout={usesContentFitIdeaLayout ? "content-fit" : "viewport"}
        >
          {activePath === "C" && showReverseParse ? (
            <ReverseParseStage onFormed={() => setShowReverseParse(false)} />
          ) : activePath === "B" && showIdeaGeneration ? (
            <IdeaCandidatesStage
              ideas={ideaCandidates}
              pastBatches={pastBatches}
              generating={ideaGenerating}
              regeneratingIds={regeneratingIds}
              onSelect={handleSelectIdea}
              onBookmark={handleBookmarkIdea}
              onArchive={handleArchiveIdea}
              onRegenerate={handleRegenerateIdea}
              onGenerateAll={generateAll}
            />
          ) : step === "idea" ? (
            <section
              className={`${stageStyles.stepView} ${stageStyles.ideaStep}`}
              aria-labelledby="idea-step-title"
            >
              <header className={stageStyles.stepHero}>
                <div>
                  <h1 id="idea-step-title">
                    把一闪而过的念头，
                    <br />
                    留在故事开始的地方。
                  </h1>
                </div>
                <p>
                  它还不必是一则完整的故事。一个人、一处异样，或一场隐约的冲突，都足以成为第一枚线索。接下来，我们只追问那些真正会让故事转向的问题。
                </p>
              </header>

              {!intakeFrozen && currentDependencyInvalidation.questions ? (
                <section
                  aria-label="下游内容需要更新"
                  className={stageStyles.sourceDependencyNotice}
                  role="status"
                >
                  <span aria-hidden="true">!</span>
                  <div>
                    <b>起案内容已经改变</b>
                    <p>
                      关键追问与创作简报需要重新检查；已有内容、候选和版本都不会丢失。
                    </p>
                  </div>
                </section>
              ) : null}

              {intakeFrozen ? (
                <>
                  <section className={stageStyles.sourceCapsule}>
                    <span>已冻结的最初想法</span>
                    <p>{sourceText || "未记录原文。"}</p>
                    <SourceBadge source="user_original" />
                  </section>
                  <p className={feedbackStyles.frozenNotice} role="status">
                    创作简报已冻结为 V
                    {String(state.frozenBriefVersion ?? 1).padStart(2, "0")}
                    。当前为只读；需要修改时请先建立简报修订。
                  </p>
                  <footer className={stageStyles.stepActions}>
                    <button
                      className={stageStyles.primaryAction}
                      onClick={() => setBriefRevisionDialogOpen(true)}
                      type="button"
                    >
                      修改建案
                    </button>
                    <button
                      className={stageStyles.secondaryAction}
                      onClick={() => openReachableStep("candidates")}
                      type="button"
                    >
                      回到深稿候选
                    </button>
                  </footer>
                </>
              ) : !polishReviewOpen ? (
                <>
                  <section className={stageStyles.ideaCapture}>
                    <div className={stageStyles.captureHeading}>
                      <span>
                        <Glyph name="target" />
                      </span>
                      <div>
                        <b>最初想法</b>
                        <small>你的输入会作为不可替换的原始来源</small>
                      </div>
                      <button
                        className={stageStyles.exampleAction}
                        onClick={requestExample}
                        type="button"
                      >
                        示例范文
                      </button>
                    </div>
                    <textarea
                      aria-label="写下最初想法"
                      onChange={(event) => {
                        setSourceText(event.target.value);
                        setSourceDirty(true);
                        markSourceDependenciesStale();
                        setError(null);
                      }}
                      placeholder="例如：一名档案员发现三份可靠记录，都指向一段不存在的时间……"
                      maxLength={2000}
                      rows={8}
                      value={sourceText}
                    />
                    {confirmingExample ? (
                      <div className={feedbackStyles.exampleConfirm} role="alert">
                        <p>载入示例会替换当前已输入的最初想法。</p>
                        <button onClick={loadExample} type="button">
                          仍要载入
                        </button>
                        <button
                          onClick={() => setConfirmingExample(false)}
                          type="button"
                        >
                          取消
                        </button>
                      </div>
                    ) : null}
                    <footer>
                      <div>
                        <button
                          aria-label="载入示例"
                          onClick={requestExample}
                          type="button"
                        >
                          输入示例
                        </button>
                        <button
                          disabled={!sourceText}
                          onClick={() => {
                            setSourceText("");
                            setSourceDirty(true);
                            markSourceDependenciesStale();
                            setError(null);
                          }}
                          type="button"
                        >
                          清空
                        </button>
                      </div>
                      <span>{sourceText.length} / 2000</span>
                    </footer>
                  </section>

                  <section className={stageStyles.polishControl}>
                    <header>
                      <div>
                        <span>
                          <Glyph name="spark" />
                        </span>
                        <div>
                          <b>需要 Agent 帮你整理表达吗？</b>
                          <small>先生成独立校样，再由你逐字审阅是否采用。</small>
                        </div>
                      </div>
                      <button
                        className={stageStyles.polishTrigger}
                        disabled={!sourceText.trim() || polishPending}
                        onClick={startPolishReview}
                        type="button"
                      >
                        生成润色校样
                      </button>
                    </header>
                    <div className={stageStyles.polishModes}>
                      {polishModes.map((mode) => (
                        <label key={mode.value}>
                          <input
                            checked={polishMode === mode.value}
                            name="intake-polish-mode"
                            onChange={() => setPolishMode(mode.value)}
                            type="radio"
                          />
                          <span>
                            <i aria-hidden="true" className={stageStyles.modeIcon}>
                              {mode.value === "proofread"
                                ? "✦"
                                : mode.value === "rewrite"
                                  ? "⌁"
                                  : "▣"}
                            </i>
                            <b>{mode.label}</b>
                            <small>{mode.hint}</small>
                          </span>
                        </label>
                      ))}
                    </div>
                  </section>
                </>
              ) : (
                <section
                  className={stageStyles.comparisonPanel}
                  aria-labelledby="polish-review-title"
                >
                  <header>
                    <div>
                      <span>
                        <Glyph name="compare" />
                      </span>
                      <div>
                        <small>原稿对校 / 独立润色候选</small>
                        <h2 id="polish-review-title">逐字确认 Agent 改了什么。</h2>
                      </div>
                    </div>
                    <em>
                      {polishModes.find((mode) => mode.value === polishMode)?.label}
                    </em>
                  </header>
                  <div className={stageStyles.comparisonLanes}>
                    <section>
                      <header>
                        <b>原始来源</b>
                        <SourceBadge source="user_original" />
                      </header>
                      <textarea
                        aria-label="当前作者原稿"
                        readOnly
                        rows={8}
                        value={sourceText}
                      />
                    </section>
                    <section>
                      <header>
                        <b>可编辑校样</b>
                        <SourceBadge source="agent_suggestion" />
                      </header>
                      <textarea
                        aria-label="编辑 Agent 润色工作稿"
                        onChange={(event) => setPolishDraft(event.target.value)}
                        rows={8}
                        value={polishDraft}
                      />
                    </section>
                  </div>
                  <div className={stageStyles.auditStrip}>
                    <section>
                      <b>修改说明</b>
                      {polishPending ? (
                        <p>正在生成校样…</p>
                      ) : (
                        <ul>
                          {polishNotes.map((note) => (
                            <li key={note}>{note}</li>
                          ))}
                        </ul>
                      )}
                    </section>
                    <section data-warning={introducedDetails.length > 0}>
                      <b>新增细节审阅</b>
                      {introducedDetails.length ? (
                        <ul>
                          {introducedDetails.map((detail) => (
                            <li key={detail}>{detail}</li>
                          ))}
                        </ul>
                      ) : (
                        <p>本次校样没有新增情节事实。</p>
                      )}
                    </section>
                  </div>
                  <footer className={stageStyles.comparisonActions}>
                    <button
                      onClick={() => setPolishReviewOpen(false)}
                      type="button"
                    >
                      保留原文
                    </button>
                    <button
                      disabled={
                        !polishDraft.trim() ||
                        polishDraft.trim() === sourceText.trim()
                      }
                      onClick={adoptPolish}
                      type="button"
                    >
                      采用这版校样
                      <Glyph name="check" />
                    </button>
                  </footer>
                </section>
              )}

              {error ? (
                <p className={stageStyles.inlineError} role="alert">
                  {error}
                </p>
              ) : null}

              <footer className={stageStyles.stepActions}>
                <button
                  className={stageStyles.primaryAction}
                  disabled={polishReviewOpen || !sourceText.trim()}
                  onClick={continueToQuestions}
                  type="button"
                >
                  {currentDependencyInvalidation.questions
                    ? "重新研查关键追问"
                    : "继续关键追问"}
                  <Glyph name="arrow" />
                </button>
              </footer>
            </section>
          ) : null}

          {!inEntryView && step === "questions" ? (
            <section
              className={`${stageStyles.stepView} ${stageStyles.questionFlow}`}
              aria-labelledby="questions-step-title"
            >
              <header className={stageStyles.questionFlowHero}>
                <div>
                  <h1 id="questions-step-title">沿着疑问的微光，辨认故事的方向。</h1>
                </div>
                <p>
                  一次只确认一个判断；前后切换不会丢失已经选择或写下的回答。
                </p>
              </header>

              {intakeFrozen ? (
                <p className={feedbackStyles.frozenNotice} role="status">
                  创作简报已冻结，回答只读。需要修改时请先建立简报修订。
                </p>
              ) : null}

              {!intakeFrozen && currentDependencyInvalidation.brief ? (
                <section
                  aria-label="创作简报需要更新"
                  className={stageStyles.dependencyNotice}
                  role="status"
                >
                  <span aria-hidden="true">!</span>
                  <div>
                    <b>
                      {currentDependencyInvalidation.changedAnswerKeys.length
                        ? `已修改 ${currentDependencyInvalidation.changedAnswerKeys.length} 个创作判断`
                        : "上游内容已经重新研查"}
                    </b>
                    <p>
                      现有 Brief 与候选不会被删除；下一步会基于新的判断重新整理，未受影响的作者修改继续保留。
                    </p>
                  </div>
                  <button
                    disabled={questionsPending || !hardQuestionsResolved}
                    onClick={generateBrief}
                    type="button"
                  >
                    更新建案简报
                    <Glyph name="arrow" />
                  </button>
                </section>
              ) : null}

              {questionsPending ? (
                <div
                  aria-label={
                    questionGenerationMode === "additional"
                      ? "Agent 正在继续研查"
                      : "Agent 正在思考"
                  }
                  aria-live="polite"
                  className={stageStyles.agentThinking}
                  role="status"
                >
                  <span aria-hidden="true" className={stageStyles.agentThinkingMark}>
                    <i />
                    <i />
                    <i />
                  </span>
                  <div className={stageStyles.agentThinkingCopy}>
                    <strong>
                      {questionGenerationMode === "additional"
                        ? "Agent 正在继续研查"
                        : "Agent 正在思考"}
                    </strong>
                    <p>
                      {questionGenerationMode === "additional"
                        ? "正在避开已问内容，补充新的方向问题……"
                        : "正在从起案原文中提炼会改变方向的关键问题……"}
                    </p>
                    <span
                      aria-hidden="true"
                      className={stageStyles.agentThinkingTrace}
                      data-testid="agent-thinking-motion"
                    >
                      <i />
                      <i />
                      <i />
                      <i />
                      <i />
                    </span>
                  </div>
                </div>
              ) : null}

              {!questionsPending && questionCount === 0 && !error ? (
                <section
                  aria-labelledby="questions-complete-title"
                  aria-live="polite"
                  className={stageStyles.questionsComplete}
                  role="status"
                >
                  <span
                    aria-hidden="true"
                    className={stageStyles.questionsCompleteSweep}
                    data-testid="questions-complete-motion"
                  />
                  <div className={stageStyles.questionsCompleteCopy}>
                    <span className={stageStyles.questionsCompleteEyebrow}>
                      QUESTION REVIEW / COMPLETE
                    </span>
                    <h2 id="questions-complete-title">
                      <span>当前信息</span>
                      <span>已经足够。</span>
                    </h2>
                    <p>
                      Agent 已完成方向缺口研查，没有发现仍需作者确认、且会改变创作方向的问题。
                    </p>
                    <strong>无需追问；可以直接形成创作简报。</strong>
                  </div>
                  <div aria-hidden="true" className={stageStyles.questionsCompleteSeal}>
                    <span>02</span>
                    <Glyph name="check" />
                    <small>研查完成</small>
                  </div>
                  <div className={stageStyles.questionsCompleteMeta}>
                    <span>方向缺口 <b>0</b></span>
                    <span>下一步 <b>03 / 创作简报</b></span>
                  </div>
                </section>
              ) : null}
              {!questionsPending && questionGenerationFailed && !error ? (
                <p className={stageStyles.emptyQuestions}>
                  Agent 未能生成关键追问。请返回原稿后重试。
                </p>
              ) : null}

              {currentQuestion ? (
                <section
                  aria-label={`关键追问 ${visibleQuestionIndex + 1} / ${questionCount}`}
                  className={stageStyles.questionWorkspace}
                >
                  <header className={stageStyles.questionContextBar}>
                    <div>
                      <span>当前起案依据</span>
                      <p>{sourceText}</p>
                    </div>
                    <strong>{visibleQuestionIndex + 1} / {questionCount}</strong>
                  </header>

                  <div className={stageStyles.questionPrompt} key={currentQuestion.key}>
                    <h2>{currentQuestion.prompt}</h2>
                    <p>{currentQuestion.impact}</p>

                    {currentQuestion.suggestions.length > 0 ? (
                      <fieldset
                        aria-label={`选择“${currentQuestion.prompt}”的回答`}
                        className={stageStyles.questionOptions}
                      >
                        {currentQuestion.suggestions.map((suggestion) => (
                          <label
                            data-selected={
                              (!currentQuestionAnswer?.pending &&
                                currentQuestionAnswer?.text === suggestion) ||
                              undefined
                            }
                            key={suggestion}
                          >
                            <input
                              checked={
                                !currentQuestionAnswer?.pending &&
                                currentQuestionAnswer?.text === suggestion
                              }
                              disabled={questionsPending || intakeFrozen}
                              name={`question-suggestion-${currentQuestion.key}`}
                              onChange={() =>
                                updateAnswer(
                                  currentQuestion.key,
                                  suggestion,
                                  "agent_suggestion",
                                )
                              }
                              type="radio"
                            />
                            <span>{suggestion}</span>
                          </label>
                        ))}
                      </fieldset>
                    ) : null}

                    <label className={stageStyles.questionCustomAnswer}>
                      <span>或写下你的补充判断 <small>选填</small></span>
                      <textarea
                        aria-label={`回答：${currentQuestion.prompt}`}
                        disabled={questionsPending || intakeFrozen}
                        onChange={(event) =>
                          updateAnswer(currentQuestion.key, event.target.value)
                        }
                        placeholder="用一句话锁定你的方向……"
                        rows={5}
                        value={
                          currentQuestionAnswer?.pending
                            ? ""
                            : currentQuestionAnswer?.text ?? ""
                        }
                      />
                    </label>
                    <div className={stageStyles.questionAnswerSource}>
                      {currentQuestionAnswer ? (
                        <SourceBadge source={currentQuestionAnswer.source} />
                      ) : (
                        <span>等待你的判断</span>
                      )}
                    </div>
                  </div>

                  <footer className={stageStyles.questionPager}>
                    <button
                      className={stageStyles.secondaryAction}
                      disabled={questionsPending}
                      onClick={() => {
                        if (visibleQuestionIndex === 0) openReachableStep("idea");
                        else setQuestionPageIndex(visibleQuestionIndex - 1);
                      }}
                      type="button"
                    >
                      {visibleQuestionIndex === 0 ? "返回起案" : "← 上一题"}
                    </button>
                    <div aria-label="追问进度" className={stageStyles.questionDots}>
                      {visibleQuestions.map((question, index) => (
                        <button
                          aria-current={index === visibleQuestionIndex ? "step" : undefined}
                          aria-label={`前往第 ${index + 1} 题`}
                          key={question.key}
                          onClick={() => setQuestionPageIndex(index)}
                          type="button"
                        />
                      ))}
                    </div>
                    <div>
                      {!currentQuestion.required ? (
                        <button
                          className={stageStyles.textAction}
                          disabled={questionsPending || intakeFrozen}
                          onClick={() => markQuestionPending(currentQuestion.key)}
                          type="button"
                        >
                          稍后决定
                        </button>
                      ) : null}
                      {visibleQuestionIndex < questionCount - 1 ? (
                        <button
                          className={stageStyles.primaryAction}
                          disabled={
                            questionsPending ||
                            (currentQuestion.required && !currentQuestionAnswer?.text.trim())
                          }
                          onClick={() => setQuestionPageIndex(visibleQuestionIndex + 1)}
                          type="button"
                        >
                          下一题 <Glyph name="arrow" />
                        </button>
                      ) : (
                        <button
                          className={stageStyles.primaryAction}
                          disabled={
                            questionsPending ||
                            questionGenerationFailed ||
                            !hardQuestionsResolved ||
                            intakeFrozen
                          }
                          onClick={generateBrief}
                          type="button"
                        >
                          {intakeFrozen
                            ? "简报已冻结"
                            : currentDependencyInvalidation.brief
                              ? "更新建案简报"
                              : "形成创作简报"}
                          <Glyph name="arrow" />
                        </button>
                      )}
                    </div>
                  </footer>
                </section>
              ) : null}

              {questionCount > 0 ? (
                <section className={stageStyles.questionAuxiliaryActions}>
                  <div><strong>需要再确认一层？</strong><span>已有问题和回答不会被改写。</span></div>
                  <button
                    disabled={questionsPending || intakeFrozen}
                    onClick={generateMoreQuestions}
                    type="button"
                  >
                    {questionGenerationMode === "additional" ? "正在补充问题…" : "再生成一些问题"}
                  </button>
                </section>
              ) : null}

              {error ? (
                <p className={stageStyles.inlineError} role="alert">
                  {error}
                </p>
              ) : null}

              {questionCount === 0 ? (
                <footer className={stageStyles.stepActions}>
                <div>
                  <button
                    className={stageStyles.secondaryAction}
                    disabled={questionsPending}
                    onClick={() => openReachableStep("idea")}
                    type="button"
                  >
                    返回原稿
                  </button>
                  <button
                    className={stageStyles.primaryAction}
                    disabled={
                      questionsPending ||
                      questionGenerationFailed ||
                      !hardQuestionsResolved ||
                      intakeFrozen
                    }
                    onClick={generateBrief}
                    type="button"
                  >
                    {intakeFrozen
                      ? "简报已冻结"
                      : currentDependencyInvalidation.brief
                        ? "更新建案简报"
                        : "形成创作简报"}
                    <Glyph name="arrow" />
                  </button>
                </div>
                </footer>
              ) : null}
            </section>
          ) : null}

          {!inEntryView && step === "confirmation" && briefGenerationPending ? (
            <section
              aria-busy="true"
              aria-labelledby="confirmation-loading-title"
              className={stageStyles.stepView}
            >
              <header className={stageStyles.stepHero}>
                <div>
                  <h1 id="confirmation-loading-title">
                    正在形成可确认的创作简报。
                  </h1>
                </div>
                <p>
                  Agent 正在把原稿与已确认回答整理成可直接确认的创作简报。
                </p>
              </header>

              <div
                aria-label="Agent 正在整理创作简报"
                aria-live="polite"
                className={stageStyles.agentThinking}
                role="status"
              >
                <span
                  aria-hidden="true"
                  className={stageStyles.agentThinkingMark}
                />
                <div>
                  <strong>Agent 正在整理创作简报</strong>
                  <p>正在归纳概念、内容骨架、推理目标和创作边界……</p>
                </div>
              </div>
            </section>
          ) : null}

          {!inEntryView &&
          step === "confirmation" &&
          !briefGenerationPending &&
          (briefConfirmationPhase === "processing" ||
            briefConfirmationPhase === "success") ? (
            <BriefConfirmationTransition
              completed={briefConfirmationPhase === "success"}
            />
          ) : null}

          {!inEntryView &&
          step === "confirmation" &&
          !briefGenerationPending &&
          briefConfirmationPhase !== "processing" &&
          briefConfirmationPhase !== "success" ? (
            <section
              className={`${stageStyles.stepView} ${stageStyles.confirmationStep}`}
              aria-labelledby="confirmation-step-title"
            >
              <header className={`${stageStyles.stepHero} ${stageStyles.confirmationHero}`}>
                <div>
                  <h1 id="confirmation-step-title">让故事的方向落定，再向深处落笔。</h1>
                  <button
                    className={stageStyles.headerBackAction}
                    disabled={briefGenerationPending}
                    onClick={() => {
                      setAuthorAnswerPending(false);
                      setAuthorAnswerSuggestion(null);
                      setAuthorAnswerError(null);
                      openReachableStep("questions");
                    }}
                    type="button"
                  >
                    <span aria-hidden="true">←</span>
                    <span>
                      <b>返回关键追问</b>
                      <small aria-hidden="true">修改上一步答案</small>
                    </span>
                  </button>
                </div>
              </header>

              {intakeFrozen ? (
                <div className={feedbackStyles.frozenNotice} role="status">
                  <p>
                    创作简报已冻结为 V
                    {String(state.frozenBriefVersion ?? 1).padStart(2, "0")}
                    ，当前为只读。后续修改会建立新的简报版本。
                  </p>
                  <button
                    disabled={briefRevisionPending}
                    onClick={() => setBriefRevisionDialogOpen(true)}
                    type="button"
                  >
                    {briefRevisionPending ? "正在创建修订…" : "修改建案"}
                  </button>
                </div>
              ) : null}

              <fieldset
                className={feedbackStyles.briefEditor}
                disabled={intakeFrozen}
              >
                <FieldShell
                  agentChanged={dialogueChangedFields.has("concept")}
                  field="concept"
                  hint="概括核心设定与冲突"
                  icon="target"
                  label="一句话概念"
                  required
                  source={brief.sources.concept}
                >
                  <textarea
                    aria-label="一句话概念"
                    onChange={(event) =>
                      updateBriefField("concept", event.target.value)
                    }
                    placeholder="例如：四名角色在不断重启的空间站中追查事故真相。"
                    rows={3}
                    value={brief.concept}
                  />
                </FieldShell>
                <FieldShell
                  agentChanged={dialogueChangedFields.has("reasoningGoal")}
                  field="reasoning"
                  hint="定义作品最终必须回答的问题"
                  icon="compare"
                  label="推理目标"
                  required
                  source={brief.sources.reasoningGoal}
                >
                  <textarea
                    aria-label="推理目标"
                    onChange={(event) =>
                      updateBriefField("reasoningGoal", event.target.value)
                    }
                    placeholder="例如：找出是谁触发了重启，以及这样做的目的。"
                    rows={3}
                    value={brief.reasoningGoal}
                  />
                </FieldShell>
                <FieldShell
                  agentChanged={dialogueChangedFields.has("conclusionMode")}
                  field="conclusion"
                  hint="决定验证器如何判断结论是否成立；每个项目必须明确选择。"
                  icon="check"
                  label="结论模式"
                  required
                  source={brief.sources.conclusionMode}
                  wide
                >
                  <div className={stageStyles.resolutionChoices}>
                    {conclusionModes.map((mode) => (
                      <label key={mode.value}>
                        <input
                          checked={brief.conclusionMode === mode.value}
                          name="intake-conclusion-mode"
                          onChange={() => updateConclusionMode(mode.value)}
                          type="radio"
                        />
                        <span>
                          <b>{mode.label}</b>
                          <small>{mode.hint}</small>
                        </span>
                      </label>
                    ))}
                  </div>
                  {brief.sources.conclusionMode === "agent_suggestion" ? (
                    <button
                      className={feedbackStyles.suggestionConfirm}
                      onClick={() => updateConclusionMode(brief.conclusionMode)}
                      type="button"
                    >
                      确认 Agent 建议（
                      {conclusionModes.find(
                        (mode) => mode.value === brief.conclusionMode,
                      )?.label ?? "当前结论模式"}
                      ）
                    </button>
                  ) : null}
                </FieldShell>
                <FieldShell
                  agentChanged={dialogueChangedFields.has("resolutionMode")}
                  field="resolution"
                  hint="选择答案由谁提供，以及深稿是否必须收束"
                  icon="target"
                  label="结论处理方式"
                  source={brief.sources.resolutionMode}
                  wide
                >
                  <div className={stageStyles.resolutionChoices}>
                    {resolutionModes.map((mode) => (
                      <label key={mode.value}>
                        <input
                          checked={brief.resolutionMode === mode.value}
                          name="intake-resolution-mode"
                          onChange={() => updateResolutionMode(mode.value)}
                          type="radio"
                        />
                        <span>
                          <b>{mode.label}</b>
                          <small>{mode.hint}</small>
                        </span>
                      </label>
                    ))}
                  </div>
                  {selectedResolutionMode ? (
                    <div
                      aria-live="polite"
                      className={stageStyles.resolutionEffect}
                      data-resolution-mode={selectedResolutionMode.value}
                    >
                      <span>{selectedResolutionMode.effectTiming}</span>
                      <strong>{selectedResolutionMode.effectTitle}</strong>
                      <p>{selectedResolutionMode.effectDetail}</p>
                    </div>
                  ) : null}
                </FieldShell>
                {brief.resolutionMode === "author_anchored" ? (
                  <FieldShell
                    agentChanged={dialogueChangedFields.has("authorAnswer")}
                    field="answer"
                    hint="知道答案可直接填写；还没有答案也可以让 Agent 先拟一版"
                    icon="spark"
                    label="作者答案"
                    required
                    source={brief.sources.authorAnswer}
                    wide
                  >
                    <textarea
                      aria-label="作者答案"
                      onChange={(event) =>
                        updateBriefField("authorAnswer", event.target.value)
                      }
                      placeholder="例如：真正改写记录的是档案修复师未来的自己。"
                      rows={3}
                      value={brief.authorAnswer}
                    />
                    <div className={stageStyles.authorAnswerTools}>
                      <div>
                        <button
                          disabled={authorAnswerPending}
                          onClick={() => void generateAuthorAnswerSuggestion()}
                          type="button"
                        >
                          {authorAnswerPending ? (
                            <>
                              <span
                                aria-hidden="true"
                                className={stageStyles.authorAnswerPendingDots}
                                data-testid="author-answer-thinking"
                              >
                                <i />
                                <i />
                                <i />
                              </span>
                              <span>Agent 正在拟定…</span>
                            </>
                          ) : (
                            "让 Agent 先拟一版"
                          )}
                        </button>
                        <small>Agent 只提供候选，不会自动写入作者答案。</small>
                      </div>
                      {authorAnswerError ? (
                        <p className={stageStyles.inlineError} role="alert">
                          {authorAnswerError}
                        </p>
                      ) : null}
                      {authorAnswerSuggestion ? (
                        <div aria-live="polite" className={stageStyles.authorAnswerSuggestion}>
                          <span>Agent 候选 · 待作者确认</span>
                          <p>{authorAnswerSuggestion}</p>
                          <div>
                            <button onClick={adoptAuthorAnswerSuggestion} type="button">
                              采用这条候选
                            </button>
                            <button
                              onClick={() => setAuthorAnswerSuggestion(null)}
                              type="button"
                            >
                              不采用，我自己写
                            </button>
                          </div>
                        </div>
                      ) : null}
                    </div>
                  </FieldShell>
                ) : null}
                <FieldShell
                  agentChanged={dialogueChangedFields.has("sellingPoints")}
                  field="selling-points"
                  hint="逐条编辑独立亮点，支持拖动排序"
                  icon="spark"
                  label="核心卖点"
                  source={brief.sources.sellingPoints}
                >
                  <SellingPointsEditor
                    onChange={(value) => updateBriefField("sellingPoints", value)}
                    value={brief.sellingPoints}
                  />
                </FieldShell>
                <FieldShell
                  agentChanged={dialogueChangedFields.has("outline")}
                  field="outline"
                  hint="按阶段拆解推进与验证过程"
                  icon="history"
                  label="内容骨架"
                  source={brief.sources.outline}
                >
                  <OutlineStagesEditor
                    onChange={(value) => updateBriefField("outline", value)}
                    value={brief.outline}
                  />
                </FieldShell>
                <FieldShell
                  agentChanged={dialogueChangedFields.has("scopeEstimate")}
                  field="scope"
                  hint="估算角色、场景与体验时长"
                  icon="archive"
                  label="预计规模"
                  source={brief.sources.scopeEstimate}
                >
                  <textarea
                    aria-label="预计规模"
                    onChange={(event) =>
                      updateBriefField("scopeEstimate", event.target.value)
                    }
                    placeholder="例如：4 名角色 / 7 个场景 / 90 分钟"
                    rows={3}
                    value={brief.scopeEstimate}
                  />
                </FieldShell>
                <FieldShell
                  agentChanged={dialogueChangedFields.has("riskNotes")}
                  field="risk"
                  hint="提前标出容易失控的设计风险"
                  icon="compare"
                  label="风险提示"
                  source={brief.sources.riskNotes}
                >
                  <textarea
                    aria-label="风险提示"
                    onChange={(event) =>
                      updateBriefField("riskNotes", event.target.value)
                    }
                    placeholder="例如：避免让记忆改写成为无法验证的万能解释。"
                    rows={3}
                    value={brief.riskNotes}
                  />
                </FieldShell>
              </fieldset>

              <details
                className={stageStyles.constraintDrawer}
                data-agent-changed={dialogueChangedFields.has("constraints") || undefined}
                data-has-items={activeConstraintCount > 0}
              >
                <summary>
                  <div className={stageStyles.constraintDrawerLead}>
                    <span aria-hidden="true" className={stageStyles.constraintDrawerIcon}>
                      <Glyph name="archive" />
                    </span>
                    <div>
                      <span>约束抽屉 / BOUNDARY CHANNEL</span>
                      <b>创作约束设置</b>
                      <small>必须保留、禁止出现、规模、人数、时长与内容尺度</small>
                    </div>
                  </div>
                  <div className={stageStyles.constraintDrawerState}>
                    <em>{activeConstraintCount} 项已填写</em>
                    <span aria-hidden="true">
                      <i>展开设置</i>
                      <Glyph name="arrow" />
                    </span>
                  </div>
                </summary>
                <div className={stageStyles.constraintGrid}>
                  {brief.constraints.map((constraint) => (
                    <label key={constraint.key}>
                      <span>
                        <b>{constraint.label}</b>
                        <small>{constraint.hint}</small>
                      </span>
                      <textarea
                        disabled={intakeFrozen}
                        onChange={(event) =>
                          updateConstraint(constraint.key, {
                            statement: event.target.value,
                          })
                        }
                        placeholder={constraint.placeholder}
                        rows={3}
                        value={constraint.statement}
                      />
                      <select
                        aria-label={constraint.label + "约束强度"}
                        disabled={intakeFrozen}
                        onChange={(event) =>
                          updateConstraint(constraint.key, {
                            strength: event.target.value as
                              | "hard"
                              | "soft",
                          })
                        }
                        value={constraint.strength}
                      >
                        <option value="hard">硬约束</option>
                        <option value="soft">软偏好</option>
                      </select>
                    </label>
                  ))}
                </div>
              </details>

              <section
                aria-busy={dialogueRevisionPending}
                aria-live="polite"
                className={revisionStyles.revisionStudio}
                data-pending={dialogueRevisionPending}
              >
                <div className={revisionStyles.revisionAgent}>
                  <span aria-hidden="true" className={revisionStyles.revisionAgentMark}>
                    <Glyph name="spark" />
                    <i />
                    <i />
                    <i />
                  </span>
                  <div>
                    <em>CASEFILE AGENT / REVISION</em>
                    <b>对话修改</b>
                    <small>告诉 Agent 要改变什么；未提及的字段默认保持不变。</small>
                  </div>
                </div>
                <textarea
                  aria-label="对话修改指令"
                  disabled={intakeFrozen || dialogueRevisionPending}
                  onChange={(event) => setRevisionInstruction(event.target.value)}
                  placeholder="例如：把内容骨架压缩成三个阶段，其他已确认内容不变。"
                  rows={3}
                  value={revisionInstruction}
                />
                <button
                  disabled={
                    intakeFrozen ||
                    dialogueRevisionPending ||
                    !revisionInstruction.trim()
                  }
                  onClick={createDialogueRevision}
                  type="button"
                >
                  {dialogueRevisionPending ? (
                    <>
                      <span
                        aria-hidden="true"
                        className={revisionStyles.revisionSubmitPulse}
                      >
                        <i />
                        <i />
                        <i />
                      </span>
                      <span>
                        <b>Agent 正在生成</b>
                        <small>对照当前候选</small>
                      </span>
                    </>
                  ) : (
                    <>
                      交给 Agent 修改
                      <Glyph name="arrow" />
                    </>
                  )}
                </button>
              </section>

              {visibleDialogueRevisionReceipt ? (
                <section
                  aria-label="本轮 Agent 修改"
                  aria-live="polite"
                  className={revisionStyles.revisionReceipt}
                  role="status"
                >
                  <header>
                    <span aria-hidden="true"><Glyph name="check" /></span>
                    <div>
                      <small>AGENT REVISION / COMPLETE</small>
                      <b>{visibleDialogueRevisionReceipt.candidateLabel}已生成</b>
                      <p>执行指令：{visibleDialogueRevisionReceipt.instruction}</p>
                    </div>
                  </header>
                  {visibleDialogueRevisionReceipt.changes.length ? (
                    <ul>
                      {visibleDialogueRevisionReceipt.changes.map((change) => (
                        <li key={change.key}>
                          <b>{change.label}</b>
                          <div>
                            <span>修改前</span>
                            <del>{change.before}</del>
                          </div>
                          <div>
                            <span>修改后</span>
                            <ins>{change.after}</ins>
                          </div>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className={revisionStyles.revisionNoChanges}>
                      新候选与原候选的字段内容一致，没有可列出的修改。
                    </p>
                  )}
                  <footer>
                    未列出的字段保持不变；原候选仍保留在候选历史中。
                  </footer>
                </section>
              ) : null}

              <section className={stageStyles.candidateHistory} data-open={historyOpen}>
                <button onClick={() => setHistoryOpen((open) => !open)} type="button">
                  <span>
                    <Glyph name="history" />
                    候选历史
                    <b>{candidates.length}</b>
                  </span>
                  <em>{historyOpen ? "收起" : "展开"}</em>
                </button>
                {historyOpen ? (
                  <div>
                    {candidates.map((candidate) => (
                      <article
                        data-current={candidate.id === currentCandidateId}
                        key={candidate.id}
                      >
                        <span>V{candidateHistoryVersionById.get(candidate.id) ?? 1}</span>
                        <div>
                          <b>{candidate.label}</b>
                          <small>
                            {candidateOriginLabels[candidate.origin]} ·{" "}
                            {formatCandidateTimestamp(candidate.createdAt)}
                          </small>
                          <p>{candidate.brief.concept}</p>
                        </div>
                        <em>
                          {candidate.id === currentCandidateId
                            ? "当前"
                            : candidate.bookmarked
                              ? "已保存"
                              : "历史"}
                        </em>
                        {candidate.id !== currentCandidateId ? (
                          <button
                            disabled={intakeFrozen}
                            onClick={() => restoreCandidate(candidate)}
                            type="button"
                          >
                            恢复此版
                          </button>
                        ) : null}
                      </article>
                    ))}
                  </div>
                ) : null}
              </section>

              {!intakeFrozen && briefConfirmationIssue ? (
                <BriefConfirmationInterruption
                  decision={resolutionDecision}
                  issue={briefConfirmationIssue}
                  issueRef={confirmationIssueRef}
                  onConfirmDecision={applyResolutionDecision}
                  onDecisionChange={setResolutionDecision}
                  onReturnToField={() => {
                    setBriefConfirmationIssue(null);
                    setBriefConfirmationPhase("idle");
                    if (briefConfirmationIssue.kind === "missing_field") {
                      focusBriefField(briefConfirmationIssue.field);
                    }
                  }}
                />
              ) : null}

              {error ? (
                <div className={feedbackStyles.confirmationError} role="alert">
                  <p>{error}</p>
                  <div>
                    {confirmationRevisionConflict ? (
                      <button onClick={() => void reloadLatestBrief()} type="button">
                        载入最新版本
                      </button>
                    ) : null}
                    {briefConfirmationPhase === "failed" ? (
                      <button
                        data-primary="true"
                        onClick={() => requestBriefConfirmation()}
                        type="button"
                      >
                        重新确认
                      </button>
                    ) : null}
                  </div>
                </div>
              ) : null}

              {intakeFrozen ||
              (!briefConfirmationIssue && briefConfirmationPhase !== "failed") ? (
              <footer className={stageStyles.stepActions}>
                <div>
                  {intakeFrozen ? (
                    <button
                      className={stageStyles.primaryAction}
                      onClick={() => setStep("candidates")}
                      type="button"
                    >
                      返回深稿候选
                      <Glyph name="arrow" />
                    </button>
                  ) : (
                    <>
                      <button
                        className={stageStyles.secondaryAction}
                        disabled={
                          candidateSavePending ||
                          missingFields.length > 0
                        }
                        onClick={saveCandidate}
                        type="button"
                      >
                        {candidateSavePending ? "正在保存候选…" : "保存为新候选"}
                      </button>
                      <button
                        className={stageStyles.primaryAction}
                        onClick={() => requestBriefConfirmation()}
                        type="button"
                      >
                        确认建案并继续
                        <Glyph name="arrow" />
                      </button>
                    </>
                  )}
                </div>
              </footer>
              ) : null}
            </section>
          ) : null}

          {!inEntryView && step === "candidates" ? <DraftCandidatesStage /> : null}
        </main>

          </div>
        </>
      )}

      <CaseHistoryDrawer
        currentProjectId={activeProjectId}
        onClose={() => setHistoryDrawerOpen(false)}
        onNotice={announce}
        onRestore={restoreProject}
        open={historyDrawerOpen}
      />
      {confirmReturnToIdea ? (
        <div className={feedbackStyles.impactDialogBackdrop} role="presentation">
          <section
            aria-labelledby="return-to-idea-title"
            aria-modal="true"
            className={feedbackStyles.impactDialog}
            role="alertdialog"
          >
            <small>DEPENDENCY NOTICE / 依赖提示</small>
            <span aria-hidden="true">!</span>
            <h2 id="return-to-idea-title">返回修改起案内容？</h2>
            <p>修改原始内容可能影响：</p>
            <ul>
              <li>当前关键追问</li>
              <li>已生成的创作简报</li>
            </ul>
            <strong>已有内容、候选和版本不会丢失。</strong>
            <footer>
              <button
                onClick={() => setConfirmReturnToIdea(false)}
                type="button"
              >
                取消
              </button>
              <button autoFocus onClick={returnToIdeaStep} type="button">
                返回修改
                <Glyph name="arrow" />
              </button>
            </footer>
          </section>
        </div>
      ) : null}
      {briefRevisionDialogOpen ? (
        <BriefRevisionDialog
          currentVersion={state.frozenBriefVersion ?? state.workingBriefVersion}
          onCancel={() => setBriefRevisionDialogOpen(false)}
          onConfirm={() => void reopenFrozenBrief()}
          pending={briefRevisionPending}
        />
      ) : null}
    </div>
  );
}
