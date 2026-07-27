import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import Ajv2020, { type ErrorObject, type ValidateFunction } from "ajv/dist/2020.js";
import addFormats from "ajv-formats";

import type {
  CaseFile,
  PatchCandidate,
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
  "https://casefile.local/schemas/v1/casefile/casefile.schema.json",
);
const issueValidator = ajv.getSchema(
  "https://casefile.local/schemas/v1/validation/validation-issue.schema.json",
);
const patchValidator = ajv.getSchema(
  "https://casefile.local/schemas/v1/casefile/patch-candidate.schema.json",
);

if (!casefileValidator || !issueValidator || !patchValidator) {
  throw new Error("Editing contract entry schemas were not registered");
}

const casefilePaths = readdirSync(resolve(fixtureRoot, "casefiles"))
  .filter((name) => name.endsWith(".casefile.json"))
  .sort();

if (casefilePaths.length !== 3) {
  throw new Error(`Expected 3 valid CaseFile fixtures, found ${casefilePaths.length}`);
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
  `TypeScript contracts passed: ${casefilePaths.length} CaseFiles, ValidationIssue, PatchCandidate, and ${invalidManifests.length} invalid fixtures.`,
);
