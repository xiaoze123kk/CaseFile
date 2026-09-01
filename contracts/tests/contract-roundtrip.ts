import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import Ajv2020, { type ErrorObject, type ValidateFunction } from "ajv/dist/2020.js";
import addFormats from "ajv-formats";

import type {
  BriefIntakeCandidate,
  BriefIntakeQuestionSet,
  CaseFile,
  CompileManifest,
  CompileInputManifest,
  CompilerArtifactRef,
  CompilerDiagnostic,
  CompilerSourceRef,
  NarrativeIR,
  NovelCandidate,
  NovelProfileV2,
  PatchCandidate,
  ProseConsensusReport,
  ProseJudgeChecklist,
  ProseJudgeReport,
  ProseQualityReport,
  SceneCompilerInputBundle,
  ScenePlanCandidate,
  ScenePlanIR,
  SceneRender,
  SceneRenderCandidate,
  PublicAgentEvent,
  PublicAgentMessage,
  PublicPatchReviewResult,
  PublicRoutingFeedbackReceipt,
  PublicRoutingInterpretation,
  ValidationIssue,
} from "../generated/typescript/index.js";

type JsonObject = Record<string, unknown>;
type Mutation = {
  op: "add" | "remove" | "replace";
  path: string;
  value?: unknown;
};

const repoRoot = resolve(fileURLToPath(new URL("../..", import.meta.url)));
const schemaRoot = resolve(repoRoot, "contracts", "schemas");
const fixtureRoot = resolve(repoRoot, "fixtures");

function loadJson(path: string): JsonObject {
  return JSON.parse(readFileSync(path, "utf8")) as JsonObject;
}

function collectJsonFiles(root: string): string[] {
  return readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(root, entry.name);
    return entry.isDirectory()
      ? collectJsonFiles(path)
      : entry.name.endsWith(".json")
        ? [path]
        : [];
  });
}

function decodePointer(pointer: string): string[] {
  if (!pointer.startsWith("/")) {
    throw new Error(`Invalid fixture JSON Pointer: ${pointer}`);
  }
  return pointer
    .slice(1)
    .split("/")
    .map((part) => part.replaceAll("~1", "/").replaceAll("~0", "~"));
}

function applyMutation(document: JsonObject, mutation: Mutation): JsonObject {
  const result = structuredClone(document);
  const parts = decodePointer(mutation.path);
  let parent: unknown = result;

  for (const part of parts.slice(0, -1)) {
    parent = Array.isArray(parent)
      ? parent[Number(part)]
      : (parent as JsonObject)[part];
  }

  const key = parts.at(-1);
  if (key === undefined) {
    throw new Error("Fixture mutation cannot target the document root");
  }

  if (Array.isArray(parent)) {
    const index = key === "-" ? parent.length : Number(key);
    if (mutation.op === "add") parent.splice(index, 0, mutation.value);
    else if (mutation.op === "remove") parent.splice(index, 1);
    else parent[index] = mutation.value;
  } else if (mutation.op === "remove") {
    delete (parent as JsonObject)[key];
  } else {
    (parent as JsonObject)[key] = mutation.value;
  }
  return result;
}

function applyManifest(base: JsonObject, manifest: JsonObject): JsonObject {
  const mutations = (manifest.mutations as Mutation[] | undefined) ?? [
    manifest.mutation as Mutation,
  ];
  return mutations.reduce(applyMutation, base);
}

function assertValid(
  validate: ValidateFunction,
  value: unknown,
  label: string,
): void {
  if (!validate(value)) {
    throw new Error(`${label} failed validation: ${JSON.stringify(validate.errors)}`);
  }
}

function normalizedErrorPath(error: ErrorObject): string {
  return error.instancePath || "/";
}

function typedRoundTrip<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

const ajv = new Ajv2020({ allErrors: true, strict: true });
addFormats(ajv);

