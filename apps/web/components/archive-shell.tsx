"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { type ReactNode, useEffect, useState } from "react";

import { usePrototype } from "@/store/prototype-store";

const modules = [
  {
    no: "01",
    label: "建案中心",
    code: "CASE OPENING",
    href: "/",
    status: "可用",
  },
  {
    no: "02",
    label: "CaseFile 工作台",
    code: "DRAFT DESK",
    href: "/workbench",
    status: "可用",
  },
  {
    no: "03",
    label: "推理实验室",
    code: "REASONING LAB",
    status: "规划",
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
    href: "/quality",
    status: "可用",
  },
  {
    no: "06",
    label: "多目标编译器",
    code: "COMPILER",
    href: "/quality#compiler",
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
  if (href === "/") return pathname === "/" || pathname === "/brief";
  if (href.startsWith("/quality")) return pathname === "/quality";
  return pathname.startsWith(href);
}

export function ArchiveShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { state, reset } = usePrototype();
  const [notice, setNotice] = useState<string | null>(null);
  const [commandOpen, setCommandOpen] = useState(false);

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

  return (
    <div className="archive-app">
      <aside className="side-rail">
        <Link className="archive-brand" href="/" aria-label="CaseFile 首页">
          <span className="brand-mark">CF</span>
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
          <div className="system-seal">
            <span>CASEFILE / SCHEMA 0.1.0</span>
            <strong>LOCAL PROTOTYPE</strong>
          </div>
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
            <span>搜索对象、正文、ID、引用或命令</span>
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
            <button className="operator" type="button">
              OP / 秦
            </button>
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
            <input autoFocus placeholder="输入页面、对象 ID 或命令…" />
            <div>
              <Link href="/" onClick={() => setCommandOpen(false)}>
                <b>01</b><span>返回建案中心</span><small>GO /</small>
              </Link>
              <Link href="/brief" onClick={() => setCommandOpen(false)}>
                <b>02</b><span>审阅 Brief</span><small>GO /BRIEF</small>
              </Link>
              <Link href="/workbench" onClick={() => setCommandOpen(false)}>
                <b>03</b><span>打开事件工作台</span><small>GO /WORKBENCH</small>
              </Link>
              <Link href="/quality" onClick={() => setCommandOpen(false)}>
                <b>04</b><span>查看质量门禁</span><small>GO /QUALITY</small>
              </Link>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}
