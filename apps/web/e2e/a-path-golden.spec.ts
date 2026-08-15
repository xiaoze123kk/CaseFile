import {
  expect,
  test,
  type APIResponse,
  type Locator,
  type Page,
  type Request,
  type TestInfo,
} from "@playwright/test";

interface DraftCandidateView {
  task_run_id: number;
  title: string;
  content_hash: string;
  object_counts: Record<string, number>;
  reasoning_questions: string[];
  constraint_statements: string[];
  is_current_brief: boolean;
  is_current: boolean;
  is_adopted: boolean;
  can_adopt: boolean;
  completed_at: string | null;
}

interface DraftView {
  project_id: number;
  draft_id: number;
  title: string;
  revision: number;
  status: string;
  content: null | {
    [field: string]: unknown;
    casefile_id: string;
    title: string;
    entities: Array<{ id: string; name: string }>;
    resolution_specs: Array<{ reasoning_question: string }>;
    constraints: Array<{ statement: string }>;
  };
}

interface DraftSummaryView {
  draft_id: number;
  title: string;
  revision: number;
  brief_version_no: number | null;
  has_content: boolean;
  is_current: boolean;
}

interface APathMetricsView {
  version: "a-path-funnel-v1";
  funnel: {
    task_runs: number;
    generated_candidates: number;
    adopted_candidates: number;
    post_adoption_edited_candidates: number;
    generation_success_rate: number;
    adoption_rate: number;
    post_adoption_edit_rate: number;
  };
  task_statuses: Record<string, number>;
  durable_events: Record<string, number>;
  post_adoption: {
    adoption_operations: number;
    edit_operations: number;
    edited_adoptions: number;
    operation_types: Record<string, number>;
  };
  usage_totals: Record<string, number>;
  usage_observations: {
    task_attempts: number;
    task_run_fallbacks: number;
  };
  completion_latency_ms: {
    observed_tasks: number;
    average: number | null;
    maximum: number | null;
  };
  unobservable_stages: Array<{ stage: string; reason: string }>;
}

const actorHeaders = { "X-CaseFile-User-Id": "1" };
const apiRoot =
  process.env.CASEFILE_E2E_API_URL ?? "http://127.0.0.1:18000/api/v1";

async function responseJson<T>(response: APIResponse): Promise<T> {
  if (!response.ok()) {
    throw new Error(
      `${response.url()} returned ${response.status()}: ${await response.text()}`,
    );
  }
  return (await response.json()) as T;
}

async function attachPageEvidence(
  testInfo: TestInfo,
  page: Page,
  name: string,
) {
  await testInfo.attach(name, {
    body: await page.screenshot({ fullPage: true }),
    contentType: "image/png",
  });
}

async function expectNarrowViewportItem(locator: Locator) {
  await locator.scrollIntoViewIfNeeded();
  await expect(locator).toBeVisible();
  const layout = await locator.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const style = window.getComputedStyle(element);
    return {
      bottom: rect.bottom,
      clientWidth: element.clientWidth,
      left: rect.left,
      right: rect.right,
      scrollWidth: element.scrollWidth,
      top: rect.top,
      viewportHeight: window.innerHeight,
      viewportWidth: window.innerWidth,
      writingMode: style.writingMode,
    };
  });
  expect(layout.writingMode).toBe("horizontal-tb");
  expect(layout.left).toBeGreaterThanOrEqual(0);
  expect(layout.top).toBeGreaterThanOrEqual(0);
  expect(layout.right).toBeLessThanOrEqual(layout.viewportWidth + 1);
  expect(layout.bottom).toBeLessThanOrEqual(layout.viewportHeight + 1);
  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.clientWidth + 1);
}