for (const schemaPath of collectJsonFiles(schemaRoot)) {
  ajv.addSchema(loadJson(schemaPath));
}

const casefileValidator = ajv.getSchema(
  "https://casefile.local/schemas/v2/casefile/casefile.schema.json",
);
const issueValidator = ajv.getSchema(
  "https://casefile.local/schemas/v2/validation/validation-issue.schema.json",
);
const patchValidator = ajv.getSchema(
  "https://casefile.local/schemas/v2/casefile/patch-candidate.schema.json",
);
const briefIntakeCandidateValidator = ajv.getSchema(
  "https://casefile.local/schemas/v2/brief-intake/brief-intake.schema.json",
);
const briefIntakeQuestionSetValidator = ajv.getSchema(
  "https://casefile.local/schemas/v2/brief-intake/brief-intake.schema.json#/$defs/BriefIntakeQuestionSet",
);
const publicMessageValidator = ajv.getSchema(
  "https://casefile.local/schemas/v2/chat/chat-public.schema.json#/$defs/PublicAgentMessage",
);
const publicEventValidator = ajv.getSchema(
  "https://casefile.local/schemas/v2/chat/chat-public.schema.json#/$defs/PublicAgentEvent",
);
const publicPatchReviewValidator = ajv.getSchema(
  "https://casefile.local/schemas/v2/chat/chat-public.schema.json#/$defs/PublicPatchReviewResult",
);
const compilerManifestValidator = ajv.getSchema(
  "https://casefile.local/schemas/v2/compiler/compiler.schema.json#/$defs/CompileInputManifest",
);
const compilerSourceRefValidator = ajv.getSchema(
  "https://casefile.local/schemas/v2/compiler/compiler.schema.json#/$defs/CompilerSourceRef",
);
const compilerArtifactRefValidator = ajv.getSchema(
  "https://casefile.local/schemas/v2/compiler/compiler.schema.json#/$defs/CompilerArtifactRef",
);
const compilerDiagnosticValidator = ajv.getSchema(
  "https://casefile.local/schemas/v2/compiler/compiler.schema.json#/$defs/CompilerDiagnostic",
);
const narrativeIrValidator = ajv.getSchema(
  "https://casefile.local/schemas/v2/compiler/narrative-ir.schema.json",
);
const scenePlanValidator = ajv.getSchema(
  "https://casefile.local/schemas/v2/compiler/scene-plan.schema.json",
);
const sceneCompilerInputValidator = ajv.getSchema(
  "https://casefile.local/schemas/v2/compiler/scene-plan.schema.json#/$defs/SceneCompilerInputBundle",
);
const scenePlanCandidateValidator = ajv.getSchema(
  "https://casefile.local/schemas/v2/compiler/scene-plan.schema.json#/$defs/ScenePlanCandidate",
);

if (
  !casefileValidator ||
  !issueValidator ||
  !patchValidator ||
  !briefIntakeCandidateValidator ||
  !briefIntakeQuestionSetValidator ||
  !publicMessageValidator ||
  !publicEventValidator ||
  !publicPatchReviewValidator ||
  !compilerManifestValidator ||
  !compilerSourceRefValidator ||
  !compilerArtifactRefValidator ||
  !compilerDiagnosticValidator ||
  !narrativeIrValidator ||
  !sceneCompilerInputValidator ||
  !scenePlanCandidateValidator ||
  !scenePlanValidator
) {
  throw new Error("Editing contract entry schemas were not registered");
}

const casefilePaths = readdirSync(resolve(fixtureRoot, "casefiles"))
  .filter((name) => name.endsWith(".casefile.json"))
  .sort();

if (casefilePaths.length !== 5) {
  throw new Error(`Expected 5 valid CaseFile fixtures, found ${casefilePaths.length}`);
}

for (const name of casefilePaths) {
  const value = loadJson(resolve(fixtureRoot, "casefiles", name));
  assertValid(casefileValidator, value, name);
  const typed = typedRoundTrip(value as unknown as CaseFile);
  assertValid(casefileValidator, typed, `${name} generated TypeScript roundtrip`);
}

