"use client";

import Link from "next/link";
import {
  type ReactNode,
  type SetStateAction,
  useEffect,
  useMemo,
  useState,
} from "react";

import { useCaseSession } from "@/features/case-session/case-session-provider";
import {
  generateIdeas,
  fetchIdeas,
  selectIdea,
  bookmarkIdea,
  archiveIdea,
  regenerateIdea,
  createCaseProject,
} from "@/features/case-session/case-session-api";

import {
  candidateOriginLabels,
  candidateHistoryVersions,
  conclusionModes,
  fieldSourceLabels,
  intakeRoutes,
  missingHardFields,
  polishModes,
  intakeSteps,
  resolutionModes,
  sampleIdea,
  type IntakeAnswer,
  type IntakeBrief,
  type BriefCandidate,
  type IntakeConstraint,
  type FieldSource,
  type IntakePolishMode,
  type ConclusionMode,
  type ResolutionMode,
  type IntakeStep,
  type IdeaCandidateView,
} from "./intake-model";
import { BriefReviewStage } from "./brief-review-stage";
import { CaseHistoryDrawer } from "./case-history-drawer";
import { DraftCandidatesStage } from "./draft-candidates-stage";
import IdeaCandidatesStage from "./idea-candidates-stage";
import ReverseParseStage from "./reverse-parse-stage";
import {
  OutlineStagesEditor,
  SellingPointsEditor,
} from "./structured-list-editor";
import stageStyles from "./intake-early-stages.module.css";
import styles from "./intake-center.module.css";

type BriefTextField =
  | "concept"
  | "sellingPoints"
  | "outline"
  | "reasoningGoal"
  | "authorAnswer"
  | "scopeEstimate"
  | "riskNotes";

type RawIdeaRecord = Record<string, unknown>;

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

function Glyph({
  name,
}: {
  name: "archive" | "arrow" | "check" | "compare" | "history" | "spark" | "target";
}) {
  if (name === "check") {
    return (
      <svg aria-hidden="true" viewBox="0 0 24 24">
        <path d="m5 12.5 4.2 4.2L19 7" />
      </svg>
    );
  }
  if (name === "compare") {
    return (
      <svg aria-hidden="true" viewBox="0 0 24 24">
        <path d="M7 5h12M7 12h8M7 19h12M3 5h.01M3 12h.01M3 19h.01" />
      </svg>
    );
  }
  if (name === "history") {
    return (
      <svg aria-hidden="true" viewBox="0 0 24 24">
        <path d="M4 12a8 8 0 1 0 2.3-5.7L4 8.6M4 4v4.6h4.6M12 7.5V12l3 2" />
      </svg>
    );
  }
  if (name === "archive") {
    return (
      <svg aria-hidden="true" viewBox="0 0 24 24">
        <path d="M4 7.5h16v12H4zM3 4.5h18v3H3zM8 12h8M9 15.5h6" />
      </svg>
    );
  }
  if (name === "spark") {
    return (
      <svg aria-hidden="true" viewBox="0 0 24 24">
        <path d="M12 2.8 13.8 9l6.2 1.8-6.2 1.8L12 19l-1.8-6.4L4 10.8 10.2 9 12 2.8Z" />
      </svg>
    );
  }
  if (name === "target") {
    return (
      <svg aria-hidden="true" viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="8" />
        <circle cx="12" cy="12" r="3" />
        <path d="M12 2v3M22 12h-3M12 22v-3M2 12h3" />
      </svg>
    );
  }
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24">
      <path d="M5 12h14M14 6l6 6-6 6" />
    </svg>
  );
}

function SourceBadge({ source }: { source: FieldSource }) {
  return (
    <span className={stageStyles.sourceBadge} data-source={source}>
      <i aria-hidden="true" />
      {fieldSourceLabels[source]}
    </span>
  );
}

function FieldShell({
  label,
  hint,
  source,
  required = false,
  wide = false,
  children,
}: {
  label: string;
  hint: string;
  source: FieldSource;
  required?: boolean;
  wide?: boolean;
  children: ReactNode;
}) {
  return (
    <section
      className={stageStyles.fieldShell}
      data-required={required || undefined}
      data-wide={wide}
    >
      <header>
        <div>
          <label>
            {label}
            {required ? <em aria-hidden="true">*</em> : null}
          </label>
          <small>{hint}</small>
        </div>
        <SourceBadge source={source} />
      </header>
      {children}
    </section>
  );
}

