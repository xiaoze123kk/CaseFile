export const workbenchViewOptions = [
  { id: "timeline", label: "时间线", shortLabel: "时" },
  { id: "relations", label: "关系图", shortLabel: "关" },
  { id: "reasoning", label: "推理图", shortLabel: "推" },
  { id: "map", label: "地图", shortLabel: "图" },
  { id: "dossier", label: "卷宗编辑器", shortLabel: "卷" },
  { id: "export", label: "导出预览", shortLabel: "出" },
  { id: "compile", label: "编译中心", shortLabel: "编" },
  { id: "evidence", label: "证据对比", shortLabel: "证" },
] as const;

export type WorkbenchView = (typeof workbenchViewOptions)[number]["id"];
