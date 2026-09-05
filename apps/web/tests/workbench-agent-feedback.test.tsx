import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { emptyFeedback, reduceFeedback, activeFeedbackRefs } from "@/features/analyst-workbench/workbench-agent-feedback";
import { AgentAttentionSurface } from "@/features/analyst-workbench/workbench-agent-attention";
import { AgentProgress } from "@/features/analyst-workbench/workbench-agent-progress";

import { AgentAnswer } from "@/features/analyst-workbench/workbench-agent-conversation";

afterEach(cleanup);
describe("assistant answer layout", () => {
  it("renders paragraphs and ordered actions with their starting priority", () => {
    const { container } = render(<AgentAnswer text={"先处理时间线。\n\n2. 调整认知范围。\n3. 补充线索。\n\n等待审阅。"} />);
    expect(container.querySelectorAll("p")).toHaveLength(2);
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByRole("list")).toHaveAttribute("start", "2");
  });
  it("keeps plain text and untrusted HTML as text", () => {
    const { container } = render(<AgentAnswer text={"<script>alert(1)</script>\n普通正文"} />);
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelectorAll("p")).toHaveLength(1);
    expect(screen.queryByRole("list")).toBeNull();
  });
  it("handles a list as preview lines arrive", () => {
    const { rerender } = render(<AgentAnswer text={"- 第一项\n- "} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    rerender(<AgentAnswer text={"- 第一项\n- 第二项"} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });
});
describe("in-message progress visibility", () => {
  const feedback = reduceFeedback(emptyFeedback(), {
    event: "run.verification", sequence: 1, verification_status: "blocked",
    summary: "发现需要作者复查的内容。",
  });

  it.each(["succeeded", "failed", "cancelled"] as const)("removes the status block after %s", (status) => {
    const { container } = render(<AgentProgress feedback={feedback} run={{
      run_id: 80, status, activity: null, cancellable: false, failure: null,
    }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("preserves required controls without the completed status decoration", () => {
    render(<AgentProgress feedback={feedback} run={{
      run_id: 80, status: "succeeded", activity: null, cancellable: false, failure: null,
    }} controls={<button>继续处理</button>} />);
    expect(screen.getByRole("button", { name: "继续处理" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "工作记录" })).not.toBeInTheDocument();
    expect(screen.queryByText("发现需要作者复查的内容。")).not.toBeInTheDocument();
  });

  it("keeps progress and stop controls while running", () => {
    render(<AgentProgress run={{
      run_id: 80, status: "running", activity: null, cancellable: true, failure: null,
    }} controls={<button>停止回复</button>} />);
    expect(screen.getByRole("region", { name: "工作记录" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "停止回复" })).toBeInTheDocument();
  });
});

describe("public feedback replay", () => {
  it("deduplicates events, uses Unicode offsets, and isolates retries", () => {
    let state = reduceFeedback(emptyFeedback(), { event: "message.preview_started", sequence: 1 });
    const delta = { event: "message.preview_delta" as const, sequence: 2, preview_sequence: 1, offset: 0, text: "😀线索" };
    state = reduceFeedback(state, delta);
    expect(reduceFeedback(state, delta)).toBe(state);
    state = reduceFeedback(state, { ...delta, sequence: 3, offset: 3, text: "。" });
    expect(state.preview?.text).toBe("😀线索。");
    state = reduceFeedback(state, { event: "message.preview_started", sequence: 4 });
    expect(state.preview?.text).toBe("");
    state = reduceFeedback(state, { ...delta, sequence: 5 });
    expect(state.gap).toBe(true);
  });
  it("discards unsafe previews and keeps verification blocks authoritative", () => {
    let state = reduceFeedback(emptyFeedback(), { event: "message.preview_started", sequence: 1 });
    state = reduceFeedback(state, { event: "message.preview_delta", sequence: 2, preview_sequence: 1, offset: 0, text: "预览" });
    state = reduceFeedback(state, { event: "message.preview_invalidated", sequence: 3, discard: true });
    expect(state.preview?.text).toBe("");
    state = reduceFeedback(state, { event: "run.verification", sequence: 4, verification_status: "blocked", summary: "请审阅" });
    state = reduceFeedback(state, { event: "run.verification", sequence: 5, verification_status: "passed", summary: "检查结束" });
    expect(state.verification?.verification_status).toBe("blocked");
  });
  it("requires exact draft identity and revision for attention", () => {
    const state = reduceFeedback(emptyFeedback(), { event: "run.activity_detail", sequence: 1, activity_id: 1, activity: "reading", status: "completed", object_ids: ["a"], draft_id: 3, draft_revision: 4 });
    expect(activeFeedbackRefs(state, 3, 4)).toEqual(["a"]);
    expect(activeFeedbackRefs(state, 3, 5)).toEqual([]);
    expect(activeFeedbackRefs(state, 9, 4)).toEqual([]);
  });
  it("attention never changes selection or adds indirect related objects", () => {
    const { rerender } = render(<AgentAttentionSurface ids={["a"]}>
      <button data-agent-object-id="a" aria-pressed="false">人物甲</button>
      <button data-agent-object-id="b" aria-pressed="true">人物乙</button>
    </AgentAttentionSurface>);
    expect(screen.getByText("人物甲")).toHaveAttribute("data-agent-focus", "true");
    expect(screen.getByText("人物甲")).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("人物乙")).not.toHaveAttribute("data-agent-focus");
    rerender(<AgentAttentionSurface ids={[]}><button data-agent-object-id="a">人物甲</button></AgentAttentionSurface>);
    expect(screen.getByText("人物甲")).not.toHaveAttribute("data-agent-focus");
  });
});
