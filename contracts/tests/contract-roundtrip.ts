import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import Ajv2020, { type ErrorObject, type ValidateFunction } from "ajv/dist/2020.js";
import addFormats from "ajv-formats";

import type {
  BriefIntakeCandidate,
  BriefIntakeQuestionSet,
  CaseFile,
  PatchCandidate,
  PublicAgentEvent,
  PublicAgentMessage,
  PublicPatchReviewResult,
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

if (
  !casefileValidator ||
  !issueValidator ||
  !patchValidator ||
  !briefIntakeCandidateValidator ||
  !briefIntakeQuestionSetValidator ||
  !publicMessageValidator ||
  !publicEventValidator ||
  !publicPatchReviewValidator
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

console.log(
  `TypeScript contracts passed: ${casefilePaths.length} CaseFiles, ValidationIssue, PatchCandidate, BriefIntake candidate/questions, and ${invalidManifests.length} invalid fixtures.`,
);
