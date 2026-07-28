"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import {
  apiRequest,
  errorMessage,
  type BriefContent,
  type BriefView,
  type ProjectView,
} from "@/lib/api-client";
import { useWorkflowSession } from "@/store/workflow-store";

import styles from "./workflow.module.css";

export const DEFAULT_PROJECT_PROFILE = {
  content_type: "interactive_reasoning",
  target_audience: "adult_general",
  primary_use_case: "idea_to_playtest",
  genres: ["mystery"],
  target_duration_minutes: 90,
  target_participant_count: 4,
  difficulty_template: "medium",
  collaboration_mode: "single_lead_review",
};

const emptyBrief: BriefContent = {
  source_text: "",
  one_line_concept: "",
  core_mystery: "",
  player_goal: "",
  gameplay_loop: "",
  constraints: [],
  open_questions: [],
  project_profile: DEFAULT_PROJECT_PROFILE,
};

const fields: Array<{
  key: keyof Pick<
    BriefContent,
    "source_text" | "one_line_concept" | "core_mystery" | "player_goal" | "gameplay_loop"
  >;
  index: string;
  label: string;
  helper: string;
  placeholder: string;
}> = [
  {
    key: "source_text",
    index: "01",
    label: "原始创意",
    helper: "最初的想法、素材或故事火花；它只进入 Brief，不单独建表。",
    placeholder: "例如：一艘渡轮每天午夜都会重新驶回同一座码头……",
  },
  {
    key: "one_line_concept",
    index: "02",
    label: "一句话概念",
    helper: "用一句话说明玩家面对的情境与核心吸引力。",
    placeholder: "玩家需要在重复靠岸前找出让渡轮回航的真实原因。",
  },
  {
    key: "core_mystery",
    index: "03",
    label: "核心谜题",
    helper: "最终必须能被证据与推理回答的问题。",
    placeholder: "是谁修改了航行记录，回航是否在保护乘客？",
  },
  {
    key: "player_goal",
    index: "04",
    label: "玩家目标",
    helper: "玩家完成本案时应达成的可判断结果。",
    placeholder: "重建最后一小时的航行事实，并决定是否终止回航。",
  },
  {
    key: "gameplay_loop",
    index: "05",
    label: "玩法循环",
    helper: "玩家反复执行的关键动作与反馈闭环。",
    placeholder: "调查舱室、交换信息、提出假设、验证记录、做出决定。",
  },
];

export function IntakeWorkspace() {
  const router = useRouter();
  const workflow = useWorkflowSession();
  const [brief, setBrief] = useState<BriefContent>(emptyBrief);
  const currentProject = useQuery({
    queryKey: ["project", workflow.actorId, workflow.projectId],
    queryFn: () =>
      apiRequest<ProjectView>(`/projects/${workflow.projectId}`, {
        actorId: workflow.actorId,
      }),
    enabled: workflow.ready && workflow.projectId !== null,
  });

  const createMutation = useMutation({
    mutationFn: async () => {
      const project = await apiRequest<ProjectView>("/projects", {
        actorId: workflow.actorId,
        method: "POST",
        body: {
          title: brief.one_line_concept,
          description: brief.source_text,
          profile: DEFAULT_PROJECT_PROFILE,
        },
      });
      await apiRequest<BriefView>(`/projects/${project.id}/brief`, {
        actorId: workflow.actorId,
        method: "PUT",
        body: { expected_revision: 1, content: brief },
      });
      return project;
    },
    onSuccess: (project) => {
      workflow.setProject(project.id);
      router.push("/brief");
    },
  });

  return (
    <main className={styles.page}>
      <header className={styles.pageHeader}>
        <div>
          <small>CASE OPENING / LIVE DATABASE</small>
          <h1>把创意整理成可生成的 Brief</h1>
          <p>五项内容都是正式契约的必填输入。这里暂不调用模型，先把生成边界写清楚。</p>
        </div>
        <span className={styles.liveBadge}>真实模式</span>
      </header>

      {workflow.projectId ? (
        <section className={styles.resumeStrip}>
          <div>
            <small>当前会话</small>
            <b>{currentProject.data?.title ?? `项目 #${workflow.projectId}`}</b>
          </div>
          <Link href="/brief">继续审阅 Brief →</Link>
          <button onClick={workflow.clear} type="button">开始新案</button>
        </section>
      ) : null}

      <form
        className={styles.intakeForm}
        onSubmit={(event) => {
          event.preventDefault();
          createMutation.mutate();
        }}
      >
        <div className={styles.formLead}>
          <span>INTAKE FORM · 5 REQUIRED FIELDS</span>
          <p>系统会自动分配 Project、CaseFile、Draft 与 Brief ID。</p>
        </div>
        {fields.map((field) => (
          <label className={styles.intakeField} key={field.key}>
            <span className={styles.fieldIndex}>{field.index}</span>
            <span className={styles.fieldCopy}>
              <b>{field.label}</b>
              <small>{field.helper}</small>
            </span>
            <textarea
              onChange={(event) =>
                setBrief((current) => ({ ...current, [field.key]: event.target.value }))
              }
              placeholder={field.placeholder}
              required
              rows={field.key === "source_text" ? 4 : 3}
              value={brief[field.key]}
            />
          </label>
        ))}
        {createMutation.isError ? (
          <p className={styles.formError}>{errorMessage(createMutation.error)}</p>
        ) : null}
        <footer className={styles.formFooter}>
          <p>
            下一步只保存并审阅 Brief；确认后才会创建 TaskRun，并由 Worker 执行单 Agent 工具循环。
          </p>
          <button className={styles.primaryButton} disabled={createMutation.isPending} type="submit">
            {createMutation.isPending ? "正在建案…" : "保存并审阅 Brief"}
          </button>
        </footer>
      </form>
    </main>
  );
}