const issue = loadJson(resolve(fixtureRoot, "editing", "validation_issue.json"));
const patch = loadJson(resolve(fixtureRoot, "editing", "patch_candidate.json"));
assertValid(issueValidator, typedRoundTrip(issue as unknown as ValidationIssue), "ValidationIssue");
assertValid(patchValidator, typedRoundTrip(patch as unknown as PatchCandidate), "PatchCandidate");

const intakeCandidate: BriefIntakeCandidate = {
  concept: "一名档案员发现所有证词都指向一段不存在的时间。",
  core_selling_points: ["证词时间互相咬合"],
  content_outline: ["建立不可能时间", "交叉验证证词"],
  reasoning_goal: "解释可靠记录为何互相冲突。",
  resolution_mode: "open",
  conclusion_mode: "open_interpretation",
  author_answer: null,
  constraints: [],
  pending_decisions: [
    {
      decision_key: "decision_supporting_cast",
      prompt: "是否合并次要证人？",
      impact: "只影响规模，不改变核心解答。",
      source: "unresolved",
    },
  ],
  scope_estimate: "中篇",
  risk_notes: ["时间线信息密度较高"],
  field_sources: {
    concept: "user_original",
    core_selling_points: "agent_suggestion",
    content_outline: "agent_suggestion",
    reasoning_goal: "user_confirmed",
    resolution_mode: "user_confirmed",
    conclusion_mode: "user_confirmed",
    author_answer: "unresolved",
    constraints: "unresolved",
    scope_estimate: "agent_suggestion",
    risk_notes: "agent_suggestion",
  },
};
assertValid(
  briefIntakeCandidateValidator,
  typedRoundTrip(intakeCandidate),
  "BriefIntakeCandidate",
);

const questionSet: BriefIntakeQuestionSet = {
  questions: [
    {
      question_key: "question_truth_mode",
      ordinal: 1,
      prompt: "作者是否已经确定真相？",
      impact: "决定结论模式。",
      required: true,
      suggestions: ["由作者确定", "保持开放"],
    },
    {
      question_key: "question_scope",
      ordinal: 2,
      prompt: "预计采用多大规模？",
      impact: "影响内容骨架。",
      required: false,
      suggestions: ["中篇"],
    },
  ],
};
assertValid(
  briefIntakeQuestionSetValidator,
  typedRoundTrip(questionSet),
  "BriefIntakeQuestionSet",
);

const publicMessage = loadJson(
  resolve(fixtureRoot, "editing", "chat_public_message.json"),
);
assertValid(
  publicMessageValidator,
  typedRoundTrip(publicMessage as unknown as PublicAgentMessage),
  "PublicAgentMessage",
);
if (publicMessageValidator({ ...publicMessage, task: { provider: "deepseek" } })) {
  throw new Error("PublicAgentMessage accepted an internal Task payload");
}

const publicEvent: PublicAgentEvent = {
  sequence: 8,
  event: "run.context",
  context_state: "compacted",
};
assertValid(publicEventValidator, typedRoundTrip(publicEvent), "PublicAgentEvent");
if (publicEventValidator({ ...publicEvent, payload_jsonb: { token_count: 12000 } })) {
  throw new Error("PublicAgentEvent accepted an internal payload");
}

const publicPatchReview: PublicPatchReviewResult = {
  patch_id: 13,
  can_apply: false,
  blockers: [],
  warnings: [
    {
      notice_id: "warning_1",
      message: "删除会影响 2 项相关内容。",
    },
  ],
  requires_author_confirmation: true,
  confirmation_token: "confirmation-token",
};
assertValid(
  publicPatchReviewValidator,
  typedRoundTrip(publicPatchReview),
  "PublicPatchReviewResult",
);