test("A 路径真实服务覆盖只读预览、窄屏、显式采用与指标", async ({ page, request }, testInfo) => {
  test.setTimeout(300_000);
  const configured = await request.put(`${apiRoot}/settings/provider`, {
    headers: actorHeaders,
    data: {
      provider: "openai",
      api_key: "casefile-e2e-fake-key",
      model_id: "gpt-5.6-sol",
      model_is_custom: false,
    },
  });
  expect(configured.ok(), await configured.text()).toBeTruthy();

  const serverFailures: string[] = [];
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  page.on("response", (response) => {
    if (response.status() >= 500) {
      serverFailures.push(`${response.status()} ${response.url()}`);
    }
  });
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: /把念头照亮/ }),
  ).toBeVisible();

  await page
    .getByLabel("写下最初想法")
    .fill("一名档案员发现三份可靠记录，都指向一段不存在的时间。");
  await attachPageEvidence(testInfo, page, "01-idea");
  await page.getByRole("button", { name: /继续关键追问/ }).click();

  await expect(
    page.getByRole("heading", { name: "只问会改变方向的问题。" }),
  ).toBeVisible();
  await expect
    .poll(() => new URL(page.url()).searchParams.get("project"))
    .not.toBeNull();
  const projectId = Number(new URL(page.url()).searchParams.get("project"));
  expect(projectId).toBeGreaterThan(0);
  const requiredQuestion = page.locator("article").filter({ hasText: "必须回答" });
  await requiredQuestion
    .getByLabel("你的回答")
    .fill("由 Agent 提出候选，但结论必须能由证据链验证。");
  await attachPageEvidence(testInfo, page, "02-questions");
  await page.getByRole("button", { name: /形成创作简报/ }).click();

  await expect(
    page.getByRole("heading", { name: "确认整体方向，再交给正式审阅。" }),
  ).toBeVisible();
  await page.getByRole("radio", { name: /唯一解/ }).check();
  await attachPageEvidence(testInfo, page, "03-brief-confirmation");
  await page.getByRole("button", { name: /进入创作简报审阅/ }).click();

  await expect(
    page.getByRole("heading", { name: "把生成依据逐条钉在纸面上。" }),
  ).toBeVisible();
  await page
    .getByRole("checkbox", { name: /我已逐条核对答案要点与创作规则/u })
    .check();
  await page.getByRole("button", { name: "保存审阅" }).click();
  const freezeButton = page.getByRole("button", { name: /确认并冻结/ });
  await expect(freezeButton).toBeEnabled();
  await attachPageEvidence(testInfo, page, "04-brief-review");
  await freezeButton.click();

  await expect(
    page.getByRole("heading", {
      name: "先选定创作策略，再生成一份完整深稿。",
    }),
  ).toBeVisible();
  await expect(page.getByText("三种方向已就绪，请由你选择。", { exact: true })).toBeVisible();
  await attachPageEvidence(testInfo, page, "05-strategy-options");

  const candidatesUrl = `${apiRoot}/projects/${projectId}/draft-candidates`;
  const draftUrl = `${apiRoot}/projects/${projectId}/draft`;
  const draftsUrl = `${apiRoot}/projects/${projectId}/drafts`;

  async function editCurrentEntity(name: string) {
    const editor = page.getByRole("region", { name: "对象详情与编辑" });
    await editor.getByRole("button", { name: "编辑" }).click();
    await editor.getByRole("textbox", { name: "名称" }).fill(name);
    await editor.getByRole("button", { name: "保存修改" }).click();
    await expect(editor.getByRole("status")).toContainText("修改已写入当前工作稿");
    await expect
      .poll(async () => {
        const draft = await responseJson<DraftView>(
          await request.get(draftUrl, { headers: actorHeaders }),
        );
        return draft.content?.entities[0]?.name ?? null;
      })
      .toBe(name);
  }

  const candidatesBeforeGeneration = await responseJson<DraftCandidateView[]>(
    await request.get(candidatesUrl, { headers: actorHeaders }),
  );
  const draftBeforeGeneration = await responseJson<DraftView>(
    await request.get(draftUrl, { headers: actorHeaders }),
  );
  expect(candidatesBeforeGeneration).toEqual([]);
  expect(draftBeforeGeneration.content).toBeNull();

  await page.getByRole("button", { name: /推理优先/ }).click();
  await page.getByRole("button", { name: /生成推理优先完整深稿/ }).click();

  let generatedCandidates: DraftCandidateView[] = [];
  await expect
    .poll(async () => {
      generatedCandidates = await responseJson<DraftCandidateView[]>(
        await request.get(candidatesUrl, { headers: actorHeaders }),
      );
      return generatedCandidates.length;
    })
    .toBe(1);

  const generatedCandidate = generatedCandidates[0];
  expect(generatedCandidate).toMatchObject({
    is_current_brief: true,
    is_current: false,
    is_adopted: false,
    can_adopt: true,
  });
  expect(generatedCandidate.completed_at).not.toBeNull();
  const draftAfterGeneration = await responseJson<DraftView>(
    await request.get(draftUrl, { headers: actorHeaders }),
  );
  expect(draftAfterGeneration.revision).toBe(draftBeforeGeneration.revision);
  expect(draftAfterGeneration.content).toBeNull();

  const candidateArchive = page.getByRole("region", {
    name: "当前简报完整深稿",
  });
  const candidateCard = candidateArchive
    .locator("article")
    .filter({ hasText: generatedCandidate.title });
  await expect(candidateCard).toContainText("待采用");
  await attachPageEvidence(testInfo, page, "06-candidate-pending-adoption");
  await testInfo.attach("candidate-not-auto-adopted", {
    body: Buffer.from(
      JSON.stringify(
        {
          before_generation: {
            candidates: candidatesBeforeGeneration.length,
            draft_revision: draftBeforeGeneration.revision,
            draft_has_content: draftBeforeGeneration.content !== null,
          },
          after_generation: {
            candidate: generatedCandidate,
            draft_revision: draftAfterGeneration.revision,
            draft_has_content: draftAfterGeneration.content !== null,
          },
        },
        null,
        2,
      ),
    ),
    contentType: "application/json",
  });

  const candidateCompletedAt = candidateCard.getByTestId(
    `candidate-completed-at-${generatedCandidate.task_run_id}`,
  );
  const previewButton = candidateCard.getByRole("button", {
    name: "预览工作台",
  });
  const adoptionButton = candidateCard.getByRole("button", {
    name: /采用为当前工作稿/,
  });
  // 新生成的候选默认展开，预览与采用动作直接可见。
  await expect(previewButton).toBeVisible();
  await expect(adoptionButton).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  const expectedCompletedAt = new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(generatedCandidate.completed_at!));
  await expect(candidateCompletedAt).toContainText(
    `完成于 ${expectedCompletedAt}`,
  );
  await expectNarrowViewportItem(candidateCompletedAt);
  await expectNarrowViewportItem(adoptionButton);
  await attachPageEvidence(testInfo, page, "07-narrow-candidate-actions");
  await page.setViewportSize({ width: 1440, height: 960 });

  const previewEndpoint = `${apiRoot}/projects/${projectId}/draft-candidates/${generatedCandidate.task_run_id}`;
  const previewEndpointPath = new URL(previewEndpoint).pathname;
  const previewApiRequests: string[] = [];
  const recordPreviewRequest = (previewRequest: Request) => {
    const url = new URL(previewRequest.url());
    if (url.origin === new URL(apiRoot).origin && url.pathname.includes(`/projects/${projectId}/`)) {
      previewApiRequests.push(`${previewRequest.method()} ${url.pathname}`);
    }
  };
  page.on("request", recordPreviewRequest);
  await previewButton.click();
  await expect(page).toHaveURL(
    new RegExp(
      `/workbench\\?project=${projectId}&preview=${generatedCandidate.task_run_id}$`,
    ),
  );
  const previewBanner = page.getByRole("status", {
    name: "候选预览只读提示",
  });
  await expect(previewBanner).toContainText("候选预览，不是 Current Draft");
  await expect(previewBanner).toContainText(
    "预览不会采用候选，也不会读取或修改当前工作稿。",
  );
  await expect(previewBanner).toContainText(
    "编辑、重置、重新验证、Agent、补丁、编译与导出均已锁定",
  );
  page.off("request", recordPreviewRequest);

  await expect
    .poll(() =>
      previewApiRequests.includes(`GET ${previewEndpointPath}`),
    )
    .toBe(true);
  expect(
    previewApiRequests.filter((entry) => !entry.startsWith("GET ")),
  ).toEqual([]);
  expect(
    previewApiRequests.some(
      (entry) =>
        entry.endsWith(`/projects/${projectId}/draft`) ||
        entry.endsWith(`/projects/${projectId}/workbench-context`),
    ),
  ).toBe(false);

  const previewRoot = page.locator('[data-read-only-preview="true"]');
  await expect(previewRoot).toBeVisible();
  await expect(
    page.getByRole("button", { name: "候选预览不可重置" }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "候选预览不可使用 Agent" }),
  ).toBeDisabled();
  await expect(
    page.locator('[aria-label="卷宗状态"] button').filter({ hasText: "导出" }),
  ).toBeDisabled();
  await expect(page.getByRole("tab", { name: /导出预览/ })).toBeDisabled();
  await expect(page.getByRole("tab", { name: /编译中心/ })).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "采用后才能编辑" }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "重新验证" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: /采用为当前工作稿/ }),
  ).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "补丁审阅" })).toHaveCount(0);
  await expect(
    page.getByText("候选预览不读取当前工作稿来源", { exact: true }),
  ).toBeVisible();
  await attachPageEvidence(testInfo, page, "08-read-only-candidate-preview");

  const draftAfterPreview = await responseJson<DraftView>(
    await request.get(draftUrl, { headers: actorHeaders }),
  );
  expect(draftAfterPreview).toEqual(draftAfterGeneration);
  const candidatesAfterPreview = await responseJson<DraftCandidateView[]>(
    await request.get(candidatesUrl, { headers: actorHeaders }),
  );
  expect(candidatesAfterPreview[0]).toMatchObject({
    task_run_id: generatedCandidate.task_run_id,
    is_current: false,
    is_adopted: false,
    can_adopt: true,
  });

  await page.goBack();
  await expect(
    page.getByRole("heading", {
      name: "先选定创作策略，再生成一份完整深稿。",
    }),
  ).toBeVisible();
  const restoredCandidateCard = page
    .getByRole("region", { name: "当前简报完整深稿" })
    .locator("article")
    .filter({ hasText: generatedCandidate.title });
  if (
    (await restoredCandidateCard
      .getByRole("button", { name: /采用为当前工作稿/ })
      .count()) === 0
  ) {
    await restoredCandidateCard.getByRole("button").first().click();
  }
  await expect(
    restoredCandidateCard.getByRole("button", { name: /采用为当前工作稿/ }),
  ).toBeVisible();
  await restoredCandidateCard
    .getByRole("button", { name: /采用为当前工作稿/ })
    .click();

  await expect(page).toHaveURL(
    new RegExp(`/workbench\\?project=${projectId}$`),
  );

  let adoptedCandidates: DraftCandidateView[] = [];
  await expect
    .poll(async () => {
      adoptedCandidates = await responseJson<DraftCandidateView[]>(
        await request.get(candidatesUrl, { headers: actorHeaders }),
      );
      return adoptedCandidates[0]?.is_adopted ?? false;
    })
    .toBe(true);
  expect(adoptedCandidates[0]).toMatchObject({
    task_run_id: generatedCandidate.task_run_id,
    content_hash: generatedCandidate.content_hash,
    is_current: true,
    is_adopted: true,
    can_adopt: false,
  });

  let currentDraft: DraftView | null = null;
  await expect
    .poll(async () => {
      currentDraft = await responseJson<DraftView>(
        await request.get(draftUrl, { headers: actorHeaders }),
      );
      return currentDraft.content?.title ?? null;
    })
    .toBe(generatedCandidate.title);
  expect(currentDraft).not.toBeNull();
  expect(currentDraft!.revision).toBeGreaterThan(draftBeforeGeneration.revision);
  expect(currentDraft!.content?.casefile_id).toMatch(/^case_/u);
  expect(
    currentDraft!.content?.resolution_specs.map((item) => item.reasoning_question),
  ).toEqual(generatedCandidate.reasoning_questions);
  expect(
    currentDraft!.content?.constraints.map((item) => item.statement),
  ).toEqual(generatedCandidate.constraint_statements);
  for (const [collection, count] of Object.entries(
    generatedCandidate.object_counts,
  )) {
    const objects = currentDraft!.content?.[collection];
    expect(Array.isArray(objects), `${collection} should be a CaseFile collection`).toBe(
      true,
    );
    expect(objects).toHaveLength(count);
  }

  await expect(page.getByText(generatedCandidate.title, { exact: true }).first()).toBeVisible();
  await expect(
    page.getByRole("button", {
      name: `服务端修订 R${currentDraft!.revision}`,
    }),
  ).toBeVisible();
  await expect(page.getByText("已与服务端同步", { exact: true })).toBeVisible();
  await attachPageEvidence(testInfo, page, "09-real-workbench");

  const draftAId = currentDraft!.draft_id;
  const draftAEntityName = `工作稿 A 独立人物 ${projectId}`;
  await editCurrentEntity(draftAEntityName);
  const draftAAfterEdit = await responseJson<DraftView>(
    await request.get(draftUrl, { headers: actorHeaders }),
  );
  expect(draftAAfterEdit.draft_id).toBe(draftAId);

  await page.locator('button[data-kind="draft"]').click();
  await page.getByRole("menuitem", { name: /生成新工作稿/u }).click();
  await expect(page).toHaveURL(new RegExp(`/\\?project=${projectId}$`));
  await expect(
    page.getByRole("heading", {
      name: "先选定创作策略，再生成一份完整深稿。",
    }),
  ).toBeVisible();

  const strategyFan = page.getByLabel("三种策略并列比较");
  await strategyFan.getByRole("button", { name: /氛围优先/u }).click();
  await page.getByRole("button", { name: /生成氛围优先完整深稿/u }).click();

  let candidatesAfterSecondGeneration: DraftCandidateView[] = [];
  await expect
    .poll(async () => {
      candidatesAfterSecondGeneration = await responseJson<DraftCandidateView[]>(
        await request.get(candidatesUrl, { headers: actorHeaders }),
      );
      return candidatesAfterSecondGeneration.length;
    })
    .toBe(2);
  const generatedCandidateB = candidatesAfterSecondGeneration.find(
    (candidate) => candidate.task_run_id !== generatedCandidate.task_run_id,
  );
  expect(generatedCandidateB).toBeDefined();
  expect(generatedCandidateB).toMatchObject({
    is_adopted: false,
    is_current: false,
    can_adopt: true,
  });

  const candidateBCard = page
    .getByRole("region", { name: "当前简报完整深稿" })
    .locator("article")
    .filter({ hasText: "氛围优先" });
  if (
    (await candidateBCard
      .getByRole("button", { name: /采用为当前工作稿/u })
      .count()) === 0
  ) {
    await candidateBCard.getByRole("button").first().click();
  }
  await expect(
    candidateBCard.getByTestId(
      `candidate-completed-at-${generatedCandidateB!.task_run_id}`,
    ),
  ).toBeVisible();
  await candidateBCard
    .getByRole("button", { name: /采用为当前工作稿/u })
    .click();
  await expect(page).toHaveURL(
    new RegExp(`/workbench\\?project=${projectId}$`),
  );

  let draftB: DraftView | null = null;
  await expect
    .poll(async () => {
      draftB = await responseJson<DraftView>(
        await request.get(draftUrl, { headers: actorHeaders }),
      );
      return draftB.draft_id;
    })
    .not.toBe(draftAId);
  const draftBId = draftB!.draft_id;
  const draftBEntityName = `工作稿 B 独立人物 ${projectId}`;
  await editCurrentEntity(draftBEntityName);

  const materializedDrafts = await responseJson<DraftSummaryView[]>(
    await request.get(draftsUrl, { headers: actorHeaders }),
  );
  expect(materializedDrafts.filter((draft) => draft.has_content)).toHaveLength(2);
  expect(materializedDrafts.filter((draft) => draft.is_current)).toEqual([
    expect.objectContaining({ draft_id: draftBId }),
  ]);

  await page.locator('button[data-kind="draft"]').click();
  await page
    .getByRole("menuitem", { name: new RegExp(`工作稿 #${draftAId}`) })
    .click();
  await expect
    .poll(async () =>
      responseJson<DraftView>(
        await request.get(draftUrl, { headers: actorHeaders }),
      ).then((draft) => draft.draft_id),
    )
    .toBe(draftAId);
  await expect(
    page
      .getByRole("region", { name: "对象详情与编辑" })
      .getByRole("heading", { name: draftAEntityName }),
  ).toBeVisible();

  await page.locator('button[data-kind="draft"]').click();
  await page
    .getByRole("menuitem", { name: new RegExp(`工作稿 #${draftBId}`) })
    .click();
  await expect
    .poll(async () =>
      responseJson<DraftView>(
        await request.get(draftUrl, { headers: actorHeaders }),
      ).then((draft) => draft.draft_id),
    )
    .toBe(draftBId);
  await expect(
    page
      .getByRole("region", { name: "对象详情与编辑" })
      .getByRole("heading", { name: draftBEntityName }),
  ).toBeVisible();
  await attachPageEvidence(testInfo, page, "10-two-drafts-isolated");

  const metricsResponse = await request.get(
    `${apiRoot}/projects/${projectId}/a-path-metrics`,
    { headers: actorHeaders },
  );
  const metrics = await responseJson<APathMetricsView>(metricsResponse);
  expect(metrics).toMatchObject({
    version: "a-path-funnel-v1",
    funnel: {
      task_runs: 2,
      generated_candidates: 2,
      adopted_candidates: 2,
      post_adoption_edited_candidates: 2,
      generation_success_rate: 1,
      adoption_rate: 1,
      post_adoption_edit_rate: 1,
    },
    task_statuses: { succeeded: 2 },
    durable_events: { "candidate.adopted": 2 },
    post_adoption: {
      adoption_operations: 2,
      edit_operations: 2,
      edited_adoptions: 2,
      operation_types: { replace: 2 },
    },
  });
  expect(metrics.usage_observations.task_attempts).toBeGreaterThanOrEqual(2);
  expect(metrics.usage_observations.task_run_fallbacks).toBe(0);
  expect(metrics.completion_latency_ms.observed_tasks).toBe(2);
  expect(metrics.unobservable_stages).toContainEqual(
    expect.objectContaining({ stage: "candidate_previewed" }),
  );
  await testInfo.attach("a-path-metrics", {
    body: Buffer.from(JSON.stringify(metrics, null, 2)),
    contentType: "application/json",
  });

  await testInfo.attach("explicit-adoption-result", {
    body: Buffer.from(
      JSON.stringify(
        {
          adopted_candidate: adoptedCandidates[0],
          current_draft: {
            project_id: currentDraft!.project_id,
            revision: currentDraft!.revision,
            casefile_id: currentDraft!.content?.casefile_id,
            title: currentDraft!.content?.title,
          },
        },
        null,
        2,
      ),
    ),
    contentType: "application/json",
  });
  await testInfo.attach("browser-runtime-errors", {
    body: Buffer.from(
      JSON.stringify({ consoleErrors, pageErrors, serverFailures }, null, 2),
    ),
    contentType: "application/json",
  });
  expect(consoleErrors).toEqual([]);
  expect(pageErrors).toEqual([]);
  expect(serverFailures).toEqual([]);
});
