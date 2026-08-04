"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { type ReactNode, useState } from "react";

import { SettingsDialog } from "@/features/workflow/settings-dialog";
import { apiRequest, type ProjectView } from "@/lib/api-client";
import { useWorkflowSession } from "@/store/workflow-store";

const realModules = [
  { no: "01", label: "建案中心", href: "/", status: "可用" },
  { no: "02", label: "创作简报审阅", href: "/brief", status: "可用" },
  { no: "03", label: "CaseFile 工作台", href: "/workbench", status: "可用" },
  { no: "04", label: "推理实验室", status: "后续接入" },
  { no: "05", label: "玩家模拟器", status: "规划" },
  { no: "06", label: "质量中心", status: "后续接入" },
  { no: "07", label: "编译与发布", status: "规划" },
] as const;

function isActive(pathname: string, href: string) {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

export function ArchiveShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const workflow = useWorkflowSession();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const projectQuery = useQuery({
    queryKey: ["project", workflow.actorId, workflow.projectId],
    queryFn: () =>
      apiRequest<ProjectView>(`/projects/${workflow.projectId}`, {
        actorId: workflow.actorId,
      }),
    enabled: workflow.ready && workflow.projectId !== null,
  });

  return (
    <div className="archive-app">
      <aside className="side-rail">
        <Link className="archive-brand" href="/" aria-label="CaseFile 真实工作区首页">
          <span className="brand-mark" aria-hidden="true" />
          <span>
            <strong>CaseFile</strong>
            <small>推理卷宗</small>
          </span>
        </Link>

        <Link className="start-case" href="/">
          <span>
            <small>当前真实案件</small>
            {projectQuery.data?.title ?? (workflow.projectId ? `项目 #${workflow.projectId}` : "尚未建案")}
          </span>
          <b aria-hidden="true">↗</b>
        </Link>

        <div className="nav-caption">
          <span>真实工作流</span>
          <b>07 个模块</b>
        </div>

        <nav className="archive-nav" aria-label="真实工作流模块">
          {realModules.map((module) => {
            const href = "href" in module ? module.href : undefined;
            const planned = href === undefined;
            const unavailable = !planned && href !== "/" && workflow.projectId === null;
            const active = href ? isActive(pathname, href) : false;
            const content = (
              <>
                <span className="nav-no">{module.no}</span>
                <span className="nav-label">
                  <strong>{module.label}</strong>
                </span>
                <span className="nav-state">
                  {unavailable ? "待建案" : active ? "当前" : module.status}
                </span>
              </>
            );
            return planned || unavailable ? (
              <span className="nav-row is-planned" key={module.no} aria-disabled="true">
                {content}
              </span>
            ) : (
              <Link
                className={`nav-row ${active ? "is-active" : "is-ready"}`}
                href={href}
                key={module.no}
              >
                {content}
              </Link>
            );
          })}
        </nav>

        <div className="rail-footer">
          <div className="real-mode-note">
            <b>真实模式</b>
            <Link className="real-mode-demo-link" href="/demo">
              演示模式 ↗
            </Link>
            <span>PostgreSQL 持久化 · 单 Agent · SSE 审计轨迹</span>
          </div>
          <button
            aria-label="打开设置"
            className="user-card"
            onClick={() => setSettingsOpen(true)}
            type="button"
          >
            <span aria-hidden="true" className="user-avatar">本</span>
            <span className="user-summary">
              <strong>本地用户</strong>
              <small>认证功能待接入</small>
            </span>
            <span aria-hidden="true" className="user-menu-mark">•••</span>
          </button>
        </div>
      </aside>

      <section className="archive-canvas">
        {children}
      </section>

      <SettingsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </div>
  );
}
