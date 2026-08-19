export const agentPromptPresets = [
  {
    id: "inspect",
    label: "全卷宗体检",
    prompt:
      "对整个卷宗做一次体检：按验证问题的严重程度分级列出待处理问题，并说明时间线与推理的收束情况。",
    routingHint: { entrypoint: "preset", preset_id: "inspect" },
  },
  {
    id: "evidence",
    label: "证据链摘要",
    prompt:
      "汇总当前证据链：每份关键证据支撑了哪些主张，支撑不完整或存在断点的地方请如实指出。",
    routingHint: { entrypoint: "preset", preset_id: "evidence" },
  },
  {
    id: "compare",
    label: "候选解释对比",
    prompt:
      "对比卷宗中实际存在的候选解释与推理路径的收束状态，指出仍存在竞争的解释。",
    routingHint: { entrypoint: "preset", preset_id: "compare" },
  },
  {
    id: "gate",
    label: "导出前检查",
    prompt:
      "按编译中心的发布门禁口径做导出前检查，结论必须与验证快照一致。",
    routingHint: { entrypoint: "preset", preset_id: "gate" },
  },
  {
    id: "audit",
    label: "逻辑漏洞复查",
    prompt:
      "对当前卷宗做一次全卷逻辑漏洞复查：找出矛盾、断链、时序错误和动机缺口；能给出可审阅补丁的就给出补丁，无法取证的列到待人工确认，未发现漏洞则如实说明。",
    routingHint: { entrypoint: "preset", preset_id: "audit" },
  },
] as const;

export type AgentPromptPresetId = (typeof agentPromptPresets)[number]["id"];
