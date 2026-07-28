"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useEffect, useMemo, useState } from "react";

import {
  agentThreadMatchesQuery,
  sortAgentThreads,
} from "@/lib/prototype-model";
import { usePrototype } from "@/store/prototype-store";

import { RealArchiveShell } from "./real-archive-shell";

const modules = [
  {
    no: "01",
    label: "建案中心",
    code: "CASE OPENING",
    href: "/demo",
    status: "可用",
  },
  {
    no: "02",
    label: "CaseFile 工作台",
    code: "DRAFT DESK",
    href: "/demo/workbench",
    status: "可用",
  },
  {
    no: "03",
    label: "推理实验室",
    code: "REASONING LAB",
    href: "/demo/reasoning",
    status: "可用",
  },
  {
    no: "04",
    label: "玩家模拟器",
    code: "SIMULATION",
    status: "规划",
  },
  {
    no: "05",
    label: "质量中心",
    code: "VALIDATION",
    href: "/demo/quality",
    status: "可用",
  },
  {
    no: "06",
    label: "多目标编译器",
    code: "COMPILER",
    href: "/demo/quality#compiler",
    status: "联动",
  },
  {
    no: "07",
    label: "审阅与发布",
    code: "RELEASE",
    status: "规划",
  },
];

function isModuleActive(pathname: string, href?: string) {
  if (!href) return false;
  if (href === "/demo") return pathname === "/demo" || pathname === "/demo/brief";
  if (href.startsWith("/demo/quality")) return pathname === "/demo/quality";
  return pathname.startsWith(href);
}

export function ArchiveShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  if (!pathname.startsWith("/demo")) return <RealArchiveShell>{children}</RealArchiveShell>;
  return <PrototypeArchiveShell>{children}</PrototypeArchiveShell>;
}

function PrototypeArchiveShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { state, reset } = usePrototype();
  const [notice, setNotice] = useState<string | null>(null);
  const [commandOpen, setCommandOpen] = useState(false);
  const [commandQuery, setCommandQuery] = useState("");

  const matchingThreads = useMemo(() => {
    const visibleThreads = sortAgentThreads(
      state.agent.history.filter(
        (thread) =>
          !thread.archived &&
          agentThreadMatchesQuery(thread, commandQuery),
      ),
    );
    return visibleThreads.slice(0, commandQuery.trim() ? 6 : 3);
  }, [commandQuery, state.agent.history]);

  useEffect(() => {
    function handleKeyboard(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setCommandOpen(true);
      }
      if (event.key === "Escape") {
        setCommandOpen(false);
      }
    }

    window.addEventListener("keydown", handleKeyboard);
    return () => window.removeEventListener("keydown", handleKeyboard);
  }, []);

  function resetPrototype() {
    if (window.confirm("确认清除本地原型状态并恢复初始样例？")) {
      reset();
      setNotice("本地原型已恢复为初始样例。");
      window.setTimeout(() => setNotice(null), 2400);
    }
  }

  function openAgentThread(threadId: string) {
    setCommandOpen(false);
    const hash = `#agent-thread=${encodeURIComponent(threadId)}`;
    if (pathname === "/demo/workbench") {
      window.history.replaceState(null, "", `/demo/workbench${hash}`);
      window.dispatchEvent(
        new CustomEvent("casefile:open-agent-thread", {
          detail: { threadId },
        }),
      );
      return;
    }
    router.push(`/demo/workbench${hash}`);
  }

  return (
    <div className="archive-app">
      <aside className="side-rail">
        <Link className="archive-brand" href="/demo" aria-label="CaseFile 演示首页">
          <span className="brand-mark" aria-hidden="true" />
          <span>
            <strong>CaseFile</strong>
            <small>推理卷宗</small>
          </span>
        </Link>

        <button
          className="start-case"
          onClick={() => setNotice("当前本地原型仅包含一个卷宗。")}
          type="button"
        >
          <span>
            <small>当前卷宗</small>
            {state.project.displayName}
          </span>
          <b aria-hidden="true">⌄</b>
        </button>

        <div className="nav-caption">
          <span>产品地图</span>
          <b>07 MODULES</b>
        </div>

        <nav className="archive-nav" aria-label="产品模块">
          {modules.map((module) => {
            const active = isModuleActive(pathname, module.href);
            const className = `nav-row ${active ? "is-active" : module.href ? "is-ready" : "is-planned"}`;
            const content = (
              <>
                <span className="nav-no">{module.no}</span>
                <span className="nav-label">
                  <strong>{module.label}</strong>
                  <small>{module.code}</small>
                </span>
                <span className="nav-state">{active ? "当前" : module.status}</span>
              </>
            );
            return module.href ? (
              <Link className={className} href={module.href} key={module.no}>
                {content}
              </Link>
            ) : (
              <button
                className={className}
                key={module.no}
                onClick={() =>
                  setNotice(`${module.label}属于后续阶段，本轮保留产品入口与职责说明。`)
                }
                type="button"
              >
                {content}
              </button>
            );
          })}
        </nav>

        <div className="rail-footer">
          <button
            onClick={() => setNotice("原型数据仅保存在当前浏览器 LocalStorage。")}
            type="button"
          >
            <span>□</span> 数据与安全
          </button>
          <button
            onClick={() => setNotice("当前采用浅色“数字档案纸”主题。")}
            type="button"
          >
            <span>◇</span> 项目设置
          </button>
          <button
            aria-label="打开当前用户菜单"
            className="user-card"
            onClick={() =>
              setNotice("当前为秦彻的本地个人空间；账户与偏好设置将在后续版本接入。")
            }
            type="button"
          >
            <span aria-hidden="true" className="user-avatar">
              秦
            </span>
            <span className="user-summary">
              <strong>秦彻</strong>
              <small>本地个人空间 · OWNER</small>
            </span>
            <span aria-hidden="true" className="user-menu-mark">
              •••
            </span>
          </button>
        </div>
      </aside>

      <section className="archive-canvas">
        <header className="utility-bar">
          <button
            className="search-field"
            onClick={() => setCommandOpen(true)}
            type="button"
          >
            <span className="search-icon" aria-hidden="true" />
            <span>搜索对象、线程、ID、引用或命令</span>
            <kbd>Ctrl K</kbd>
          </button>
          <div className="utility-actions">
            <button onClick={() => setNotice("已启用新手引导文案。")} type="button">
              新手模式
            </button>
            <button onClick={() => setCommandOpen(true)} type="button">
              命令面板
            </button>
            <button onClick={resetPrototype} type="button">
              重置原型
            </button>
            <Link className="utility-link" href="/">
              返回真实模式
            </Link>
          </div>
        </header>
        {children}
      </section>

      {notice ? (
        <div className="global-toast" role="status">
          <b>CASEFILE</b>
          <span>{notice}</span>
        </div>
      ) : null}

      {commandOpen ? (
        <div
          className="command-backdrop"
          onMouseDown={() => setCommandOpen(false)}
          role="presentation"
        >
          <section
            aria-label="命令面板"
            aria-modal="true"
            className="command-panel"
            onMouseDown={(event) => event.stopPropagation()}
            role="dialog"
          >
            <header>
              <span>快速导航 / COMMAND INDEX</span>
              <button onClick={() => setCommandOpen(false)} type="button">
                ESC
              </button>
            </header>
            <input
              autoFocus
              onChange={(event) => setCommandQuery(event.target.value)}
              placeholder="搜索页面、线程、对象 ID、REV 或 Validator…"
              value={commandQuery}
            />
            <div className="command-results">
              <section>
                <header>
                  <span>页面与命令</span>
                  <small>NAVIGATION</small>
                </header>
                <Link href="/demo" onClick={() => setCommandOpen(false)}>
                  <b>01</b>
                  <span>返回建案中心</span>
                  <small>GO /</small>
                </Link>
                <Link href="/demo/brief" onClick={() => setCommandOpen(false)}>
                  <b>02</b>
                  <span>审阅 Brief</span>
                  <small>GO /BRIEF</small>
                </Link>
                <Link href="/demo/workbench" onClick={() => setCommandOpen(false)}>
                  <b>03</b>
                  <span>打开事件工作台</span>
                  <small>GO /WORKBENCH</small>
                </Link>
                <Link href="/demo/reasoning" onClick={() => setCommandOpen(false)}>
                  <b>04</b>
                  <span>进入推理实验室</span>
                  <small>GO /REASONING</small>
                </Link>
                <Link href="/demo/quality" onClick={() => setCommandOpen(false)}>
                  <b>05</b>
                  <span>查看质量门禁</span>
                  <small>GO /QUALITY</small>
                </Link>
              </section>
              <section>
                <header>
                  <span>协作线程</span>
                  <small>{matchingThreads.length} MATCHES</small>
                </header>
                {matchingThreads.map((thread) => (
                  <button
                    key={thread.id}
                    onClick={() => openAgentThread(thread.id)}
                    type="button"
                  >
                    <b>TH</b>
                    <span>
                      <strong>{thread.label}</strong>
                      <small>
                        {thread.id} · REV.{thread.baseRevision} ·{" "}
                        {thread.summary}
                      </small>
                    </span>
                    <i>OPEN ↗</i>
                  </button>
                ))}
                {matchingThreads.length === 0 ? (
                  <p>没有找到匹配的协作线程。</p>
                ) : null}
              </section>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}
