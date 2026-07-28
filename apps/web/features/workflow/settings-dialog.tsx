"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { apiRequest, errorMessage, type ProviderSettingView } from "@/lib/api-client";
import { useWorkflowSession } from "@/store/workflow-store";

import styles from "./workflow.module.css";

const DEFAULT_MODEL = "gpt-5.6-sol";

export function SettingsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { actorId } = useWorkflowSession();
  const queryClient = useQueryClient();
  const [apiKey, setApiKey] = useState("");
  const [modelId, setModelId] = useState("");
  const settingQuery = useQuery({
    queryKey: ["provider-setting", actorId],
    queryFn: () => apiRequest<ProviderSettingView | null>("/settings/provider", { actorId }),
    enabled: open,
  });

  const effectiveModelId = modelId || settingQuery.data?.model_id || DEFAULT_MODEL;

  const saveMutation = useMutation({
    mutationFn: () =>
      apiRequest<ProviderSettingView>("/settings/provider", {
        actorId,
        method: "PUT",
        body: {
          api_key: apiKey,
          model_id: effectiveModelId.trim(),
          model_is_custom: effectiveModelId.trim() !== DEFAULT_MODEL,
        },
      }),
    onSuccess: async () => {
      setApiKey("");
      await queryClient.invalidateQueries({ queryKey: ["provider-setting", actorId] });
    },
  });

  if (!open) return null;

  return (
    <div className={styles.modalBackdrop} onMouseDown={onClose} role="presentation">
      <section
        aria-labelledby="settings-title"
        aria-modal="true"
        className={styles.settingsDialog}
        onMouseDown={(event) => event.stopPropagation()}
        role="dialog"
      >
        <header className={styles.dialogHeader}>
          <div>
            <small>USER SETTINGS / LOCAL</small>
            <h2 id="settings-title">设置</h2>
          </div>
          <button onClick={onClose} type="button">关闭</button>
        </header>

        <div className={styles.settingsLayout}>
          <nav aria-label="设置栏目">
            <button className={styles.activeSetting} type="button">模型与 API</button>
            <span>账户与认证 · 待接入</span>
          </nav>
          <form
            className={styles.settingsForm}
            onSubmit={(event) => {
              event.preventDefault();
              saveMutation.mutate();
            }}
          >
            <div className={styles.settingStatus}>
              <span>当前凭据</span>
              <b>
                {settingQuery.isLoading
                  ? "读取中"
                  : settingQuery.data
                    ? `${settingQuery.data.masked_api_key} · ${settingQuery.data.credential_status}`
                    : "尚未配置"}
              </b>
            </div>
            <label>
              <span>OpenAI API Key</span>
              <input
                autoComplete="off"
                onChange={(event) => setApiKey(event.target.value)}
                placeholder={settingQuery.data ? "输入新 Key 以替换现有凭据" : "sk-..."}
                required
                type="password"
                value={apiKey}
              />
              <small>Key 只发送到本地后端并以 AES-256-GCM 密文保存；前端不持久化明文。</small>
            </label>
            <label>
              <span>生成模型</span>
              <input
                onChange={(event) => setModelId(event.target.value)}
                required
                value={effectiveModelId}
              />
              <small>默认使用 {DEFAULT_MODEL}；自定义模型会被标记为用户配置。</small>
            </label>
            {saveMutation.isError ? (
              <p className={styles.formError}>{errorMessage(saveMutation.error)}</p>
            ) : null}
            {saveMutation.isSuccess ? (
              <p className={styles.formSuccess}>设置已加密保存，可以开始生成。</p>
            ) : null}
            <div className={styles.dialogActions}>
              <button className={styles.secondaryButton} onClick={onClose} type="button">取消</button>
              <button className={styles.primaryButton} disabled={saveMutation.isPending} type="submit">
                {saveMutation.isPending ? "保存中…" : "保存设置"}
              </button>
            </div>
          </form>
        </div>
      </section>
    </div>
  );
}
