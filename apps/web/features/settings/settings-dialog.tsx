"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  apiRequest,
  errorMessage,
  type ProviderName,
  type ProviderSettingView,
} from "@/lib/api-client";

import styles from "./settings.module.css";

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
    caption: "响应接口",
    defaultModel: "gpt-5.6-sol",
    models: ["gpt-5.6-sol"],
  },
  {
    id: "deepseek",
    label: "DeepSeek",
    caption: "对话补全接口",
    defaultModel: "deepseek-v4-flash",
    models: ["deepseek-v4-flash", "deepseek-v4-pro"],
  },
];

function credentialStatusLabel(status: string) {
  const labels: Record<string, string> = {
    unverified: "待验证",
    verified: "已验证",
    valid: "可用",
    invalid: "不可用",
  };
  return labels[status] ?? "状态待确认";
}

export function SettingsDialog({
  actorId,
  open,
  onClose,
}: {
  actorId: number;
  open: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [providerName, setProviderName] = useState<ProviderName>("openai");
  const [apiKey, setApiKey] = useState("");
  const [showApiKey, setShowApiKey] = useState(false);
  const [modelId, setModelId] = useState("");
  const [customModelMode, setCustomModelMode] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const provider = PROVIDERS.find((item) => item.id === providerName) ?? PROVIDERS[0];
  const settingQuery = useQuery({
    queryKey: ["provider-setting", actorId, providerName],
    queryFn: () =>
      apiRequest<ProviderSettingView | null>(
        `/settings/provider?provider=${providerName}`,
        { actorId },
      ),
    enabled: open,
  });

  const savedCustomModel = Boolean(settingQuery.data?.model_is_custom && !modelId);
  const customModel = customModelMode || savedCustomModel;
  const effectiveModelId = customModelMode
    ? modelId
    : modelId || settingQuery.data?.model_id || provider.defaultModel;

  const deleteMutation = useMutation({
    mutationFn: () =>
      apiRequest<void>(`/settings/provider?provider=${providerName}`, {
        actorId,
        method: "DELETE",
      }),
    onSuccess: async () => {
      setApiKey("");
      setShowApiKey(false);
      setConfirmDelete(false);
      await queryClient.invalidateQueries({
        queryKey: ["provider-setting", actorId, providerName],
      });
    },
  });

  const saveMutation = useMutation({
    mutationFn: () =>
      apiRequest<ProviderSettingView>("/settings/provider", {
        actorId,
        method: "PUT",
        body: {
          provider: providerName,
          api_key: apiKey,
          model_id: effectiveModelId.trim(),
          model_is_custom: !provider.models.includes(effectiveModelId.trim()),
        },
      }),
    onSuccess: async () => {
      setApiKey("");
      setShowApiKey(false);
      setConfirmDelete(false);
      deleteMutation.reset();
      await queryClient.invalidateQueries({
        queryKey: ["provider-setting", actorId, providerName],
      });
    },
  });

  function resetLocalForm() {
    setApiKey("");
    setShowApiKey(false);
    setModelId("");
    setCustomModelMode(false);
    setConfirmDelete(false);
    deleteMutation.reset();
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
            <small>本地用户设置</small>
            <h2 id="settings-title">设置</h2>
          </div>
          <button onClick={closeDialog} type="button">关闭</button>
        </header>

        <div className={styles.settingsLayout}>
          <nav aria-label="设置栏目">
            <button className={styles.activeSetting} type="button">API 密钥管理</button>
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
              <div>
                <span>当前凭据</span>
                <b>
                  {settingQuery.isLoading
                    ? "读取中"
                    : settingQuery.data
                      ? `${provider.label} ${settingQuery.data.masked_api_key} · ${credentialStatusLabel(settingQuery.data.credential_status)}`
                      : "尚未配置"}
                </b>
              </div>
              {settingQuery.data ? (
                <button
                  className={styles.deleteCredentialButton}
                  disabled={deleteMutation.isPending}
                  onClick={() => {
                    setConfirmDelete(true);
                    deleteMutation.reset();
                  }}
                  type="button"
                >
                  删除密钥
                </button>
              ) : null}
            </div>
            {confirmDelete && settingQuery.data ? (
              <section aria-label="确认删除 API 密钥" className={styles.credentialDanger}>
                <div>
                  <b>确认删除 {provider.label} 密钥？</b>
                  <p>密文将被清空，历史任务仍会保留；正在执行的任务结束前无法删除。</p>
                </div>
                <div>
                  <button
                    className={styles.secondaryButton}
                    disabled={deleteMutation.isPending}
                    onClick={() => setConfirmDelete(false)}
                    type="button"
                  >
                    继续保留
                  </button>
                  <button
                    className={styles.dangerButton}
                    disabled={deleteMutation.isPending}
                    onClick={() => deleteMutation.mutate()}
                    type="button"
                  >
                    {deleteMutation.isPending ? "删除中…" : "确认删除"}
                  </button>
                </div>
              </section>
            ) : null}
            <fieldset className={styles.providerFieldset}>
              <legend>模型供应商</legend>
              <div className={styles.providerGrid}>
                {PROVIDERS.map((item) => (
                  <button
                    aria-pressed={providerName === item.id}
                    className={providerName === item.id ? styles.providerActive : undefined}
                    key={item.id}
                    onClick={() => {
                      resetLocalForm();
                      setProviderName(item.id);
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
              <span>{provider.label} API 密钥</span>
              <span className={styles.secretInput}>
                <input
                  aria-label={`${provider.label} API 密钥`}
                  autoComplete="off"
                  onChange={(event) => setApiKey(event.target.value)}
                  placeholder={
                    settingQuery.data
                      ? "输入新的 API 密钥以替换当前凭据"
                      : "请输入 API 密钥"
                  }
                  required
                  type={showApiKey ? "text" : "password"}
                  value={apiKey}
                />
                <button
                  aria-label={showApiKey ? "隐藏 API 密钥" : "显示 API 密钥"}
                  aria-pressed={showApiKey}
                  disabled={!apiKey}
                  onClick={() => setShowApiKey((visible) => !visible)}
                  title={showApiKey ? "隐藏 API 密钥" : "显示 API 密钥"}
                  type="button"
                >
                  <svg aria-hidden="true" viewBox="0 0 24 24">
                    <path d="M2.8 12s3.4-5.2 9.2-5.2S21.2 12 21.2 12 17.8 17.2 12 17.2 2.8 12 2.8 12Z" />
                    <circle cx="12" cy="12" r="2.4" />
                    {showApiKey ? null : <path d="m4 4 16 16" />}
                  </svg>
                </button>
              </span>
              <small>密钥只发送到本地后端并以 AES-256-GCM 密文保存；前端不持久化明文。</small>
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
            {deleteMutation.isError ? (
              <p className={styles.formError}>{errorMessage(deleteMutation.error)}</p>
            ) : null}
            {saveMutation.isSuccess ? (
              <p className={styles.formSuccess}>设置已加密保存，可以开始生成。</p>
            ) : null}
            {deleteMutation.isSuccess ? (
              <p className={styles.formSuccess}>API 密钥已删除，可以随时重新添加。</p>
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