export function IntakeCenter() {
  const {
    state,
    patchState,
    beginBriefReview,
    submitPolish,
    adoptPolish: adoptPolishDraft,
    continueToQuestions: proceedToQuestions,
    generateMoreQuestions: requestMoreQuestions,
    generateBriefFromAnswers: synthesizeBriefFromServer,
    generateAuthorAnswer,
    createManualBrief,
    saveCandidateAsNew: saveCandidateToServer,
    createDialogueRevision: createDialogueRevisionFromServer,
    saveCandidateBookmark,
    activateCandidate,
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
  const [notice, setNotice] = useState("建案数据写入开发库；当前项目可通过 URL 恢复。");
  const [sourceDirty, setSourceDirty] = useState(false);
  const [answersDirty, setAnswersDirty] = useState(false);
  const [briefDirty, setBriefDirty] = useState(false);
  const [questionGenerationFailed, setQuestionGenerationFailed] =
    useState(false);
  const [manualBriefPending, setManualBriefPending] = useState(false);
  const [candidateSavePending, setCandidateSavePending] = useState(false);
  const [dialogueRevisionPending, setDialogueRevisionPending] =
    useState(false);
  const [briefReviewPending, setBriefReviewPending] = useState(false);
  const [confirmingExample, setConfirmingExample] = useState(false);

  // ── Path B: Idea Generation ───────────────────────────────────────────
  const [showIdeaGeneration, setShowIdeaGeneration] = useState(false);
  const [ideaProjectId, setIdeaProjectId] = useState<number | null>(null);
  const [ideaCandidates, setIdeaCandidates] = useState<IdeaCandidateView[]>([]);
  const [pastBatches, setPastBatches] = useState<Record<string, IdeaCandidateView[]>>({});
  const [ideaGenerating, setIdeaGenerating] = useState(false);

  // ── Path C: Reverse Parse ────────────────────────────────────────────
  const [showReverseParse, setShowReverseParse] = useState(false);

  const handlePathB = async () => {
    if (ideaGenerating) return;
    // 立即切换到路径 B 界面（组件自带 generating 加载态），
    // 避免异步建案/生成期间停留在路径 A，也避免完成后覆盖用户已切换的路径。
    setShowReverseParse(false);
    setShowIdeaGeneration(true);
    setIdeaGenerating(true);
    setError(null);
    try {
      const project = activeProjectId
        ? { id: activeProjectId }
        : await createCaseProject("帮我想一个");
      setIdeaProjectId(project.id);

      // Fetch past ideas for inspiration browsing
      try {
        const past = await fetchIdeas(project.id);
        const castIdea = (idea: RawIdeaRecord): IdeaCandidateView => ({
          id: idea.id as number,
          batch_id: idea.batch_id as string,
          ordinal: idea.ordinal as number,
          content: idea.content as IdeaCandidateView["content"],
          status: (idea.status ?? "active") as IdeaCandidateView["status"],
          bookmarked: (idea.bookmarked ?? false) as boolean,
          created_at: (idea.created_at ?? null) as string | null,
        });
        const pastMap: Record<string, IdeaCandidateView[]> = {};
        for (const [key, val] of Object.entries(past.batches ?? {})) {
          pastMap[key] = (val as RawIdeaRecord[]).map(castIdea);
        }
        setPastBatches(pastMap);
      } catch { /* silently ignore */ }

      const result = await generateIdeas(project.id);
      const cast = (idea: Record<string, unknown>): IdeaCandidateView => ({
        id: idea.id as number,
        batch_id: idea.batch_id as string,
        ordinal: idea.ordinal as number,
        content: idea.content as IdeaCandidateView["content"],
        status: (idea.status ?? "active") as IdeaCandidateView["status"],
        bookmarked: (idea.bookmarked ?? false) as boolean,
        created_at: (idea.created_at ?? null) as string | null,
      });
      setIdeaCandidates((result.ideas ?? []).map(cast));
    } catch (err) {
      setError(err instanceof Error ? err.message : "生成创意失败。");
    } finally {
      setIdeaGenerating(false);
    }
  };

  const handleSelectIdea = async (ideaId: number) => {
    if (!ideaProjectId) return;
    try {
      await selectIdea(ideaProjectId, ideaId);
      setIdeaCandidates((prev) =>
        prev.map((i) => (i.id === ideaId ? { ...i, status: "selected" as const } : i)),
      );
      await loadProject(ideaProjectId);
      setShowIdeaGeneration(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "选择失败。");
    }
  };

  const refetchIdeas = async () => {
    if (!ideaProjectId) return;
    try {
      const past = await fetchIdeas(ideaProjectId);
      const castIdea = (idea: RawIdeaRecord): IdeaCandidateView => ({
        id: idea.id as number, batch_id: idea.batch_id as string, ordinal: idea.ordinal as number,
        content: idea.content as IdeaCandidateView["content"],
        status: (idea.status ?? "active") as IdeaCandidateView["status"],
        bookmarked: (idea.bookmarked ?? false) as boolean,
        created_at: (idea.created_at ?? null) as string | null,
      });
      const all: Record<string, IdeaCandidateView[]> = {};
      for (const [key, val] of Object.entries(past.batches ?? {})) {
        all[key] = (val as RawIdeaRecord[]).map(castIdea);
      }
      setPastBatches(all);
      setIdeaCandidates((prev) => {
        const latestBatchId = prev[0]?.batch_id;
        return latestBatchId ? (all[latestBatchId] ?? prev) : prev;
      });
    } catch { /* noop */ }
  };

  const handleBookmarkIdea = async (ideaId: number) => {
    if (!ideaProjectId) return;
    try {
      await bookmarkIdea(ideaProjectId, ideaId);
      await refetchIdeas();
    } catch { /* noop */ }
  };

  const handleArchiveIdea = async (ideaId: number) => {
    if (!ideaProjectId) return;
    try {
      await archiveIdea(ideaProjectId, ideaId);
      await refetchIdeas();
    } catch { /* noop */ }
  };

  const handleRegenerateIdea = async (ideaId: number) => {
    if (!ideaProjectId) return;
    try {
      await regenerateIdea(ideaProjectId, ideaId);
      await refetchIdeas();
    } catch { /* noop */ }
  };

  useEffect(() => {
    if (!state.review?.dirty) return;
    const warnBeforeLeaving = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warnBeforeLeaving);
    return () => window.removeEventListener("beforeunload", warnBeforeLeaving);
  }, [state.review?.dirty]);

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
  const currentCandidate =
    candidates.find((candidate) => candidate.id === currentCandidateId) ?? null;
  const candidateHistoryVersionById = useMemo(
    () => candidateHistoryVersions(candidates),
    [candidates],
  );
  const requiredQuestions = state.questions.filter(
    (question) => question.required,
  );
  const hardQuestionsResolved = requiredQuestions.every((question) => {
    const answer = answers[question.key];
    return Boolean(answer?.text.trim() && !answer.pending);
  });
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
  const pendingDecisionCount = Object.values(answers).filter(
    (answer) => answer.pending,
  ).length;

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
        ready: candidates.length > 0,
        value: candidates.length ? candidates.length + " 个候选" : "尚未形成",
      },
      {
        label: "审阅冻结",
        ready: state.frozenBriefVersion !== null,
        value: state.frozenBriefVersion ? "已冻结" : "尚未冻结",
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
    setNotice(message);
  }

  function openReachableStep(target: IntakeStep) {
    const targetIndex = intakeSteps.findIndex((item) => item.id === target);
    if (targetIndex < 0 || targetIndex > furthestStep) return;
    if (target === "candidates" && state.frozenBriefVersion === null) {
      setError("先完成创作简报审阅并冻结，再生成深稿候选。");
      return;
    }
    if (target === "review" && !state.review) {
      setError("先进入创作简报审阅，再回到审阅步骤。");
      return;
    }
    // 步骤条不能绕过持久化动作：上游改动未落库时阻止前跳。
    if (
      step === "idea" &&
      sourceDirty &&
      targetIndex > stepIndex
    ) {
      setError("最初想法尚未保存，请先点击“继续关键追问”。");
      return;
    }
    if (
      step === "questions" &&
      answersDirty &&
      targetIndex > stepIndex
    ) {
      setError("回答尚未并入创作简报，请点击“形成创作简报”或“手动建立简报”。");
      return;
    }
    if (
      step === "confirmation" &&
      briefDirty &&
      targetIndex > stepIndex
    ) {
      setError("创作简报有未保存修改，请先保存为新候选，或通过“进入创作简报审阅”保存。");
      return;
    }
    if (
      step === "review" &&
      state.review?.dirty &&
      targetIndex > stepIndex
    ) {
      setError("审阅有未保存修改，保存并冻结后才能生成深稿候选。");
      return;
    }
    if (target !== "confirmation") {
      setAuthorAnswerPending(false);
      setAuthorAnswerSuggestion(null);
      setAuthorAnswerError(null);
    }
    setStep(target);
    setError(null);
    announce("已切换到" + intakeSteps[targetIndex].label + "。");
  }

  function loadExample() {
    setSourceText(sampleIdea);
    setSourceDirty(true);
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
      setPolishDraft(result.text);
      setPolishNotes(result.notes);
      setIntroducedDetails(result.introducedDetails);
      setPolishParentSourceRecordId(result.parentSourceRecordId);
      setSourceDirty(false);
      announce("润色校样已形成，原文仍保持不变。");
    } catch (caught) {
      setPolishReviewOpen(false);
      setError(caught instanceof Error ? caught.message : "润色任务未完成。");
    } finally {
      setPolishPending(false);
    }
  }

  async function adoptPolish() {
    if (!polishDraft.trim()) return;
    setError(null);
    try {
      await adoptPolishDraft(
        polishDraft.trim(),
        polishParentSourceRecordId,
      );
      setSourceText(polishDraft.trim());
      setSourceDirty(false);
      setPolishReviewOpen(false);
      announce("已采用润色稿，原始版本仍可在来源记录中追溯。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "采用润色稿失败。");
    }
  }

  async function continueToQuestions() {
    if (!sourceText.trim()) {
      setError("请先写下最初想法。");
      return;
    }
    setError(null);
    setQuestionGenerationFailed(false);
    setQuestionGenerationMode("initial");
    setStep("questions");
    try {
      await proceedToQuestions();
      setSourceDirty(false);
      setAnswersDirty(false);
      announce("起案原文已记录，进入关键追问。");
    } catch (caught) {
      // 提问失败也保留人工继续路径：停留在追问页，允许“手动建立简报”。
      setQuestionGenerationFailed(true);
      setError(caught instanceof Error ? caught.message : "追问任务未完成。");
    } finally {
      setQuestionGenerationMode(null);
    }
  }

  async function generateMoreQuestions() {
    if (questionsPending) return;
    setError(null);
    setQuestionGenerationMode("additional");
    try {
      await requestMoreQuestions();
      announce("已补充新的追问；已有问题和回答保持不变。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "补充追问任务未完成。");
    } finally {
      setQuestionGenerationMode(null);
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
    announce("这项偏好已放入待决定队列，不会阻止继续。");
  }

  async function generateBrief() {
    if (!hardQuestionsResolved) {
      setError("必须先回答关键问题，才能形成创作简报。");
      return;
    }
    if (questionGenerationFailed) {
      setError("关键追问尚未生成。可以返回原稿重试，或使用“手动建立简报”。");
      return;
    }
    setError(null);
    setAuthorAnswerSuggestion(null);
    setAuthorAnswerError(null);
    setBriefGenerationPending(true);
    setStep("confirmation");
    try {
      await synthesizeBriefFromServer();
      setAnswersDirty(false);
      setBriefDirty(false);
      announce("创作简报候选已形成，请逐项校核后采用。");
    } catch (caught) {
      setStep("questions");
      setError(caught instanceof Error ? caught.message : "创作简报生成未完成。");
    } finally {
      setBriefGenerationPending(false);
    }
  }

  async function continueManually() {
    if (manualBriefPending) return;
    setManualBriefPending(true);
    setError(null);
    try {
      await createManualBrief();
      setAnswersDirty(false);
      setBriefDirty(false);
      announce("已建立人工简报，不包含任何伪造的 Agent 结果。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "人工简报建立失败。");
    } finally {
      setManualBriefPending(false);
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
    setAuthorAnswerPending(true);
    setAuthorAnswerSuggestion(null);
    setAuthorAnswerError(null);
    setError(null);
    try {
      const suggestion = await generateAuthorAnswer(brief);
      setAuthorAnswerSuggestion(suggestion);
      announce("Agent 只提供了一版答案候选；你可以采用、改写，或直接写自己的结论。");
    } catch (caught) {
      setAuthorAnswerError(
        caught instanceof Error
          ? caught.message
          : "答案候选生成未完成，请直接填写你的结论。",
      );
    } finally {
      setAuthorAnswerPending(false);
    }
  }

  function adoptAuthorAnswerSuggestion() {
    if (!authorAnswerSuggestion) return;
    updateBriefField("authorAnswer", authorAnswerSuggestion);
    setAuthorAnswerSuggestion(null);
    announce("已把 Agent 候选放入简报草案；提交审阅前仍可继续改写。 ");
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
    if (candidateSavePending) return;
    if (missingFields.length) {
      setError("保存前请补齐：" + missingFields.join("、") + "。");
      return;
    }
    setCandidateSavePending(true);
    setError(null);
    try {
      await saveCandidateToServer();
      setBriefDirty(false);
      announce("已保存为新的独立候选，旧版本没有被覆盖。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "候选保存失败。");
    } finally {
      setCandidateSavePending(false);
    }
  }

  async function createDialogueRevision() {
    if (dialogueRevisionPending) return;
    const instruction = revisionInstruction.trim();
    if (!instruction) {
      setError("请先写下这一轮要修改的内容。");
      return;
    }
    setDialogueRevisionPending(true);
    setError(null);
    try {
      await createDialogueRevisionFromServer(instruction);
      setRevisionInstruction("");
      announce("已从当前候选形成子版本；原候选仍保留。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "对话修改未完成。");
    } finally {
      setDialogueRevisionPending(false);
    }
  }

  async function restoreCandidate(candidate: BriefCandidate) {
    setError(null);
    try {
      await activateCandidate(candidate.id);
      setBriefDirty(false);
      announce("已恢复" + candidate.label + "。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "候选恢复失败。");
    }
  }

  async function toggleBookmark(candidateId: number) {
    setError(null);
    try {
      await saveCandidateBookmark(candidateId);
      announce("候选保存状态已更新。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "候选保存失败。");
    }
  }

  async function enterBriefReview() {
    if (briefReviewPending) return;
    setBriefReviewPending(true);
    setError(null);
    try {
      await beginBriefReview();
      setBriefDirty(false);
      setAuthorAnswerPending(false);
      setAuthorAnswerSuggestion(null);
      setAuthorAnswerError(null);
      announce("已进入创作简报审阅；保存并冻结后才能生成候选稿。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "进入审阅失败。");
    } finally {
      setBriefReviewPending(false);
    }
  }

  function resetSession() {
    resetSessionState();
    setPolishReviewOpen(false);
    setQuestionGenerationMode(null);
    setQuestionGenerationFailed(false);
    setBriefGenerationPending(false);
    setAuthorAnswerPending(false);
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
    setManualBriefPending(false);
    setCandidateSavePending(false);
    setDialogueRevisionPending(false);
    setBriefReviewPending(false);
    setConfirmingExample(false);
    setError(null);
    announce("已恢复未建案状态；后续操作将创建新项目。");
  }

  async function restoreProject(projectId: number) {
    stashCurrentSession();
    try {
      await loadProject(projectId);
      setSourceDirty(false);
      setAnswersDirty(false);
      setBriefDirty(false);
      setQuestionGenerationFailed(false);
      setHistoryDrawerOpen(false);
      setError(null);
      announce("已恢复该卷宗；服务端状态已重新同步。");
    } catch (caught) {
      resetSession();
      setHistoryDrawerOpen(false);
      setError(caught instanceof Error ? caught.message : "卷宗恢复失败，请重试。");
    }
  }

  async function retryLatestTask(taskType: Parameters<typeof retryTask>[0]) {
    if (retryingTaskType) return;
    setRetryingTaskType(taskType);
    setError(null);
    try {
      const polished = await retryTask(taskType);
      if (polished) {
        setPolishDraft(polished.text);
        setPolishNotes(polished.notes);
        setIntroducedDetails(polished.introducedDetails);
        setPolishParentSourceRecordId(polished.parentSourceRecordId);
        setPolishReviewOpen(true);
      }
      announce("任务已重新提交；页面会继续显示最新执行状态。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "任务重试失败，请稍后再试。");
    } finally {
      setRetryingTaskType(null);
    }
  }

  function restoreStashed() {
    restoreStashedSession();
    setSourceDirty(false);
    setAnswersDirty(false);
    setBriefDirty(false);
    setQuestionGenerationFailed(false);
    setError(null);
    announce("已回到暂存的卷宗。");
  }

  const selectedResolutionMode = resolutionModes.find(
    (mode) => mode.value === brief.resolutionMode,
  );
  const intakeFrozen = state.frozenBriefVersion !== null;

  return (
    <div
      className={styles.intakeCenter}
      data-casefile-surface="intake-center-v1"
      data-intake-step={step}
    >
      <header className={styles.topbar}>
        <div className={styles.brandCell}>
          <Link className={styles.brand} href="/">
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
            className={styles.accountButton}
            onClick={() => window.dispatchEvent(new Event("casefile:open-settings"))}
            title="模型服务设置"
            type="button"
          >
            <span>A</span>
            <i aria-hidden="true">⌄</i>
          </button>
        </nav>
      </header>

      <nav aria-label="建案进度" className={styles.pulseTrack}>
        <div className={styles.pulseIdentity}>
          <div>
            <span>选择起点</span>
            <b>四条建案路径</b>
          </div>
          <i aria-hidden="true">＋</i>
        </div>
        <ol>
          {intakeSteps.map((item, index) => {
            const stepLocked =
              index > furthestStep ||
              (item.id === "candidates" && state.frozenBriefVersion === null) ||
              (item.id === "review" && state.review === null);
            return (
              <li
                data-active={item.id === step}
                data-complete={
                  index < stepIndex ||
                  (item.id === "candidates" && Boolean(state.adoptedCandidateId))
                }
                data-reachable={!stepLocked}
                key={item.id}
              >
                <button
                  aria-current={item.id === step ? "step" : undefined}
                  aria-label={`${item.no} ${item.shortLabel} ${item.label}`}
                  disabled={stepLocked}
                  onClick={() => openReachableStep(item.id)}
                  type="button"
                >
                  <span>{item.no}</span>
                  <div>
                    <small>{item.shortLabel}</small>
                    <b>{item.label}</b>
                  </div>
                  {index < stepIndex ||
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
          <span>{String(completionCount).padStart(2, "0")} / 05</span>
          <small>建案完成度</small>
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
        {notice ? (
          <div aria-live="polite" className={styles.sessionNotice} role="status">
            <span>{notice}</span>
            <button
              aria-label="关闭提示"
              onClick={() => setNotice("")}
              type="button"
            >
              ×
            </button>
          </div>
        ) : null}
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
        <aside aria-label="建案入口" className={styles.routeDock}>
          <div className={styles.routeList}>
            {intakeRoutes.map((route) => {
              const available = route.state === "available";
              const isActive = route.code === "A"
                ? !showIdeaGeneration && !showReverseParse
                : route.code === "B"
                  ? showIdeaGeneration
                  : route.code === "C"
                    ? showReverseParse
                    : false;
              return (
                <button
                  aria-current={isActive ? "step" : undefined}
                  data-available={available}
                  disabled={!available}
                  key={route.code}
                  type="button"
                  onClick={
                    route.code === "B" ? handlePathB
                      : route.code === "A" ? () => { setShowIdeaGeneration(false); setShowReverseParse(false); }
                      : route.code === "C" ? () => { setShowIdeaGeneration(false); setShowReverseParse(true); }
                      : undefined
                  }
                >
                  <span>{route.code}</span>
                  <div>
                    <b>{route.label}</b>
                    <small>{route.summary}</small>
                  </div>
                  <em>{isActive ? "当前路径" : available ? "" : "后续开放"}</em>
                </button>
              );
            })}
          </div>
          <section className={styles.routeNote}>
            <span>为什么先建案？</span>
            <p>
              先固定意图、关键问题和边界，后续生成才有可审阅的依据。
            </p>
          </section>
        </aside>

        <main className={styles.focusPlane}>
          {showReverseParse ? (
            <ReverseParseStage onFormed={() => setShowReverseParse(false)} />
          ) : showIdeaGeneration ? (
            <IdeaCandidatesStage
              ideas={ideaCandidates}
              pastBatches={pastBatches}
              generating={ideaGenerating}
              onSelect={handleSelectIdea}
              onBookmark={handleBookmarkIdea}
              onArchive={handleArchiveIdea}
              onRegenerate={handleRegenerateIdea}
              onGenerateAll={handlePathB}
            />
          ) : step === "idea" ? (
            <section className={stageStyles.stepView} aria-labelledby="idea-step-title">
              <header className={stageStyles.stepHero}>
                <div>
                  <small>第 1 步 / 记录信号</small>
                  <h1 id="idea-step-title">
                    把念头照亮，
                    <br />
                    留下可追溯的起案依据。
                  </h1>
                </div>
                <p>
                  不必先写成完整故事。记录角色、异常或冲突中的任意一个，后续只追问真正会改变方向的问题。
                </p>
              </header>

              {intakeFrozen ? (
                <>
                  <section className={stageStyles.sourceCapsule}>
                    <span>已冻结的最初想法</span>
                    <p>{sourceText || "未记录原文。"}</p>
                    <SourceBadge source="user_original" />
                  </section>
                  <p className={stageStyles.frozenNotice} role="status">
                    创作简报已冻结为 V
                    {String(state.frozenBriefVersion ?? 1).padStart(2, "0")}
                    。当前为只读；要修改内容，请到第 5 步选择“建立简报修订”。
                  </p>
                  <footer className={stageStyles.stepActions}>
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
                        setError(null);
                      }}
                      placeholder="例如：一名档案员发现三份可靠记录，都指向一段不存在的时间……"
                      maxLength={2000}
                      rows={8}
                      value={sourceText}
                    />
                    {confirmingExample ? (
                      <div className={stageStyles.exampleConfirm} role="alert">
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
                  继续关键追问
                  <Glyph name="arrow" />
                </button>
              </footer>
            </section>
          ) : null}

          {step === "questions" ? (
            <section
              className={stageStyles.stepView}
              aria-labelledby="questions-step-title"
            >
              <header className={stageStyles.stepHero}>
                <div>
                  <small>第 2 步 / 关键追问</small>
                  <h1 id="questions-step-title">只问会改变方向的问题。</h1>
                  <button
                    className={stageStyles.headerBackAction}
                    disabled={questionsPending}
                    onClick={() => openReachableStep("idea")}
                    type="button"
                  >
                    ← 返回原稿
                  </button>
                </div>
                <p>
                  首轮最多两问，且最多一道硬问题。回答后仍可继续补充可选追问，不会改写已有判断。
                </p>
              </header>

              <section className={stageStyles.sourceCapsule}>
                <span>当前起案原文</span>
                <p>{sourceText}</p>
                <SourceBadge source="user_original" />
              </section>

              {intakeFrozen ? (
                <p className={stageStyles.frozenNotice} role="status">
                  创作简报已冻结，回答只读。要修改内容，请到第 5 步选择“建立简报修订”。
                </p>
              ) : null}

              <div className={stageStyles.questionStack}>
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
                    <span
                      aria-hidden="true"
                      className={stageStyles.agentThinkingMark}
                    />
                    <div>
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
                    </div>
                  </div>
                ) : state.questions.length === 0 && !error ? (
                  <p className={stageStyles.emptyQuestions}>
                    Agent 判断当前原稿信息已足够，无需追问；可以直接形成创作简报。
                  </p>
                ) : questionGenerationFailed && !error ? (
                  <p className={stageStyles.emptyQuestions}>
                    Agent 未能生成关键追问。你可以返回原稿重试，或直接使用“手动建立简报”继续。
                  </p>
                ) : null}
                {state.questions.map((question) => {
                  const answer = answers[question.key];
                  const resolved = Boolean(answer);
                  return (
                    <article data-resolved={resolved} key={question.key}>
                      <header>
                        <span>Q{String(question.ordinal).padStart(2, "0")}</span>
                        <div>
                          <h2>{question.prompt}</h2>
                          <p>{question.impact}</p>
                        </div>
                        <em>{question.required ? "必须回答" : "可以暂缓"}</em>
                      </header>
                      <div className={stageStyles.answerComposer}>
                        <label htmlFor={"intake-answer-" + question.key}>
                          你的回答
                        </label>
                        <textarea
                          id={"intake-answer-" + question.key}
                          disabled={questionsPending || intakeFrozen}
                          onChange={(event) =>
                            updateAnswer(question.key, event.target.value)
                          }
                          placeholder="用一句话锁定你的方向……"
                          rows={3}
                          value={answer?.pending ? "" : answer?.text ?? ""}
                        />
                        <div className={stageStyles.suggestionList}>
                          <span>快速采用一个方向</span>
                          {question.suggestions.map((suggestion) => (
                            <button
                              disabled={questionsPending || intakeFrozen}
                              key={suggestion}
                              onClick={() =>
                                updateAnswer(
                                  question.key,
                                  suggestion,
                                  "agent_suggestion",
                                )
                              }
                              type="button"
                            >
                              {suggestion}
                            </button>
                          ))}
                        </div>
                        <footer>
                          {answer ? (
                            <SourceBadge source={answer.source} />
                          ) : (
                            <span>等待你的判断</span>
                          )}
                          {!question.required ? (
                            <button
                              disabled={questionsPending || intakeFrozen}
                              onClick={() => markQuestionPending(question.key)}
                              type="button"
                            >
                              稍后决定
                            </button>
                          ) : null}
                        </footer>
                      </div>
                    </article>
                  );
                })}
                {state.questions.length > 0 ? (
                  <section className={stageStyles.moreQuestions}>
                    <div>
                      <span>还想继续深挖？</span>
                      <p>保留现有问题和回答，再补充最多两道不重复的可选问题。</p>
                    </div>
                    <button
                      disabled={questionsPending || intakeFrozen}
                      onClick={generateMoreQuestions}
                      type="button"
                    >
                      {questionGenerationMode === "additional"
                        ? "正在补充问题…"
                        : "再生成一些问题"}
                    </button>
                  </section>
                ) : null}
              </div>

              {error ? (
                <p className={stageStyles.inlineError} role="alert">
                  {error}
                </p>
              ) : null}

              <footer className={stageStyles.stepActions}>
                <div>
                  <button
                    className={stageStyles.secondaryAction}
                    disabled={questionsPending || manualBriefPending || intakeFrozen}
                    onClick={continueManually}
                    type="button"
                  >
                    {manualBriefPending ? "正在建立人工简报…" : "手动建立简报"}
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
                    {intakeFrozen ? "简报已冻结" : "形成创作简报"}
                    <Glyph name="arrow" />
                  </button>
                </div>
              </footer>
            </section>
          ) : null}

          {step === "confirmation" && briefGenerationPending ? (
            <section
              aria-busy="true"
              aria-labelledby="confirmation-loading-title"
              className={stageStyles.stepView}
            >
              <header className={stageStyles.stepHero}>
                <div>
                  <small>第 3 步 / 形成简报</small>
                  <h1 id="confirmation-loading-title">
                    确认整体方向，再交给正式审阅。
                  </h1>
                </div>
                <p>
                  页面已经准备好。Agent 正在把原稿与已确认回答整理成可逐项审阅的创作简报。
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

          {step === "confirmation" && !briefGenerationPending ? (
            <section className={stageStyles.stepView} aria-labelledby="confirmation-step-title">
              <header className={stageStyles.stepHero}>
                <div>
                  <small>第 3 步 / 校核简报</small>
                  <h1 id="confirmation-step-title">确认整体方向，再交给正式审阅。</h1>
                  <button
                    className={stageStyles.headerBackAction}
                    disabled={briefGenerationPending || briefReviewPending}
                    onClick={() => {
                      setAuthorAnswerPending(false);
                      setAuthorAnswerSuggestion(null);
                      setAuthorAnswerError(null);
                      openReachableStep("questions");
                    }}
                    type="button"
                  >
                    ← 返回追问
                  </button>
                </div>
                <p>
                  每个字段都保留来源。表单修改和对话修改会产生新候选，不覆盖旧版本。
                </p>
              </header>

              {intakeFrozen ? (
                <p className={stageStyles.frozenNotice} role="status">
                  创作简报已冻结为 V
                  {String(state.frozenBriefVersion ?? 1).padStart(2, "0")}
                  ，当前为只读。需要修改请到第 5 步选择“建立简报修订”。
                </p>
              ) : null}

              <div className={stageStyles.confirmationToolbar}>
                <div>
                  <span>当前候选</span>
                  <b>{currentCandidate?.label ?? "人工简报"}</b>
                  <small>{currentCandidate?.createdAt ?? "尚未保存"}</small>
                </div>
                <div>
                  {intakeFrozen ? (
                    <span data-status="ready">已冻结只读</span>
                  ) : missingFields.length ? (
                    <span data-status="missing">还缺 {missingFields.length} 项</span>
                  ) : (
                    <span data-status="ready">可以采用</span>
                  )}
                  <button
                    disabled={
                      intakeFrozen || !currentCandidate || currentCandidate.bookmarked
                    }
                    onClick={() =>
                      currentCandidate && toggleBookmark(currentCandidate.id)
                    }
                    type="button"
                  >
                    {currentCandidate?.bookmarked ? "已保存候选" : "保存候选书签"}
                  </button>
                </div>
              </div>

              <fieldset
                className={stageStyles.briefEditor}
                disabled={intakeFrozen}
              >
                <FieldShell
                  hint="概括核心设定与冲突"
                  label="一句话概念"
                  required
                  source={brief.sources.concept}
                  wide
                >
                  <textarea
                    aria-label="一句话概念"
                    onChange={(event) =>
                      updateBriefField("concept", event.target.value)
                    }
                    placeholder="例如：四名玩家在不断重启的空间站中追查事故真相。"
                    rows={3}
                    value={brief.concept}
                  />
                </FieldShell>
                <FieldShell
                  hint="定义玩家最终必须回答的问题"
                  label="推理目标"
                  required
                  source={brief.sources.reasoningGoal}
                  wide
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
                  hint="决定验证器如何判断结论是否成立；每个项目必须明确选择。"
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
                      className={stageStyles.suggestionConfirm}
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
                  hint="选择答案由谁提供，以及深稿是否必须收束"
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
                    hint="只有已经知道答案时填写"
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
                          {authorAnswerPending ? "Agent 正在拟定…" : "让 Agent 先拟一版"}
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
                  hint="逐条编辑独立亮点，支持拖动排序"
                  label="核心卖点"
                  source={brief.sources.sellingPoints}
                  wide
                >
                  <SellingPointsEditor
                    onChange={(value) => updateBriefField("sellingPoints", value)}
                    value={brief.sellingPoints}
                  />
                </FieldShell>
                <FieldShell
                  hint="按阶段拆解推进与验证过程"
                  label="内容骨架"
                  source={brief.sources.outline}
                  wide
                >
                  <OutlineStagesEditor
                    onChange={(value) => updateBriefField("outline", value)}
                    value={brief.outline}
                  />
                </FieldShell>
                <FieldShell
                  hint="估算角色、场景与体验时长"
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
                  hint="提前标出容易失控的设计风险"
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

              <details className={stageStyles.constraintDrawer}>
                <summary>
                  <div>
                    <span>约束通道</span>
                    <b>约束抽屉</b>
                    <small>必须保留、禁止出现、规模、人数、时长与内容尺度</small>
                  </div>
                  <em>
                    {brief.constraints.filter((constraint) => constraint.statement.trim())
                      .length}{" "}
                    项已填写
                  </em>
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

              <section className={stageStyles.revisionStudio}>
                <div>
                  <span>
                    <Glyph name="spark" />
                  </span>
                  <div>
                    <b>对话修改</b>
                    <small>只提交这一轮指令，并从当前候选生成子版本。</small>
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
                  {dialogueRevisionPending ? "正在生成修改候选…" : "生成修改候选"}
                  <Glyph name="arrow" />
                </button>
              </section>

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
                            {candidate.createdAt}
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

              {error ? (
                <p className={stageStyles.inlineError} role="alert">
                  {error}
                </p>
              ) : null}

              <footer className={stageStyles.stepActions}>
                <div>
                  <button
                    className={stageStyles.secondaryAction}
                    disabled={
                      intakeFrozen ||
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
                    disabled={intakeFrozen || briefReviewPending}
                    onClick={enterBriefReview}
                    type="button"
                  >
                    {intakeFrozen
                      ? "简报已冻结"
                      : briefReviewPending
                        ? "正在进入审阅…"
                        : "进入创作简报审阅"}
                    <Glyph name="arrow" />
                  </button>
                </div>
              </footer>
            </section>
          ) : null}

          {step === "review" ? <BriefReviewStage /> : null}

          {step === "candidates" ? <DraftCandidatesStage /> : null}
        </main>

        <aside aria-label="实时简报映射" className={styles.liveBrief}>
          <header>
            <div>
              <span>实时简报</span>
              <b>实时简报映射</b>
            </div>
            <em>{completionCount}/5</em>
          </header>
          <div
            aria-label={"建案完成度 " + completionCount + "/5"}
            className={styles.signalMeter}
            role="progressbar"
            aria-valuemax={5}
            aria-valuemin={0}
            aria-valuenow={completionCount}
          >
            {completionSignals.map((signal) => (
              <i data-ready={signal.ready} key={signal.label} />
            ))}
          </div>
          <div className={styles.signalRows}>
            {completionSignals.map((signal) => (
              <section data-ready={signal.ready} key={signal.label}>
                <i aria-hidden="true" />
                <span>{signal.label}</span>
                <b>{signal.value}</b>
              </section>
            ))}
          </div>
          <section className={styles.liveExtract}>
            <header>
              <span>当前概念</span>
              <SourceBadge
                source={
                  brief.concept
                    ? brief.sources.concept
                    : sourceText
                      ? "user_original"
                      : "unresolved"
                }
              />
            </header>
            <p>
              {brief.concept ||
                sourceText ||
                "写下最初想法后，这里会持续映射建案结果。"}
            </p>
          </section>
          <section className={styles.pendingQueue}>
            <header>
              <span>待决定</span>
              <b>{pendingDecisionCount}</b>
            </header>
            {pendingDecisionCount > 0 ? (
              <>
                <p>已标记的偏好会在正式审阅时继续确认。</p>
                <button onClick={() => openReachableStep("questions")} type="button">
                  回到问题处理
                </button>
              </>
            ) : (
              <p>没有被隐藏的待决定事项。</p>
            )}
          </section>
        </aside>
      </div>

      <CaseHistoryDrawer
        currentProjectId={activeProjectId}
        onClose={() => setHistoryDrawerOpen(false)}
        onNotice={announce}
        onRestore={restoreProject}
        open={historyDrawerOpen}
      />
    </div>
  );
}
