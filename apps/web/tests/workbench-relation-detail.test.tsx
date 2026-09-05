import type { CaseFile } from "@casefile/contracts";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import fixture from "../../../fixtures/casefiles/restart_loop.casefile.json";
import { WorkbenchCollaborationDetail } from "@/features/analyst-workbench/workbench-collaboration-detail";
import { buildContextRelations } from "@/features/analyst-workbench/workbench-relation-model";

const source = fixture as unknown as CaseFile;
afterEach(cleanup);

function renderDetail(document = source, relationId = `relationship:${source.relationships[0].id}`) {
  const onLocate = vi.fn();
  const onOpenDetail = vi.fn();
  const onAddContext = vi.fn();
  const onBack = vi.fn();
  const view = render(<WorkbenchCollaborationDetail
    detail={{ kind: "relation", objectId: "ent_researcher", relationId }}
    data={{ document, context: null, issues: [] }}
    onLocate={onLocate} onOpenDetail={onOpenDetail} onAddContext={onAddContext} onBack={onBack}
  />);
  return { ...view, onLocate, onOpenDetail, onAddContext, onBack };
}

describe("relation dossier", () => {
  it("shows names and relationship meaning without internal IDs or field paths", () => {
    const { container, onLocate, onOpenDetail, onAddContext, onBack } = renderDetail();
    expect(screen.getByRole("heading", { name: source.relationships[0].title })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "关联对象" })).toBeInTheDocument();
    for (const id of ["ent_researcher", "ent_backup_system", source.relationships[0].id]) {
      expect(container.textContent).not.toContain(id);
    }
    expect(screen.queryByText("字段来源")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "打开对象 林研究员" }));
    expect(onLocate).toHaveBeenCalledWith("ent_researcher");
    fireEvent.click(screen.getByRole("button", { name: "查看来源 备用控制系统" }));
    expect(onOpenDetail).toHaveBeenCalledWith({ kind: "provenance", objectId: "ent_backup_system" });
    fireEvent.click(screen.getByRole("button", { name: "添加到问题" }));
    expect(onAddContext).toHaveBeenCalledWith([
      { kind: "object", id: "ent_researcher", label: "林研究员" },
      { kind: "object", id: "ent_backup_system", label: "备用控制系统" },
    ]);
    fireEvent.click(screen.getByRole("button", { name: "返回" }));
    expect(onBack).toHaveBeenCalledOnce();
  });

  it("presents cognition without implying a causal arrow or exposing its source path", () => {
    const relation = buildContextRelations(source, "ent_researcher").groups
      .flatMap((group) => group.relations).find((item) => item.fieldLabel === "认知时点")!;
    const { container } = renderDetail(source, relation.id);
    expect(screen.getByRole("heading", { name: "认知时点" })).toBeInTheDocument();
    expect(container.querySelector('[data-direction="neutral"]')).toBeInTheDocument();
    expect(container.textContent).not.toContain(relation.fieldPath);
    expect(screen.getByRole("button", { name: "打开对象 系统第七次重启" })).toBeEnabled();
  });

  it("keeps missing targets readable, disables navigation and excludes them from message context", () => {
    const document = structuredClone(source);
    document.entities = document.entities.filter((item) => item.id !== "ent_backup_system");
    const { container, onAddContext } = renderDetail(document);
    const objects = screen.getByRole("region", { name: "关联对象" });
    expect(within(objects).getByText("此对象已不在当前工作稿中。")).toBeInTheDocument();
    expect(within(objects).getByRole("button", { name: /打开对象 已缺失/ })).toBeDisabled();
    expect(container.textContent).not.toContain("ent_backup_system");
    fireEvent.click(screen.getByRole("button", { name: "添加到问题" }));
    expect(onAddContext).toHaveBeenCalledWith([{ kind: "object", id: "ent_researcher", label: "林研究员" }]);
  });

  it("retains the missing-relation recovery message", () => {
    renderDetail(source, "relationship:removed");
    expect(screen.getByRole("status")).toHaveTextContent("内容已变化");
    expect(screen.getByRole("button", { name: "返回" })).toBeEnabled();
  });
});
