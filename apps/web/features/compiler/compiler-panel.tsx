"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { PanelHeader, StatusBadge } from "@/components/archive-ui";
import { canCompilePrototype, hasBlockingIssue } from "@/lib/prototype-model";
import { usePrototype } from "@/store/prototype-store";

import styles from "./compiler-panel.module.css";

const profileDetails = {
  standard: {
    label: "标准发行包",
    code: "STD",
    note: "主持人手册、玩家材料与稳定事实",
  },
  developer: {
    label: "开发审阅包",
    code: "DEV",
    note: "附带对象索引、规则报告与 Source Map",
  },
  casefile: {
    label: "CaseFile 归档",
    code: "CFA",
    note: "结构化卷宗与不可变验证清单",
  },
} as const;

export function CompilerPanel() {
  const { state, dispatch } = usePrototype();
  const [message, setMessage] = useState<string | null>(null);
  const compileTimer = useRef<number | null>(null);
  const canCompile = canCompilePrototype(state);
  const blocking = hasBlockingIssue(state);

  useEffect(
    () => () => {
      if (compileTimer.current !== null) {
        window.clearTimeout(compileTimer.current);
      }
    },
    [],
  );

  const selectedArtifacts = useMemo(
    () => state.compiler.artifacts.filter((artifact) => artifact.selected),
    [state.compiler.artifacts],
  );

  function explainCompileBlock() {
    if (state.validation.status === "running") {
      setMessage("验证仍在运行。编译器会等待当前报告完成并固定快照。");
      return;
    }
    if (state.validation.status === "stale") {
      setMessage(
        `报告只覆盖 REV.${state.validation.snapshotRevision}，当前草稿为 REV.${state.draft.revision}；请先重新验证。`,
      );
      return;
    }
    if (blocking) {
      setMessage("存在未解决的 S1 阻断项 VAL-KNOW-001，禁止生成发布产物。");
      return;
    }
    if (selectedArtifacts.length === 0) {
      setMessage("至少选择一个产物后才能开始构建。");
    }
  }

  function startCompile() {
    if (!canCompile || selectedArtifacts.length === 0) {
      explainCompileBlock();
      return;
    }

    dispatch({ type: "start-compile" });
    setMessage(
      `已固定 SNAP-REV-${state.validation.snapshotRevision}，正在构建 ${selectedArtifacts.length} 个产物…`,
    );
    if (compileTimer.current !== null) {
      window.clearTimeout(compileTimer.current);
    }
    compileTimer.current = window.setTimeout(() => {
      dispatch({ type: "complete-compile" });
      setMessage("构建完成。所有产物均可追踪到固定验证快照。");
      compileTimer.current = null;
    }, 1550);
  }

  const compilerState =
    state.compiler.status === "building"
      ? "构建中"
      : state.compiler.status === "completed"
        ? "已完成"
        : canCompile
          ? "待构建"
          : "已封存";

  return (
    <aside className={`paper-panel ${styles.compilerPanel}`} id="compiler">
      <PanelHeader
        code="TARGET ADAPTER / FIXED SNAPSHOT"
        title="多目标编译器"
        trailing={
          <StatusBadge
            tone={
              state.compiler.status === "completed"
                ? "dark"
                : canCompile
                  ? "neutral"
                  : "red"
            }
          >
            {compilerState}
          </StatusBadge>
        }
      />

      <div className={styles.compilerBody}>
        <section
          className={`${styles.snapshotCard} ${
            state.validation.status !== "fresh" ? styles.snapshotStale : ""
          }`}
        >
          <div className={styles.snapshotSeal} aria-hidden="true">
            SNAP
          </div>
          <div>
            <span>编译输入 / IMMUTABLE SOURCE</span>
            <strong>SNAP-REV-{state.validation.snapshotRevision}</strong>
            <small>
              {state.validation.runId} · {state.project.version} · SHA256 7D9A…42E1
            </small>
          </div>
          <b>
            {state.validation.status === "fresh"
              ? "已校验"
              : state.validation.status === "running"
                ? "锁定中"
                : "已过期"}
          </b>
        </section>

        <fieldset className={styles.profilePicker}>
          <legend>构建配置 / BUILD PROFILE</legend>
          {(Object.keys(profileDetails) as Array<keyof typeof profileDetails>).map(
            (profile) => {
              const detail = profileDetails[profile];
              return (
                <label
                  className={
                    state.compiler.profile === profile ? styles.profileSelected : ""
                  }
                  key={profile}
                >
                  <input
                    checked={state.compiler.profile === profile}
                    name="compiler-profile"
                    onChange={() =>
                      dispatch({ type: "set-compiler-profile", profile })
                    }
                    type="radio"
                  />
                  <b>{detail.code}</b>
                  <span>
                    <strong>{detail.label}</strong>
                    <small>{detail.note}</small>
                  </span>
                </label>
              );
            },
          )}
        </fieldset>

        <section className={styles.artifactSection}>
          <header>
            <div>
              <span>产物清单 / ARTIFACT MANIFEST</span>
              <small>
                {selectedArtifacts.length}/{state.compiler.artifacts.length} SELECTED
              </small>
            </div>
            <button
              onClick={() =>
                state.compiler.artifacts
                  .filter((artifact) => !artifact.selected)
                  .forEach((artifact) =>
                    dispatch({ type: "toggle-artifact", id: artifact.id }),
                  )
              }
              type="button"
            >
              全选
            </button>
          </header>

          <div className={styles.artifactList}>
            {state.compiler.artifacts.map((artifact, index) => (
              <label key={artifact.id}>
                <input
                  checked={artifact.selected}
                  onChange={() =>
                    dispatch({ type: "toggle-artifact", id: artifact.id })
                  }
                  type="checkbox"
                />
                <span className={styles.artifactIndex}>
                  {String(index + 1).padStart(2, "0")}
                </span>
                <span className={styles.artifactName}>
                  <strong>{artifact.name}</strong>
                  <small>{artifact.description}</small>
                </span>
                <b>{artifact.size}</b>
              </label>
            ))}
          </div>
        </section>

        <div className={styles.buildReceipt}>
          <span>
            <small>输入修订</small>
            <b>REV.{state.validation.snapshotRevision}</b>
          </span>
          <span>
            <small>目标配置</small>
            <b>{profileDetails[state.compiler.profile].code}</b>
          </span>
          <span>
            <small>产物</small>
            <b>{String(selectedArtifacts.length).padStart(2, "0")}</b>
          </span>
          <span>
            <small>门禁</small>
            <b className={canCompile ? styles.receiptPassed : styles.receiptBlocked}>
              {canCompile ? "PASS" : "HOLD"}
            </b>
          </span>
        </div>

        <button
          className={`${styles.compileButton} ${
            canCompile ? styles.compileButtonReady : ""
          }`}
          disabled={state.compiler.status === "building"}
          onClick={startCompile}
          type="button"
        >
          <span>
            {state.compiler.status === "building"
              ? "正在编译固定快照"
              : state.compiler.status === "completed"
                ? "重新构建发布包"
                : canCompile
                  ? "开始构建"
                  : "编译已阻止"}
          </span>
          <b>
            {state.compiler.status === "building"
              ? "BUILDING…"
              : state.compiler.status === "completed"
                ? "BUILD AGAIN ↗"
                : canCompile
                  ? "COMPILE ↗"
                  : "查看原因 →"}
          </b>
        </button>

        <div className={styles.compilerMessage} role="status">
          <i
            className={
              state.compiler.status === "completed"
                ? styles.messageComplete
                : message
                  ? styles.messageActive
                  : ""
            }
          />
          <span>
            {message ??
              (canCompile
                ? "硬门禁已通过。编译器将锁定当前验证快照，不读取可变草稿。"
                : "编译器只接受有效验证快照；S1 或报告过期均会阻止构建。")}
          </span>
        </div>

        {state.compiler.status === "completed" ? (
          <section className={styles.completedPackage}>
            <div>
              <span>BUILD RECEIPT / CF-017-{state.validation.runId}</span>
              <strong>发布包构建完成</strong>
              <small>{selectedArtifacts.length} 个产物 · 校验和已写入清单</small>
            </div>
            <button onClick={() => setMessage("原型模式不生成真实文件。")} type="button">
              下载清单 ↓
            </button>
          </section>
        ) : null}
      </div>
    </aside>
  );
}
