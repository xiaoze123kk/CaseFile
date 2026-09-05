import { useEffect, useRef, useState } from "react";

import type { WorkbenchSeed } from "./analyst-fixture";
import styles from "./workbench-agent.module.css";
import { WorkbenchAgentComposer } from "./workbench-agent-composer";
import { WorkbenchAgentDesk } from "./workbench-agent-desk";
import { reasoningOutcomeLabels } from "./workbench-presenters";
import type { AgentSurface } from "./workbench-agent-surface";

interface AgentMessage {
  id: string;
  role: "user" | "agent";
  text: string;
}

function composeAgentReply(
  prompt: string,
  seed: WorkbenchSeed,
  unresolvedCount: number,
): string {
  if (/体检|问题/.test(prompt)) {
    const issueLines =
      seed.validationIssues
        .map((issue) => `· ${issue.severity} ${issue.title}（${issue.rule}）`)
        .join("\n") || "· 当前没有记录在案的问题";
    return `对“${seed.caseMeta.title}”的体检完成：\n\n${issueLines}\n\n时间线 ${seed.timelineEvents.length} 个事件，推理路径 ${seed.reasoningPaths.length} 条，当前 ${unresolvedCount} 个问题待人工决定。建议优先处理 S0。`;
  }
  if (/证据/.test(prompt)) {
    const evidenceItems = seed.caseObjects.filter(
      (object) => object.kind === "evidence",
    );
    const lines =
      evidenceItems
        .map((item) => {
          const referenced = seed.reasoningPaths
            .flatMap((path) =>
              path.steps.flatMap((step) => step.evidenceIds),
            )
            .filter((id) => id === item.id).length;
          return `· ${item.label}（${item.code}）：被 ${referenced} 处推理引用`;
        })
        .join("\n") || "· 卷宗中暂无证据对象";
    return `证据链摘要：\n\n${lines}\n\n问题依据可到对象上下文的“来源依据”核对。`;
  }
  if (/对比|竞争/.test(prompt)) {
    const lines =
      seed.reasoningPaths
        .map(
          (path) =>
            `· ${path.question} → ${reasoningOutcomeLabels[path.outcome]}`,
        )
        .join("\n") || "· 卷宗中暂无推理路径";
    const contested = seed.reasoningPaths.some(
      (path) => path.outcome === "contested",
    );
    return `候选解释对比：\n\n${lines}\n\n${
      contested
        ? "仍存在竞争解释，冻结前建议补齐证据。"
        : "当前解释已收束，可以进入导出门禁。"
    }`;
  }
  if (/导出|门禁/.test(prompt)) {
    return `导出前检查（${seed.caseMeta.revision}）：\n\n· 结构完整性 — 通过\n· 引用可追溯 — 通过\n· 语义验证 — ${
      unresolvedCount > 0 ? `阻断（${unresolvedCount} 个问题）` : "通过"
    }\n· 作者批准 — 待确认\n\n${
      unresolvedCount > 0
        ? `先处理证据对比中的 ${unresolvedCount} 个问题。`
        : "门禁通过，可以生成导出包。"
    }`;
  }
  return `已收到：${prompt}\n\n该指令已记入卷宗统筹队列。目前卷宗共有 ${seed.caseObjects.length} 个对象、${seed.timelineEvents.length} 个事件、${unresolvedCount} 个待处理问题；可以使用上方统筹指令获得针对性分析。`;
}

export function AgentPanel({
  seed,
  unresolvedCount,
  surface,
  onContinueInDesk,
  disabled = false,
  focusRequest = 0,
}: {
  seed: WorkbenchSeed;
  unresolvedCount: number;
  onClose: () => void;
  surface: AgentSurface;
  onContinueInDesk: () => void;
  disabled?: boolean;
  focusRequest?: number;
}) {
  const [messages, setMessages] = useState<AgentMessage[]>([
    {
      id: "AG-0",
      role: "agent",
      text: `我是卷宗统筹 Agent，可以围绕“${seed.caseMeta.title}”做全卷宗体检、证据链摘要、候选解释对比与导出前检查。`,
    },
  ]);
  const [draft, setDraft] = useState("");
  const [thinking, setThinking] = useState(false);
  const timersRef = useRef<number[]>([]);

  useEffect(
    () => () => timersRef.current.forEach((timer) => window.clearTimeout(timer)),
    [],
  );


  function send(prompt: string) {
    const normalized = prompt.trim();
    if (!normalized || thinking) return;
    setMessages((previous) => [
      ...previous,
      { id: `US-${previous.length}`, role: "user", text: normalized },
    ]);
    setDraft("");
    setThinking(true);
    const timer = window.setTimeout(() => {
      setMessages((previous) => [
        ...previous,
        {
          id: `AG-${previous.length}`,
          role: "agent",
          text: composeAgentReply(normalized, seed, unresolvedCount),
        },
      ]);
      setThinking(false);
    }, 420);
    timersRef.current.push(timer);
  }

  return (
    <WorkbenchAgentDesk
      composer={
        <WorkbenchAgentComposer
          busy={thinking}
          disabled={disabled || thinking}
          draft={draft}
          onDraftChange={setDraft}
          onSend={() => { if (!draft.trim() || disabled || thinking) return; send(draft); onContinueInDesk(); }}
          focusRequest={focusRequest}
          surface={surface === "dock" || surface === "side" ? "dock" : "desk"}
        />
      }
      conversation={<div aria-live="polite" className={styles.agentMessages}>
        {messages.map((message) => (
          <p
            className={styles.agentMessage}
            data-role={message.role}
            key={message.id}
          >
            {message.text}
          </p>
        ))}
        {thinking ? (
          <p className={styles.agentThinking}>Agent 正在统筹卷宗…</p>
        ) : null}
      </div>}
      prompts={null}
      surface={surface}
      taskStrip={null}
    />
  );
}
