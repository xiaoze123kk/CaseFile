"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  apiRequest,
  errorMessage,
  type ProviderName,
  type ProviderSettingView,
} from "@/lib/api-client";
import { useWorkflowSession } from "@/store/workflow-store";

import styles from "./workflow.module.css";

const PROVIDERS: Array<{
  id: ProviderName;
  label: string;
  caption: string;
  defaultModel: string;
  models: string[];
}> = [
  {
    id: "openai",
    label: "OpenAI",
    caption: "Responses API",
    defaultModel: "gpt-5.6-sol",
    models: ["gpt-5.6-sol"],
  },
  {
    id: "deepseek",
    label: "DeepSeek",
    caption: "Chat Completions",
    defaultModel: "deepseek-v4-flash",
    models: ["deepseek-v4-flash", "deepseek-v4-pro"],
  },
];

export function SettingsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const workflow = useWorkflowSession();
  const queryClient = useQueryClient();
  const [apiKey, setApiKey] = useState("");
  const [modelId, setModelId] = useState("");
  const [customModelMode, setCustomModelMode] = useState(false);
  const provider = PROVIDERS.find((item) => item.id === workflow.provider) ?? PROVIDERS[0];
  const settingQuery = useQuery({
    queryKey: ["provider-setting", workflow.actorId, workflow.provider],
    queryFn: () =>
      apiRequest<ProviderSettingView | null>(
        `/settings/provider?provider=${workflow.provider}`,
        { actorId: workflow.actorId },
      ),
    enabled: open,
  });

  const savedCustomModel = Boolean(settingQuery.data?.model_is_custom && !modelId);
  const customModel = customModelMode || savedCustomModel;
  const effectiveModelId = customModelMode
    ? modelId
    : modelId || settingQuery.data?.model_id || provider.defaultModel;

  const saveMutation = useMutation({
    mutationFn: () =>
      apiRequest<ProviderSettingView>("/settings/provider", {
        actorId: workflow.actorId,
        method: "PUT",
        body: {
          provider: workflow.provider,
          api_key: apiKey,
          model_id: effectiveModelId.trim(),
          model_is_custom: !provider.models.includes(effectiveModelId.trim()),
        },
      }),
    onSuccess: async () => {
      setApiKey("");
      await queryClient.invalidateQueries({
        queryKey: ["provider-setting", workflow.actorId, workflow.provider],
      });
    },
  });

  function resetLocalForm() {
    setApiKey("");
    setModelId("");
    setCustomModelMode(false);
    saveMutation.reset();
  }

  function closeDialog() {
    resetLocalForm();
    onClose();
  }

  if (!open) return null;

  return (
    <div className={styles.modalBackdrop} onMouseDown={closeDialog} role="presentation">
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
          <button onClick={closeDialog} type="button">关闭</button>
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
                    ? `${provider.label} ${settingQuery.data.masked_api_key} · ${settingQuery.data.credential_status}`
                    : "尚未配置"}
              </b>
            </div>
            <fieldset className={styles.providerFieldset}>
              <legend>模型供应商</legend>
              <div className={styles.providerGrid}>
                {PROVIDERS.map((item) => (
                  <button
                    aria-pressed={workflow.provider === item.id}
                    className={workflow.provider === item.id ? styles.providerActive : undefined}
                    key={item.id}
                    onClick={() => {
                      resetLocalForm();
                      workflow.setProvider(item.id);
                    }}
                    type="button"
                  >
                    <b>{item.label}</b>
                    <small>{item.caption}</small>
                  </button>
                ))}
              </div>
            </fieldset>
            <label>
              <span>{provider.label} API Key</span>
              <input
                autoComplete="off"
                onChange={(event) => setApiKey(event.target.value)}
                placeholder={settingQuery.data ? "输入新 Key 以替换当前供应商凭据" : "sk-..."}
                required
                type="password"
                value={apiKey}
              />
              <small>Key 只发送到本地后端并以 AES-256-GCM 密文保存；前端不持久化明文。</small>
            </label>
            <label>
              <span>生成模型</span>
              <select
                onChange={(event) => {
                  if (event.target.value === "__custom__") {
                    setCustomModelMode(true);
                    setModelId(settingQuery.data?.model_is_custom ? settingQuery.data.model_id : "");
                  } else {
                    setCustomModelMode(false);
                    setModelId(event.target.value);
                  }
                }}
                value={customModel ? "__custom__" : effectiveModelId}
              >
                {provider.models.map((model) => (
                  <option key={model} value={model}>{model}</option>
                ))}
                <option value="__custom__">自定义模型…</option>
              </select>
              {customModel ? (
                <input
                  autoFocus
                  onChange={(event) => setModelId(event.target.value)}
                  placeholder="输入兼容模型 ID"
                  required
                  value={effectiveModelId}
                />
              ) : null}
              <small>
                默认使用 {provider.defaultModel}；也可填写该供应商兼容的自定义模型。
              </small>
            </label>
            {saveMutation.isError ? (
              <p className={styles.formError}>{errorMessage(saveMutation.error)}</p>
            ) : null}
            {saveMutation.isSuccess ? (
              <p className={styles.formSuccess}>设置已加密保存，可以开始生成。</p>
            ) : null}
            <div className={styles.dialogActions}>
              <button className={styles.secondaryButton} onClick={closeDialog} type="button">取消</button>
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