const publicInterpretation: PublicRoutingInterpretation = "logic_review";
const publicFeedback: PublicRoutingFeedbackReceipt = {
  message_id: 56,
  acknowledged: true,
  interpretation: publicInterpretation,
};
const publicFeedbackValidator = ajv.getSchema(
  "https://casefile.local/schemas/v2/chat/chat-public.schema.json#/$defs/PublicRoutingFeedbackReceipt",
);
if (!publicFeedbackValidator) {
  throw new Error("PublicRoutingFeedbackReceipt schema was not registered");
}
assertValid(
  publicFeedbackValidator,
  typedRoundTrip(publicFeedback),
  "PublicRoutingFeedbackReceipt",
);
if (publicFeedbackValidator({ ...publicFeedback, route_source: "internal-canary" })) {
  throw new Error("PublicRoutingFeedbackReceipt accepted internal routing metadata");
}

const compilerFixtureRoot = resolve(fixtureRoot, "compiler", "foundation");
const typedCompilerSourceRef: CompilerSourceRef = {
  object_ref: {
    object_type: "event",
    object_id: "evt_archive_arrival",
  },
  field_path: "/time/start",
  source_fragment_hash: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
};
const typedCompilerManifest: CompileInputManifest = {
  target: "novel",
  mode: "preview",
  source_snapshot: {
    snapshot_id: 101,
    draft_id: 11,
    snapshot_revision: 7,
    schema_version: "2.0",
    content_hash: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  },
  source_canon: null,
  exposure: null,
  profile: {
    profile_key: "novel.default",
    profile_schema_id: "compiler.profile.v1",
    profile_version: 1,
    frozen_payload: { language: "zh-CN" },
    content_hash: "62873f1487d2b4dcf8bd68e2014c0f1ffd708f0537d0579f4401883491a7f7c6",
  },
  compiler_version: "narrative-compiler.v1",
};
assertValid(
  compilerSourceRefValidator,
  typedRoundTrip(typedCompilerSourceRef),
  "typed CompilerSourceRef",
);
assertValid(
  compilerManifestValidator,
  typedRoundTrip(typedCompilerManifest),
  "typed CompileInputManifest",
);
for (const name of [
  "preview_minimal.input_manifest.json",
  "canonical.input_manifest.json",
  "preview_with_exposure.input_manifest.json",
]) {
  const value = loadJson(resolve(compilerFixtureRoot, name));
  assertValid(
    compilerManifestValidator,
    typedRoundTrip(value as unknown as CompileInputManifest),
    name,
  );
}

const compilerSourceRef = loadJson(resolve(compilerFixtureRoot, "source_ref.json"));
const compilerArtifactRef = loadJson(resolve(compilerFixtureRoot, "artifact_ref.json"));
const compilerDiagnostic = loadJson(resolve(compilerFixtureRoot, "diagnostic.json"));
assertValid(
  compilerSourceRefValidator,
  typedRoundTrip(compilerSourceRef as unknown as CompilerSourceRef),
  "CompilerSourceRef",
);
assertValid(
  compilerArtifactRefValidator,
  typedRoundTrip(compilerArtifactRef as unknown as CompilerArtifactRef),
  "CompilerArtifactRef",
);
assertValid(
  compilerDiagnosticValidator,
  typedRoundTrip(compilerDiagnostic as unknown as CompilerDiagnostic),
  "CompilerDiagnostic",
);
const narrativeIr = loadJson(
  resolve(fixtureRoot, "compiler", "narrative_ir", "v1", "minimal.json"),
);
assertValid(
  narrativeIrValidator,
  typedRoundTrip(narrativeIr as unknown as NarrativeIR),
  "NarrativeIR",
);
const scenePlan = loadJson(
  resolve(fixtureRoot, "compiler", "scene_plan", "v1", "minimal.json"),
);
assertValid(
  scenePlanValidator,
  typedRoundTrip(scenePlan as unknown as ScenePlanIR),
  "ScenePlanIR",
);
const sceneCompilerInput = loadJson(
  resolve(
    fixtureRoot,
    "compiler",
    "scene_plan",
    "v1",
    "input.json",
  ),
);
assertValid(
  sceneCompilerInputValidator,
  typedRoundTrip(sceneCompilerInput as unknown as SceneCompilerInputBundle),
  "SceneCompilerInputBundle",
);
const scenePlanCandidate = loadJson(
  resolve(
    fixtureRoot,
    "scene_plan_benchmark",
    "v1",
    "references",
    "scene_decomposition__basic.json",
  ),
);
assertValid(
  scenePlanCandidateValidator,
  typedRoundTrip(scenePlanCandidate as unknown as ScenePlanCandidate),
  "ScenePlanCandidate",
);

const duplicateDiagnostic = structuredClone(compilerDiagnostic);
(duplicateDiagnostic.source_refs as unknown[]).push(
  structuredClone(compilerSourceRef),
);
if (compilerDiagnosticValidator(duplicateDiagnostic)) {
  throw new Error("CompilerDiagnostic accepted duplicate source refs");
}

const compilerInvalidCases = loadJson(
  resolve(compilerFixtureRoot, "invalid_cases.json"),
).cases as JsonObject[];
for (const invalidCase of compilerInvalidCases.filter(
  (value) => {
    const expectedLayers = value.expected_layers as string[] | undefined;
    return value.expected_layer === "schema" || expectedLayers?.includes("schema") === true;
  },
)) {
  const baseName = invalidCase.base_fixture as string;
  const invalidValue = applyManifest(
    loadJson(resolve(compilerFixtureRoot, baseName)),
    invalidCase,
  );
  const validator = baseName === "source_ref.json"
    ? compilerSourceRefValidator
    : baseName === "artifact_ref.json"
      ? compilerArtifactRefValidator
      : compilerManifestValidator;
  if (validator(invalidValue)) {
    throw new Error(`${String(invalidCase.name)} unexpectedly passed Compiler schema`);
  }
}

if (
  briefIntakeQuestionSetValidator({
    questions: questionSet.questions.map((question) => ({
      ...question,
      required: true,
    })),
  })
) {
  throw new Error("BriefIntakeQuestionSet accepted two hard questions");
}

const invalidRoot = resolve(fixtureRoot, "invalid", "schema");
const invalidManifests = readdirSync(invalidRoot)
  .filter((name) => name.endsWith(".json"))
  .sort();

if (invalidManifests.length !== 5) {
  throw new Error(`Expected 5 structural invalid fixtures, found ${invalidManifests.length}`);
}

for (const name of invalidManifests) {
  const manifest = loadJson(resolve(invalidRoot, name));
  const base = loadJson(resolve(invalidRoot, manifest.base_fixture as string));
  const invalidCasefile = applyManifest(base, manifest);
  if (casefileValidator(invalidCasefile)) {
    throw new Error(`${name} unexpectedly passed JSON Schema`);
  }
  const actualPaths = new Set(
    (casefileValidator.errors ?? []).map(normalizedErrorPath),
  );
  if (!actualPaths.has(manifest.expected_error_path as string)) {
    throw new Error(
      `${name} failed at ${JSON.stringify([...actualPaths])}, expected ${String(manifest.expected_error_path)}`,
    );
  }
}

const proseSchemaId =
  "https://casefile.local/schemas/v2/compiler/prose-rendering.schema.json";
const profileV2Validator = ajv.getSchema(
  "https://casefile.local/schemas/v2/compiler/novel-profile-v2.schema.json",
);
const proseChecklistValidator = ajv.getSchema(proseSchemaId);
const sceneRenderCandidateValidator = ajv.getSchema(
  `${proseSchemaId}#/$defs/SceneRenderCandidate`,
);
const sceneRenderValidator = ajv.getSchema(`${proseSchemaId}#/$defs/SceneRender`);
const proseJudgeReportValidator = ajv.getSchema(
  `${proseSchemaId}#/$defs/ProseJudgeReport`,
);
const proseConsensusReportValidator = ajv.getSchema(
  `${proseSchemaId}#/$defs/ProseConsensusReport`,
);
const proseQualityReportValidator = ajv.getSchema(
  `${proseSchemaId}#/$defs/ProseQualityReport`,
);
const novelCandidateValidator = ajv.getSchema(`${proseSchemaId}#/$defs/NovelCandidate`);
const compileManifestV1Validator = ajv.getSchema(`${proseSchemaId}#/$defs/CompileManifest`);
if (
  !profileV2Validator ||
  !proseChecklistValidator ||
  !sceneRenderCandidateValidator ||
  !sceneRenderValidator ||
  !proseJudgeReportValidator ||
  !proseConsensusReportValidator ||
  !proseQualityReportValidator ||
  !novelCandidateValidator ||
  !compileManifestV1Validator
) {
  throw new Error("N4.5 prose contract schemas were not registered");
}

const proseFixtureRoot = resolve(fixtureRoot, "compiler", "prose_rendering", "v1");
const profileV2 = loadJson(resolve(proseFixtureRoot, "profile_v2.json"));
assertValid(
  profileV2Validator,
  typedRoundTrip(profileV2 as unknown as NovelProfileV2),
  "NovelProfileV2",
);
for (const name of ["checklist_scene_1.json", "checklist_scene_2.json"]) {
  const value = loadJson(resolve(proseFixtureRoot, name));
  assertValid(
    proseChecklistValidator,
    typedRoundTrip(value as unknown as ProseJudgeChecklist),
    name,
  );
}
const proseCases: [string, ValidateFunction, unknown][] = [
  ["scene_render_candidate.json", sceneRenderCandidateValidator, {} as SceneRenderCandidate],
  ["scene_render_writer.json", sceneRenderValidator, {} as SceneRender],
  ["scene_render_rewrite_1.json", sceneRenderValidator, {} as SceneRender],
  ["scene_render_rewrite_2.json", sceneRenderValidator, {} as SceneRender],
  ["scene_render_polished.json", sceneRenderValidator, {} as SceneRender],
  ["scene_render_accepted.json", sceneRenderValidator, {} as SceneRender],
  ["judge_required_pass.json", proseJudgeReportValidator, {} as ProseJudgeReport],
  ["judge_forbidden_fail.json", proseJudgeReportValidator, {} as ProseJudgeReport],
  ["consensus_pass.json", proseConsensusReportValidator, {} as ProseConsensusReport],
  ["quality_findings.json", proseQualityReportValidator, {} as ProseQualityReport],
  ["novel_candidate.json", novelCandidateValidator, {} as NovelCandidate],
  ["compile_manifest.json", compileManifestV1Validator, {} as CompileManifest],
];
for (const [name, validator] of proseCases) {
  assertValid(validator, loadJson(resolve(proseFixtureRoot, name)), name);
}

const proseInvalidCases = loadJson(resolve(proseFixtureRoot, "invalid_cases.json"))
  .cases as JsonObject[];
for (const invalidCase of proseInvalidCases.filter(
  (value) => value.expected_layer === "schema",
)) {
  const baseName = invalidCase.base_fixture as string;
  const invalidValue = applyManifest(
    loadJson(resolve(proseFixtureRoot, baseName)),
    invalidCase,
  );
  const validator = baseName === "profile_v2.json"
    ? profileV2Validator
    : proseChecklistValidator;
  if (validator(invalidValue)) {
    throw new Error(`${String(invalidCase.name)} unexpectedly passed N4.5 schema`);
  }
}

console.log(
  `TypeScript contracts passed: ${casefilePaths.length} CaseFiles, Compiler foundation, N4.5 prose contracts, ValidationIssue, PatchCandidate, BriefIntake candidate/questions, and ${invalidManifests.length} invalid fixtures.`,
);
