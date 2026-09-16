import {
  expect,
  test,
  type Download,
  type Locator,
  type Page,
  type Request,
} from "@playwright/test";

/**
 * Browser-level validation for the standalone single-screen Home flow
 * (M6-05, then updated for S0004,
 * `specs/S0004-responsive-single-screen-home-design-convergence/spec.md`).
 *
 * The test runner is intentionally supplied by the controlled validation
 * environment: this specification does not authorize package-manifest or
 * lockfile changes. These tests never intercept, fulfill, or mock a
 * request. Every assertion is made against the running WebUI and its real
 * REST API v1 backend.
 */

const analysisTimeoutMs = Number.parseInt(
  process.env.E2E_ANALYSIS_TIMEOUT_MS ?? "180000",
  10,
);

interface FixturePaths {
  sourceMedia: string;
  matchingCue: string;
  secondMatchingCue: string;
  noMatchCue: string;
}

/**
 * S0002 (`specs/S0002-common-video-container-source-media-support/
 * spec.md`): a small WebM source containing an audio stream, supplied by
 * the same operator/environment-fixture convention as `fixturePaths()`
 * above (no binary fixture is committed to the repository for this).
 */
function webmSourceMediaPath(): string {
  return requiredEnvironmentValue("E2E_WEBM_SOURCE_MEDIA_PATH");
}

interface ApiCall {
  method: string;
  origin: string;
  pathname: string;
}

interface ResultEnvelope {
  api_version: string;
  result_schema_version: string;
  result: {
    analysis_id: string;
    configuration: {
      matching: {
        method: string;
        acceptance_threshold: number;
      };
      configuration_source_name: string;
    };
    cues: Array<{
      cue_id: string;
      outcome: {
        kind: "occurrences" | "no_match" | "failure";
        occurrences?: Array<{
          temporal_position: number;
          score: number;
          matching_method: string;
        }>;
      };
    }>;
  };
}

interface ErrorEnvelope {
  error_code: string;
  message: string;
  correlation_id: string;
}

interface AnalysisCueRequestBody {
  cue_id: string;
  asset_id: string;
  label?: string | null;
  trim_start_seconds?: number | null;
  trim_end_seconds?: number | null;
}

const documentedOperations = [
  { method: "POST", pathname: /^\/api\/v1\/assets\/source-media$/ },
  { method: "POST", pathname: /^\/api\/v1\/assets\/cue$/ },
  { method: "POST", pathname: /^\/api\/v1\/analyses$/ },
  { method: "GET", pathname: /^\/api\/v1\/analyses\/[^/]+$/ },
  {
    method: "GET",
    pathname: /^\/api\/v1\/analyses\/[^/]+\/result$/,
  },
  {
    method: "GET",
    pathname: /^\/api\/v1\/analyses\/[^/]+\/cues\/[^/]+\/audio$/,
  },
  {
    method: "GET",
    pathname:
      /^\/api\/v1\/analyses\/[^/]+\/cues\/[^/]+\/occurrences\/\d+\/audio$/,
  },
] as const;

function requiredEnvironmentValue(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new Error(`${name} must be set by the real-backend test environment`);
  }
  return value;
}

function fixturePaths(): FixturePaths {
  return {
    sourceMedia: requiredEnvironmentValue("E2E_SOURCE_MEDIA_PATH"),
    matchingCue: requiredEnvironmentValue("E2E_MATCHING_CUE_PATH"),
    secondMatchingCue: requiredEnvironmentValue(
      "E2E_SECOND_MATCHING_CUE_PATH",
    ),
    noMatchCue: requiredEnvironmentValue("E2E_NO_MATCH_CUE_PATH"),
  };
}

function repeatedOccurrenceFixturePaths(): {
  sourceMedia: string;
  cue: string;
} | null {
  const sourceMedia = process.env.E2E_REPEATED_SOURCE_MEDIA_PATH?.trim();
  const cue = process.env.E2E_REPEATED_CUE_PATH?.trim();
  return sourceMedia && cue ? { sourceMedia, cue } : null;
}

function webuiBaseUrl(): string {
  return requiredEnvironmentValue("WEBUI_BASE_URL").replace(/\/$/, "");
}

function isBrowserApiOperation(request: Request): boolean {
  return request.resourceType() === "fetch" || request.resourceType() === "xhr";
}

function beginPublicApiAudit(page: Page): ApiCall[] {
  const calls: ApiCall[] = [];
  page.on("request", (request) => {
    if (!isBrowserApiOperation(request)) {
      return;
    }
    const url = new URL(request.url());
    calls.push({
      method: request.method(),
      origin: url.origin,
      pathname: url.pathname,
    });
  });
  return calls;
}

function assertOnlyDocumentedPublicApiCalls(
  calls: ApiCall[],
  baseUrl: string,
): void {
  expect(calls.length).toBeGreaterThan(0);
  const expectedOrigin = new URL(baseUrl).origin;

  for (const call of calls) {
    expect(call.origin).toBe(expectedOrigin);
    expect(call.pathname.startsWith("/api/v1/")).toBe(true);
    expect(
      documentedOperations.some(
        (operation) =>
          operation.method === call.method &&
          operation.pathname.test(call.pathname),
      ),
    ).toBe(true);
  }
}

/**
 * Builds a deterministic, valid PCM WAV buffer at least `minTotalBytes`
 * long (S0001). Content is silence; only the RIFF/`WAVE` container needs
 * to be well-formed, since the goal is to cross the *old* Nginx default
 * `client_max_body_size` (1m) while staying far below ACL's configured
 * 500 MiB source-media limit -- not to exercise media-processing content.
 */
function buildDeterministicWavBuffer(minTotalBytes: number): Buffer {
  const headerBytes = 44;
  const numChannels = 1;
  const sampleRate = 44100;
  const bitsPerSample = 16;
  const blockAlign = (numChannels * bitsPerSample) / 8;
  const byteRate = sampleRate * blockAlign;
  const rawDataBytes = Math.max(blockAlign, minTotalBytes - headerBytes);
  const dataBytes = rawDataBytes + (rawDataBytes % blockAlign);

  const header = Buffer.alloc(headerBytes);
  header.write("RIFF", 0, "ascii");
  header.writeUInt32LE(36 + dataBytes, 4);
  header.write("WAVE", 8, "ascii");
  header.write("fmt ", 12, "ascii");
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
  header.writeUInt16LE(numChannels, 22);
  header.writeUInt32LE(sampleRate, 24);
  header.writeUInt32LE(byteRate, 28);
  header.writeUInt16LE(blockAlign, 32);
  header.writeUInt16LE(bitsPerSample, 34);
  header.write("data", 36, "ascii");
  header.writeUInt32LE(dataBytes, 40);

  return Buffer.concat([header, Buffer.alloc(dataBytes)]);
}

function countCalls(
  calls: ApiCall[],
  method: string,
  pathname: RegExp,
): number {
  return calls.filter(
    (call) => call.method === method && pathname.test(call.pathname),
  ).length;
}

function sourceMediaInput(page: Page) {
  // The Source media file input and its enclosing `<section
  // aria-labelledby="source-title">` both resolve to the accessible name
  // "Source media" -- legitimate for assistive tech (a landmark region and
  // its labelled control may share a name), but ambiguous for a bare
  // `getByLabel` locator. Intersecting with the input role disambiguates.
  return page.getByLabel("Source media", { exact: true }).and(page.locator("input"));
}

async function openHomePage(page: Page, baseUrl: string): Promise<void> {
  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await expect(
    page.getByRole("heading", { name: "Locate audio cues inside your media files" }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "Source media" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Cues", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Results" })).toHaveCount(0);
}

interface CueInput {
  path: string;
  name?: string;
  startText?: string;
  endText?: string;
}

async function fillCueRow(
  page: Page,
  index: number,
  cue: CueInput,
): Promise<void> {
  const row = page.locator(".cue-row").nth(index);
  await page.getByLabel(`Cue ${index + 1}`, { exact: true }).setInputFiles(cue.path);
  if (cue.name !== undefined) {
    await row.getByPlaceholder("e.g. Notification sound").fill(cue.name);
  }
  if (cue.startText !== undefined) {
    await row.getByPlaceholder("e.g. 00:00:01").fill(cue.startText);
  }
  if (cue.endText !== undefined) {
    await row.getByPlaceholder("e.g. 00:00:03").fill(cue.endText);
  }
}

/**
 * Fills the form (without submitting) so validation-focused tests can
 * inspect button/field state before deciding whether to submit.
 */
async function fillAnalysisForm(
  page: Page,
  baseUrl: string,
  sourceMediaPath: string | Parameters<Page["setInputFiles"]>[1],
  cues: CueInput[],
): Promise<void> {
  await openHomePage(page, baseUrl);
  const sourceInput = sourceMediaInput(page);
  await sourceInput.setInputFiles(sourceMediaPath as never);

  for (let index = 0; index < cues.length; index += 1) {
    if (index > 0) {
      await page.getByRole("button", { name: "Add another cue" }).click();
    }
    await fillCueRow(page, index, cues[index]);
  }
}

interface SubmittedAnalysis {
  analysisId: string;
  requestCues: AnalysisCueRequestBody[];
  minimumSimilarityScore?: number;
}

async function submitAnalysis(
  page: Page,
  baseUrl: string,
  sourceMediaPath: string | Parameters<Page["setInputFiles"]>[1],
  cues: CueInput[],
): Promise<SubmittedAnalysis> {
  await fillAnalysisForm(page, baseUrl, sourceMediaPath, cues);

  const createButton = page.getByRole("button", { name: "Create Analysis" });
  await expect(createButton).toBeEnabled();

  const sourceUploadRequestPromise = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      new URL(request.url()).pathname === "/api/v1/assets/source-media",
  );
  const analysesRequestPromise = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      new URL(request.url()).pathname === "/api/v1/analyses",
  );
  const analysesResponsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/v1/analyses",
    { timeout: analysisTimeoutMs },
  );
  const uploadingButtonPromise = page
    .getByRole("button", { name: "Uploading…" })
    .waitFor({ state: "visible" });
  const uploadingActivityPromise = page
    .locator('.cues-panel [data-activity-phase="uploading"]')
    .waitFor({ state: "visible" });
  const locatingButtonPromise = page
    .getByRole("button", { name: "Locating…" })
    .waitFor({ state: "visible", timeout: analysisTimeoutMs });
  const preIdLocatingActivityPromise = page
    .locator('.cues-panel [data-activity-phase="locating"]')
    .waitFor({ state: "visible", timeout: analysisTimeoutMs });
  const postIdLocatingActivityPromise = page
    .locator('.results-panel [data-activity-phase="locating"]')
    .waitFor({ state: "visible", timeout: analysisTimeoutMs });
  await createButton.click();

  await sourceUploadRequestPromise;
  await Promise.all([uploadingButtonPromise, uploadingActivityPromise]);
  const analysesRequest = await analysesRequestPromise;
  await Promise.all([locatingButtonPromise, preIdLocatingActivityPromise]);
  await expect(page.getByText("Creating…", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Analyzing…", { exact: true })).toHaveCount(0);
  const requestBody = analysesRequest.postDataJSON() as {
    cues: AnalysisCueRequestBody[];
    minimum_similarity_score?: number;
  };

  const analysesResponse = await analysesResponsePromise;
  // S0005 section 4.9: the real Analysis-creation HTTP status must be
  // checked before the response body is ever treated as an Analysis --
  // a `400 validation_error` must fail here, at this boundary, with the
  // real response observable, instead of continuing with an undefined/
  // nonexistent `analysis_id`.
  expect(analysesResponse.status()).toBe(202);
  const analysis = (await analysesResponse.json()) as { analysis_id: string };

  await expect(page.getByRole("heading", { name: "Results" })).toBeVisible({
    timeout: analysisTimeoutMs,
  });
  await postIdLocatingActivityPromise;
  await expect(page.getByText(/^Status: /)).toHaveCount(0);

  return {
    analysisId: analysis.analysis_id,
    requestCues: requestBody.cues,
    minimumSimilarityScore: requestBody.minimum_similarity_score,
  };
}

interface RejectedAnalysisSubmission {
  status: number;
  requestCues: AnalysisCueRequestBody[];
  error: ErrorEnvelope;
}

/**
 * Mirrors `submitAnalysis` up through the real `/api/v1/analyses` response,
 * but for a scenario that is expected to be rejected (S0005 section 4.11):
 * it asserts the non-202 status directly, returns the parsed public Error
 * envelope, and never adopts an Analysis ID or waits for a Results heading
 * -- there is no Analysis to poll.
 */
async function submitAnalysisExpectingRejection(
  page: Page,
  baseUrl: string,
  sourceMediaPath: string | Parameters<Page["setInputFiles"]>[1],
  cues: CueInput[],
): Promise<RejectedAnalysisSubmission> {
  await fillAnalysisForm(page, baseUrl, sourceMediaPath, cues);

  const createButton = page.getByRole("button", { name: "Create Analysis" });
  await expect(createButton).toBeEnabled();

  const analysesRequestPromise = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      new URL(request.url()).pathname === "/api/v1/analyses",
  );
  const analysesResponsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/v1/analyses",
    { timeout: analysisTimeoutMs },
  );
  await createButton.click();

  const analysesRequest = await analysesRequestPromise;
  const requestBody = analysesRequest.postDataJSON() as {
    cues: AnalysisCueRequestBody[];
  };

  const analysesResponse = await analysesResponsePromise;
  const status = analysesResponse.status();
  expect(status).not.toBe(202);
  const error = (await analysesResponse.json()) as ErrorEnvelope;

  return { status, requestCues: requestBody.cues, error };
}

function captureSuccessfulResultBodies(
  page: Page,
  analysisId: string,
): Array<Promise<Buffer>> {
  const bodies: Array<Promise<Buffer>> = [];
  const expectedPath = `/api/v1/analyses/${encodeURIComponent(
    analysisId,
  )}/result`;

  page.on("response", (response) => {
    const url = new URL(response.url());
    if (
      response.request().method() === "GET" &&
      url.pathname === expectedPath &&
      response.ok()
    ) {
      bodies.push(response.body());
    }
  });
  return bodies;
}

async function readDownload(download: Download): Promise<Buffer> {
  const stream = await download.createReadStream();
  if (!stream) {
    throw new Error("The browser did not expose the downloaded bytes");
  }

  const chunks: Buffer[] = [];
  for await (const chunk of stream) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  return Buffer.concat(chunks);
}

async function resolvedResultBodies(
  bodies: Array<Promise<Buffer>>,
): Promise<Buffer[]> {
  await expect.poll(() => bodies.length, { timeout: analysisTimeoutMs }).toBeGreaterThan(0);
  return Promise.all(bodies);
}

function deriveSimilarityRanks(
  occurrences: Array<{ temporal_position: number; score: number }>,
): number[] {
  const rankedIndices = occurrences
    .map((_occurrence, index) => index)
    .sort((leftIndex, rightIndex) => {
      const left = occurrences[leftIndex];
      const right = occurrences[rightIndex];
      return (
        right.score - left.score ||
        left.temporal_position - right.temporal_position ||
        leftIndex - rightIndex
      );
    });
  const ranks = new Array<number>(occurrences.length);
  rankedIndices.forEach((canonicalIndex, rankIndex) => {
    ranks[canonicalIndex] = rankIndex + 1;
  });
  return ranks;
}

function formatExpectedClock(seconds: number): string {
  const wholeSeconds = Math.floor(seconds);
  const hours = Math.floor(wholeSeconds / 3600);
  const minutes = Math.floor((wholeSeconds % 3600) / 60);
  const secondsPart = wholeSeconds % 60;
  if (wholeSeconds < 3600) {
    return `${String(Math.floor(wholeSeconds / 60)).padStart(2, "0")}:${String(
      secondsPart,
    ).padStart(2, "0")}`;
  }
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(
    2,
    "0",
  )}:${String(secondsPart).padStart(2, "0")}`;
}

const removedSettingsCopy = [
  "Using the server recommended default",
  "Custom value. Recommended default",
  "Lower values can return more and less-similar matches",
  "Higher values are stricter",
  "The selected value is captured for each new Analysis",
] as const;

async function assertRemovedSettingsCopyAbsent(dialog: Locator): Promise<void> {
  const renderedContent = await dialog.evaluate((root) => ({
    text: root.textContent ?? "",
    attributeValues: Array.from(root.querySelectorAll("*")).flatMap((element) =>
      Array.from(element.attributes, (attribute) => attribute.value),
    ),
  }));
  const attributeText = renderedContent.attributeValues.join("\n");

  for (const copy of removedSettingsCopy) {
    expect(renderedContent.text).not.toContain(copy);
    expect(attributeText).not.toContain(copy);
  }
  await expect(dialog.locator("#minimum-similarity-mode")).toHaveCount(0);
  await expect(dialog.locator("#minimum-similarity-guidance")).toHaveCount(0);
  expect(
    await dialog
      .getByRole("slider", { name: "Minimum similarity score" })
      .getAttribute("aria-describedby"),
  ).toBeNull();
}

async function assertDisclosureRightEdge(
  page: Page,
  group: Locator,
  disclosure: Locator,
  viewport: { width: number; height: number },
): Promise<number> {
  const epsilon = 0.5;
  await page.setViewportSize(viewport);
  await disclosure.scrollIntoViewIfNeeded();

  const groupBox = await group.boundingBox();
  const disclosureBox = await disclosure.boundingBox();
  if (!groupBox || !disclosureBox) {
    throw new Error("Cue group disclosure geometry is not measurable");
  }
  const metrics = await group.evaluate((element) => {
    const style = window.getComputedStyle(element);
    return {
      borderLeft: Number.parseFloat(style.borderLeftWidth),
      borderRight: Number.parseFloat(style.borderRightWidth),
    };
  });
  const disclosureRadius = await disclosure.evaluate((element) =>
    Number.parseFloat(window.getComputedStyle(element).borderRadius),
  );
  const groupInnerLeft = groupBox.x + metrics.borderLeft;
  const groupInnerRight = groupBox.x + groupBox.width - metrics.borderRight;
  const disclosureRight = disclosureBox.x + disclosureBox.width;
  const rightInset = groupInnerRight - disclosureRight;

  expect(disclosureBox.x).toBeGreaterThanOrEqual(groupInnerLeft - epsilon);
  expect(disclosureRight).toBeLessThanOrEqual(groupInnerRight + epsilon);
  expect(rightInset).toBeGreaterThanOrEqual(-epsilon);
  expect(rightInset).toBeLessThanOrEqual(4 + epsilon);
  expect(Math.abs(disclosureBox.width - 44)).toBeLessThanOrEqual(epsilon);
  expect(Math.abs(disclosureBox.height - 44)).toBeLessThanOrEqual(epsilon);
  expect(disclosureRadius).toBeGreaterThanOrEqual(22 - epsilon);

  const overflow = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth);
  return rightInset;
}

test.describe.serial("standalone WebUI against the real REST API", () => {
  test("renders the single-screen Home shell with Source before Cues and no Results initially", async ({
    page,
  }) => {
    const baseUrl = webuiBaseUrl();
    await openHomePage(page, baseUrl);

    await expect(page.getByAltText("Audio Cue Locator")).toBeVisible();

    const headingTexts = await page.getByRole("heading", { level: 2 }).allTextContents();
    const sourceIndex = headingTexts.indexOf("Source media");
    const cuesIndex = headingTexts.indexOf("Cues");
    expect(sourceIndex).toBeGreaterThanOrEqual(0);
    expect(cuesIndex).toBeGreaterThan(sourceIndex);

    await expect(sourceMediaInput(page)).toBeAttached();
    await expect(page.getByLabel("Cue 1", { exact: true })).toBeAttached();
    await expect(page.getByPlaceholder("e.g. Notification sound")).toBeVisible();
    await expect(page.getByPlaceholder("e.g. 00:00:01")).toBeVisible();
    await expect(page.getByPlaceholder("e.g. 00:00:03")).toBeVisible();
    await expect(
      page.getByText(
        "Start and End are positions inside this cue file, not the source media.",
        { exact: true },
      ),
    ).toHaveCount(0);

    await page.getByRole("button", { name: "Add another cue" }).click();
    await expect(page.getByLabel("Cue 2", { exact: true })).toBeAttached();
  });

  test("submits multiple cues with S0003 fields, renders their occurrences, and downloads the exact Result JSON", async ({
    page,
  }) => {
    test.setTimeout(analysisTimeoutMs + 30_000);
    const baseUrl = webuiBaseUrl();
    const fixtures = fixturePaths();
    const calls = beginPublicApiAudit(page);

    const { analysisId, requestCues } = await submitAnalysis(page, baseUrl, fixtures.sourceMedia, [
      { path: fixtures.matchingCue, name: "First cue", startText: "00:00:01" },
      { path: fixtures.secondMatchingCue, name: "Second cue" },
    ]);

    // Compatibility-preserved fields carry a per-Cue source window.
    expect(requestCues[0].label).toBe("First cue");
    expect(requestCues[0].trim_start_seconds).toBe(1);
    expect(requestCues[0].trim_end_seconds ?? null).toBeNull();
    expect(requestCues[1].label).toBe("Second cue");

    const resultBodies = captureSuccessfulResultBodies(page, analysisId);

    await expect(page.getByRole("heading", { name: "First cue" })).toBeVisible({
      timeout: analysisTimeoutMs,
    });
    await expect(page.getByRole("heading", { name: "Second cue" })).toBeVisible();
    await expect(page.locator(".cue-result-group")).toHaveCount(2);
    const firstGroup = page.locator(".cue-result-group").nth(0);
    const secondGroup = page.locator(".cue-result-group").nth(1);
    expect(
      await page.locator(".occurrence-position").count(),
    ).toBeGreaterThanOrEqual(2);

    const downloadButton = page.getByRole("button", { name: "Download result JSON" });
    await expect(downloadButton).toBeVisible({ timeout: analysisTimeoutMs });
    await expect(page.locator("[data-activity-phase]")).toHaveCount(0);
    await expect(page.getByText("Creating…", { exact: true })).toHaveCount(0);
    await expect(page.getByText("Analyzing…", { exact: true })).toHaveCount(0);

    // Scores are never presented as confidence or a percentage.
    const resultsText = (await page.locator(".results-panel").innerText()).toLowerCase();
    expect(resultsText).not.toMatch(/confidence/);
    expect(page.getByText(/^Status: /)).toHaveCount(0);

    const responseBodies = await resolvedResultBodies(resultBodies);
    const envelope = JSON.parse(
      responseBodies[responseBodies.length - 1].toString("utf8"),
    ) as ResultEnvelope;
    expect(envelope.api_version).toBe("v1");
    expect(envelope.result.analysis_id).toBe(analysisId);
    expect(envelope.result.cues.map((cue) => cue.cue_id)).toEqual([
      "cue-1",
      "cue-2",
    ]);
    expect(
      envelope.result.cues.every(
        (cue) => cue.outcome.kind === "occurrences",
      ),
    ).toBe(true);
    await expect(firstGroup.locator(".cue-result-identity h3")).toHaveText(
      "First cue",
    );
    await expect(
      firstGroup.locator('.cue-result-window [title="Raw source-search start: 1 seconds"]'),
    ).toHaveText("Start 00:01");
    for (const group of [firstGroup, secondGroup]) {
      await expect(group.locator(".cue-result-method strong")).toHaveText(
        envelope.result.configuration.matching.method,
      );
      await expect(group.getByText(/^Method$/)).toHaveCount(1);
    }

    const rawScores = envelope.result.cues.flatMap((cue) =>
      cue.outcome.kind === "occurrences"
        ? (cue.outcome.occurrences ?? []).map((occurrence) => occurrence.score)
        : [],
    );
    const scoreBadges = page.locator(".occurrence-score-badge");
    const positionCells = page.locator(".occurrence-position");
    expect(rawScores.length).toBeGreaterThan(0);
    await expect(scoreBadges).toHaveCount(rawScores.length);
    await expect(positionCells).toHaveCount(rawScores.length);
    for (let index = 0; index < rawScores.length; index += 1) {
      const rawScore = rawScores[index];
      expect(Number.isFinite(rawScore)).toBe(true);
      await expect(scoreBadges.nth(index)).toHaveText(
        /^-?\d+\.\d{2}$/,
      );
      await expect(scoreBadges.nth(index)).not.toContainText("%");
      await expect(scoreBadges.nth(index)).toHaveAttribute(
        "title",
        `Raw similarity score: ${rawScore}`,
      );
      await expect(scoreBadges.nth(index)).toHaveText(
        rawScore.toFixed(2),
      );
    }

    await expect(firstGroup.locator(".cue-result-window")).toContainText(
      "Start 00:01",
    );
    await expect(firstGroup.locator(".cue-result-window")).not.toContainText("End");
    await expect(secondGroup.locator(".cue-result-window")).toHaveCount(0);
    const firstDisclosure = firstGroup.getByRole("button", {
      name: "Collapse results for First cue",
    });
    await expect(firstDisclosure).toHaveAttribute("aria-expanded", "true");
    const firstDetailsId = await firstDisclosure.getAttribute("aria-controls");
    if (!firstDetailsId) {
      throw new Error("Disclosure has no aria-controls target");
    }
    await expect(
      firstGroup.locator(".cue-result-group__header > button").last(),
    ).toHaveAttribute("aria-controls", firstDetailsId);
    await expect(firstGroup.locator(`[id="${firstDetailsId}"]`)).toBeVisible();

    for (const viewport of [
      { label: "390", width: 390, height: 844 },
      { label: "820", width: 820, height: 1180 },
      { label: "1440", width: 1440, height: 900 },
    ]) {
      await test.step(
        `disclosure stays at the right edge at ${viewport.label}px`,
        async () => {
          await assertDisclosureRightEdge(
            page,
            firstGroup,
            firstDisclosure,
            viewport,
          );
        },
      );
    }

    await firstDisclosure.click();
    await expect(firstGroup.locator(".cue-result-group__details")).toBeHidden();
    await expect(secondGroup.locator(".cue-result-group__details")).toBeVisible();
    await firstGroup
      .getByRole("button", { name: "Expand results for First cue" })
      .click();
    await expect(firstGroup.locator(".cue-result-group__details")).toBeVisible();

    const downloadPromise = page.waitForEvent("download");
    await downloadButton.click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe(
      `analysis-${analysisId}-result.json`,
    );
    const downloadedBytes = await readDownload(download);
    expect(responseBodies.some((body) => body.equals(downloadedBytes))).toBe(true);

    assertOnlyDocumentedPublicApiCalls(calls, baseUrl);
    expect(countCalls(calls, "POST", /^\/api\/v1\/assets\/source-media$/)).toBe(1);
    expect(countCalls(calls, "POST", /^\/api\/v1\/assets\/cue$/)).toBe(2);
    expect(countCalls(calls, "POST", /^\/api\/v1\/analyses$/)).toBe(1);
    expect(countCalls(calls, "GET", /^\/api\/v1\/analyses\/[^/]+$/)).toBeGreaterThan(0);
    expect(
      countCalls(
        calls,
        "GET",
        /^\/api\/v1\/analyses\/[^/]+\/result$/,
      ),
    ).toBeGreaterThan(0);
  });

  test("presents no-match as a visually distinct successful cue outcome", async ({ page }) => {
    test.setTimeout(analysisTimeoutMs + 30_000);
    const baseUrl = webuiBaseUrl();
    const fixtures = fixturePaths();
    const calls = beginPublicApiAudit(page);

    const { analysisId } = await submitAnalysis(page, baseUrl, fixtures.sourceMedia, [
      { path: fixtures.noMatchCue },
    ]);
    const resultBodies = captureSuccessfulResultBodies(page, analysisId);

    await expect(page.getByText("No match found for this cue.")).toBeVisible({
      timeout: analysisTimeoutMs,
    });
    const noMatchGroup = page.locator(".cue-result-group--no_match");
    await expect(noMatchGroup).toBeVisible();
    await expect(noMatchGroup.locator(".cue-result-status")).toHaveText("0 matches");
    await expect(
      noMatchGroup.getByRole("button", { name: /^Play cue / }),
    ).toBeVisible();
    await expect(
      noMatchGroup.getByRole("button", { name: /^Play match / }),
    ).toHaveCount(0);

    const responseBodies = await resolvedResultBodies(resultBodies);
    const envelope = JSON.parse(
      responseBodies[responseBodies.length - 1].toString("utf8"),
    ) as ResultEnvelope;
    expect(envelope.result.analysis_id).toBe(analysisId);
    expect(envelope.result.cues).toHaveLength(1);
    expect(envelope.result.cues[0].outcome.kind).toBe("no_match");
    await expect(noMatchGroup.locator(".cue-result-method strong")).toHaveText(
      envelope.result.configuration.matching.method,
    );

    assertOnlyDocumentedPublicApiCalls(calls, baseUrl);
  });

  test("renders every real repeated occurrence for one cue", async ({ page }) => {
    test.setTimeout(analysisTimeoutMs + 30_000);
    const repeated = repeatedOccurrenceFixturePaths();
    test.skip(
      repeated === null,
      "Set E2E_REPEATED_SOURCE_MEDIA_PATH and E2E_REPEATED_CUE_PATH to run S0009 browser validation",
    );
    if (repeated === null) {
      return;
    }

    const baseUrl = webuiBaseUrl();
    const calls = beginPublicApiAudit(page);
    const { analysisId } = await submitAnalysis(
      page,
      baseUrl,
      repeated.sourceMedia,
      [{ path: repeated.cue, name: "Repeated cue" }],
    );
    const resultBodies = captureSuccessfulResultBodies(page, analysisId);

    await expect(page.getByRole("heading", { name: "Repeated cue" })).toBeVisible({
      timeout: analysisTimeoutMs,
    });
    const responseBodies = await resolvedResultBodies(resultBodies);
    const envelope = JSON.parse(
      responseBodies[responseBodies.length - 1].toString("utf8"),
    ) as ResultEnvelope;
    const occurrences = envelope.result.cues[0].outcome.occurrences ?? [];
    expect(occurrences.length).toBeGreaterThanOrEqual(2);
    expect(occurrences.map((item) => item.temporal_position)).toEqual(
      [...occurrences]
        .map((item) => item.temporal_position)
        .sort((left, right) => left - right),
    );
    expect(
      occurrences.every(
        (item) => item.matching_method === "normalized_cross_correlation_multi_v1",
      ),
    ).toBe(true);
    const group = page.locator(".cue-result-group");
    await expect(group).toHaveCount(1);
    await expect(
      group.getByRole("heading", { name: "Repeated cue" }),
    ).toBeVisible();
    await expect(group.locator(".cue-result-status")).toHaveText(
      `${occurrences.length} matches`,
    );
    await expect(group.locator(".cue-result-method strong")).toHaveText(
      envelope.result.configuration.matching.method,
    );
    await expect(group.locator(".cue-result-method strong")).toHaveCount(1);

    const disclosure = group.getByRole("button", {
      name: "Collapse results for Repeated cue",
    });
    await expect(disclosure).toHaveAttribute("aria-expanded", "true");
    await expect(disclosure).toHaveText("−");
    await expect(group.locator(".cue-result-group__details")).toBeVisible();

    const rows = group.locator(".occurrence-row");
    await expect(rows).toHaveCount(occurrences.length);
    await expect(page.locator(".match-badge")).toContainText(
      `${occurrences.length} matches found`,
    );
    const expectedRanks = deriveSimilarityRanks(occurrences);
    const scoreBadges = page.locator(".occurrence-score-badge");
    await expect(scoreBadges).toHaveCount(occurrences.length);
    for (let index = 0; index < occurrences.length; index += 1) {
      const row = rows.nth(index);
      await expect(row).toHaveAttribute("data-occurrence-index", String(index));
      await expect(row).toHaveAttribute(
        "data-similarity-rank",
        String(expectedRanks[index]),
      );
      await expect(row.locator(".occurrence-position")).toHaveText(
        formatExpectedClock(occurrences[index].temporal_position),
      );
      await expect(row.locator(".occurrence-position")).toHaveAttribute(
        "title",
        `Raw position: ${occurrences[index].temporal_position} seconds`,
      );
      await expect(scoreBadges.nth(index)).toHaveText(
        occurrences[index].score.toFixed(2),
      );
      await expect(scoreBadges.nth(index)).toHaveAttribute(
        "title",
        `Raw similarity score: ${occurrences[index].score}`,
      );
      await expect(scoreBadges.nth(index)).not.toContainText("%");
    }

    await disclosure.click();
    await expect(group.locator(".cue-result-group__details")).toBeHidden();
    const expand = group.getByRole("button", {
      name: "Expand results for Repeated cue",
    });
    await expect(expand).toHaveAttribute("aria-expanded", "false");
    await expect(expand).toHaveText("+");
    await expand.click();
    await expect(group.locator(".cue-result-group__details")).toBeVisible();

    const cueAudioPath = `/api/v1/analyses/${encodeURIComponent(analysisId)}/cues/cue-1/audio`;
    const cueAudioResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "GET" &&
        new URL(response.url()).pathname === cueAudioPath,
    );
    await group.getByRole("button", { name: "Play cue Repeated cue" }).click();
    const cueResponse = await cueAudioResponse;
    expect(cueResponse.status()).toBe(200);
    expect(cueResponse.headers()["content-type"]).toContain("audio/wav");
    await expect(group.getByRole("button", { name: "Stop cue Repeated cue" })).toBeVisible();
    await group.getByRole("button", { name: "Stop cue Repeated cue" }).click();
    await expect(group.getByRole("button", { name: "Play cue Repeated cue" })).toBeVisible();

    const firstOccurrencePath = `${cueAudioPath.replace(/\/audio$/, "")}/occurrences/0/audio`;
    const firstOccurrenceResponse = page.waitForResponse(
      (response) => new URL(response.url()).pathname === firstOccurrencePath,
    );
    await rows.nth(0).getByRole("button", { name: "Play match 1 for Repeated cue" }).click();
    expect((await firstOccurrenceResponse).status()).toBe(200);
    await expect(rows.nth(0).getByRole("button", { name: "Stop match 1 for Repeated cue" })).toBeVisible();

    const secondOccurrencePath = `${cueAudioPath.replace(/\/audio$/, "")}/occurrences/1/audio`;
    const secondOccurrenceResponse = page.waitForResponse(
      (response) => new URL(response.url()).pathname === secondOccurrencePath,
    );
    await rows.nth(1).getByRole("button", { name: "Play match 2 for Repeated cue" }).click();
    expect((await secondOccurrenceResponse).status()).toBe(200);
    await expect(rows.nth(0).getByRole("button", { name: "Play match 1 for Repeated cue" })).toBeVisible();
    await expect(rows.nth(1).getByRole("button", { name: "Stop match 2 for Repeated cue" })).toBeVisible();
    await rows.nth(1).getByRole("button", { name: "Stop match 2 for Repeated cue" }).click();

    const rankDiffersIndex = expectedRanks.findIndex((rank, index) => rank !== index + 1);
    expect(rankDiffersIndex).toBeGreaterThanOrEqual(0);
    const canonicalPath = `${cueAudioPath.replace(/\/audio$/, "")}/occurrences/${rankDiffersIndex}/audio`;
    const canonicalResponse = page.waitForResponse(
      (response) => new URL(response.url()).pathname === canonicalPath,
    );
    await rows
      .nth(rankDiffersIndex)
      .getByRole("button", {
        name: `Play match ${rankDiffersIndex + 1} for Repeated cue`,
      })
      .click();
    expect((await canonicalResponse).status()).toBe(200);

    await group.getByRole("button", { name: "Collapse results for Repeated cue" }).click();
    await expect(group.locator(".cue-result-group__details")).toBeHidden();
    await group.getByRole("button", { name: "Expand results for Repeated cue" }).click();
    await expect(group.locator(".cue-result-group__details")).toBeVisible();
    await expect(group.getByRole("button", { name: /^Stop match / })).toHaveCount(0);
    await expect(
      rows.nth(rankDiffersIndex).getByRole("button", {
        name: `Play match ${rankDiffersIndex + 1} for Repeated cue`,
      }),
    ).toBeVisible();

    assertOnlyDocumentedPublicApiCalls(calls, baseUrl);
  });

  test("enforces local time validation and the 20-cue admission guard", async ({ page }) => {
    const baseUrl = webuiBaseUrl();
    const fixtures = fixturePaths();

    await fillAnalysisForm(page, baseUrl, fixtures.sourceMedia, [
      { path: fixtures.matchingCue, startText: "not-a-time" },
    ]);

    const createButton = page.getByRole("button", { name: "Create Analysis" });
    await expect(createButton).toBeDisabled();
    await expect(page.getByText("Enter a start time as MM:SS or HH:MM:SS.")).toBeVisible();

    for (const invalidTime of ["1::02", "-1:02", "+1:02", "1e2:03", "NaN:03", "00:60", "01:60:00"]) {
      await page.locator(".cue-row").first().getByPlaceholder("e.g. 00:00:01").fill(invalidTime);
      await expect(createButton).toBeDisabled();
    }

    const analysesRequests: string[] = [];
    page.on("request", (request) => {
      if (
        request.method() === "POST" &&
        new URL(request.url()).pathname === "/api/v1/analyses"
      ) {
        analysesRequests.push(request.url());
      }
    });

    await page.locator(".cue-row").first().getByPlaceholder("e.g. 00:00:01").fill("");
    await expect(createButton).toBeEnabled();

    // Cross-field validation: start >= end is rejected before any upload.
    await page.locator(".cue-row").first().getByPlaceholder("e.g. 00:00:01").fill("00:00:10");
    await page.locator(".cue-row").first().getByPlaceholder("e.g. 00:00:03").fill("00:00:05");
    await expect(createButton).toBeDisabled();
    await expect(page.getByText("Start time must be before end time.")).toBeVisible();

    expect(analysesRequests).toHaveLength(0);

    // 20-cue admission guard: starting from 1 row, 19 additions reach the
    // limit; the button then disables and no 21st row can be created.
    await page.locator(".cue-row").first().getByPlaceholder("e.g. 00:00:01").fill("");
    await page.locator(".cue-row").first().getByPlaceholder("e.g. 00:00:03").fill("");
    const addButton = page.getByRole("button", { name: "Add another cue" });
    for (let i = 0; i < 19; i += 1) {
      await addButton.click();
    }
    await expect(page.locator(".cue-row")).toHaveCount(20);
    await expect(addButton).toBeDisabled();
    await expect(page.getByText("Maximum of 20 cues reached.")).toBeVisible();
  });

  test("serializes blank, end-only, start-only, and both-bound source windows", async ({ page }) => {
    test.setTimeout(analysisTimeoutMs + 30_000);
    const baseUrl = webuiBaseUrl();
    const fixtures = fixturePaths();
    const { requestCues } = await submitAnalysis(page, baseUrl, fixtures.sourceMedia, [
      { path: fixtures.matchingCue },
      { path: fixtures.matchingCue, endText: "00:00:00.0004" },
      { path: fixtures.matchingCue, startText: "00:00:00.0001" },
      {
        path: fixtures.matchingCue,
        startText: "00:00:00.0001",
        endText: "00:00:00.0004",
      },
    ]);

    expect(requestCues[0].trim_start_seconds ?? null).toBeNull();
    expect(requestCues[0].trim_end_seconds ?? null).toBeNull();
    expect(requestCues[1].trim_start_seconds ?? null).toBeNull();
    expect(requestCues[1].trim_end_seconds).toBe(0.0004);
    expect(requestCues[2].trim_start_seconds).toBe(0.0001);
    expect(requestCues[2].trim_end_seconds ?? null).toBeNull();
    expect(requestCues[3].trim_start_seconds).toBe(0.0001);
    expect(requestCues[3].trim_end_seconds).toBe(0.0004);
  });

  test("proxies a source-media upload larger than the old Nginx body-size ceiling through to the API (S0001)", async ({
    page,
  }) => {
    test.setTimeout(analysisTimeoutMs + 30_000);
    const baseUrl = webuiBaseUrl();
    const fixtures = fixturePaths();
    const calls = beginPublicApiAudit(page);
    await page.emulateMedia({ reducedMotion: "reduce" });

    // Nginx's own historical default (`client_max_body_size 1m`, unset in
    // the pre-S0001 embedded config) is 1,048,576 bytes. 2 MiB clears that
    // ceiling with margin while staying far below ACL's 500 MiB limit, so
    // this proves the proxy boundary without needing a 287 MB fixture.
    const largeSourceMedia = buildDeterministicWavBuffer(2 * 1024 * 1024);

    await openHomePage(page, baseUrl);
    await sourceMediaInput(page).setInputFiles({
      name: "proxy-boundary-source.wav",
      mimeType: "audio/wav",
      buffer: largeSourceMedia,
    });
    await page
      .getByLabel("Cue 1", { exact: true })
      .setInputFiles(fixtures.matchingCue);

    const sourceMediaResponsePromise = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === "/api/v1/assets/source-media",
      { timeout: analysisTimeoutMs },
    );
    const uploadingStatusPromise = page
      .locator('.cues-panel [data-activity-phase="uploading"]', {
        hasText: "Uploading media…",
      })
      .waitFor({ state: "visible" });
    const staticReducedMotionArtworkPromise = page.waitForFunction(() => {
      const bar = document.querySelector(
        '.acl-activity--uploading .acl-activity__bar',
      );
      const visual = document.querySelector(
        '.acl-activity--uploading .acl-activity__visual',
      );
      return (
        bar instanceof HTMLElement &&
        visual instanceof HTMLElement &&
        getComputedStyle(bar).animationName === "none" &&
        visual.getBoundingClientRect().width > 0
      );
    });
    await page.getByRole("button", { name: "Create Analysis" }).click();

    await Promise.all([
      uploadingStatusPromise,
      staticReducedMotionArtworkPromise,
    ]);
    const sourceMediaResponse = await sourceMediaResponsePromise;

    // A proxy-generated rejection would be a 413 from Nginx itself, before
    // ACL's own bounded upload handling ever runs. Proving ACL answered
    // (a real, well-formed AssetPublic body) rather than a proxy 413 is
    // exactly the observable this regression is for.
    expect(sourceMediaResponse.status()).not.toBe(413);
    expect(sourceMediaResponse.ok()).toBe(true);

    const asset = (await sourceMediaResponse.json()) as {
      size_bytes: number;
      media_type: string;
    };
    expect(asset.media_type).toBe("audio/wav");
    expect(asset.size_bytes).toBe(largeSourceMedia.length);

    assertOnlyDocumentedPublicApiCalls(calls, baseUrl);
    expect(
      countCalls(calls, "POST", /^\/api\/v1\/assets\/source-media$/),
    ).toBe(1);
  });

  test("renders only the sanitized Error contract for rejected media", async ({
    page,
  }) => {
    test.setTimeout(analysisTimeoutMs + 30_000);
    const baseUrl = webuiBaseUrl();
    const fixtures = fixturePaths();
    const calls = beginPublicApiAudit(page);

    await openHomePage(page, baseUrl);
    await sourceMediaInput(page).setInputFiles({
      name: "unsupported-source.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("S0004 deliberately unsupported media"),
    });
    await page
      .getByLabel("Cue 1", { exact: true })
      .setInputFiles(fixtures.matchingCue);

    const errorResponsePromise = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname ===
          "/api/v1/assets/source-media" &&
        !response.ok(),
      { timeout: analysisTimeoutMs },
    );
    await page.getByRole("button", { name: "Create Analysis" }).click();

    const errorResponse = await errorResponsePromise;
    const error = (await errorResponse.json()) as ErrorEnvelope;
    expect(Object.keys(error).sort()).toEqual([
      "correlation_id",
      "error_code",
      "message",
    ]);
    expect(error.error_code).toBe("unsupported_media");
    expect(error.message.length).toBeGreaterThan(0);
    expect(error.correlation_id).toMatch(/^[0-9a-f]{32}$/);
    expect(error.message).not.toMatch(
      /unsupported-source\.txt|S0004 deliberately|Traceback|\/home\/|\/tmp\/|SELECT\s|INSERT\s/i,
    );

    await expect(page.getByRole("alert")).toHaveText(
      `${error.message} (error_code: ${error.error_code}, correlation_id: ${error.correlation_id})`,
    );
    await expect(page.locator("[data-activity-phase]")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Create Analysis" })).toBeVisible();
    expect(countCalls(calls, "POST", /^\/api\/v1\/analyses$/)).toBe(0);
    assertOnlyDocumentedPublicApiCalls(calls, baseUrl);
  });

  test("accepts a WebM source with audio through the real WebUI/API path (S0002)", async ({
    page,
  }) => {
    test.setTimeout(analysisTimeoutMs + 30_000);
    const baseUrl = webuiBaseUrl();
    const fixtures = fixturePaths();
    const webmSourceMedia = webmSourceMediaPath();
    const calls = beginPublicApiAudit(page);

    const sourceMediaResponsePromise = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === "/api/v1/assets/source-media",
      { timeout: analysisTimeoutMs },
    );

    // `submitAnalysis` already asserts the standard success path (the
    // Results card appears, no status card is rendered, and the real
    // `/api/v1/analyses` response is explicitly 202 before its body is
    // treated as an Analysis -- S0005 section 4.9) -- exactly what this
    // scenario needs for "an Analysis is created and reaches a visible
    // terminal state".
    const analysisPromise = submitAnalysis(page, baseUrl, webmSourceMedia, [
      // Blank Name/Start/End: the direct regression for the user's
      // observed WebM + blank-trim path (S0005 section 4.10).
      { path: fixtures.matchingCue },
    ]);

    // Not rejected as unsupported_media, and detected as video/webm --
    // proven from the real upload response, not inferred from later steps.
    const sourceMediaResponse = await sourceMediaResponsePromise;
    expect(sourceMediaResponse.ok()).toBe(true);
    const asset = (await sourceMediaResponse.json()) as {
      media_type: string;
    };
    expect(asset.media_type).toBe("video/webm");

    const { requestCues } = await analysisPromise;

    // S0005 section 4.10: blank Start/End must serialize as null, never
    // as 0/0 -- the exact regression for the user's originally rejected
    // blank-trim WebM request.
    expect(requestCues[0].trim_start_seconds ?? null).toBeNull();
    expect(requestCues[0].trim_end_seconds ?? null).toBeNull();

    await expect(
      page.getByRole("button", { name: "Download result JSON" }),
    ).toBeVisible({ timeout: analysisTimeoutMs });
    await expect(page.locator("[data-activity-phase]")).toHaveCount(0);

    assertOnlyDocumentedPublicApiCalls(calls, baseUrl);
    expect(
      countCalls(calls, "POST", /^\/api\/v1\/assets\/source-media$/),
    ).toBe(1);
  });

  test("rejects an out-of-range source window with a safe advisory and no fabricated Result (S0008)", async ({
    page,
  }) => {
    test.setTimeout(analysisTimeoutMs + 30_000);
    const baseUrl = webuiBaseUrl();
    const fixtures = fixturePaths();
    const calls = beginPublicApiAudit(page);

    const sourceMediaResponsePromise = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === "/api/v1/assets/source-media",
      { timeout: analysisTimeoutMs },
    );
    const cueResponsePromise = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === "/api/v1/assets/cue",
      { timeout: analysisTimeoutMs },
    );

    // 99:59 (5,999s) exceeds the controlled source fixture's duration, so
    // the server-side source-window rejection is deterministic. Start is
    // left blank.
    const submission = await submitAnalysisExpectingRejection(
      page,
      baseUrl,
      fixtures.sourceMedia,
      [{ path: fixtures.matchingCue, endText: "99:59" }],
    );

    // Both real uploads succeeded before the Analysis-creation rejection.
    const sourceMediaResponse = await sourceMediaResponsePromise;
    expect(sourceMediaResponse.ok()).toBe(true);
    const cueResponse = await cueResponsePromise;
    expect(cueResponse.ok()).toBe(true);

    expect(submission.status).toBe(400);
    expect(submission.error.error_code).toBe("validation_error");
    expect(submission.requestCues[0].trim_start_seconds ?? null).toBeNull();
    expect(submission.requestCues[0].trim_end_seconds).toBe(99 * 60 + 59);

    // The existing public Error notice remains visible with its exact
    // message/error_code/correlation_id.
    await expect(page.getByRole("alert")).toHaveText(
      `${submission.error.message} (error_code: ${submission.error.error_code}, correlation_id: ${submission.error.correlation_id})`,
    );

    // The safe, conditional source-window advisory is shown alongside it.
    const advisory = page.getByRole("status");
    await expect(advisory).toBeVisible();
    await expect(advisory).toContainText("search window inside the source media");
    await expect(advisory).toContainText("source duration");
    await expect(advisory).not.toContainText("inside the cue file");
    await expect(advisory).not.toContainText("invalid");
    await expect(advisory).not.toContainText(/\d+(\.\d+)?\s*s(econds)?\b/i);

    // No Analysis ID is adopted, no Results heading appears, and no status
    // polling ever starts -- the Results success state is not fabricated.
    await expect(page.getByRole("heading", { name: "Results" })).toHaveCount(0);
    expect(
      countCalls(calls, "GET", /^\/api\/v1\/analyses\/[^/]+$/),
    ).toBe(0);

    assertOnlyDocumentedPublicApiCalls(calls, baseUrl);
    expect(countCalls(calls, "POST", /^\/api\/v1\/analyses$/)).toBe(1);
  });

  test("does not show the trim advisory for a blank-trim validation_error unrelated to trim bounds (S0005)", async ({
    page,
  }) => {
    test.setTimeout(analysisTimeoutMs + 30_000);
    const baseUrl = webuiBaseUrl();
    const fixtures = fixturePaths();

    // A real server-side validation_error unrelated to trim bounds (an
    // over-length Cue label -- S0003's own `MAX_CUE_LABEL_CODEPOINTS`,
    // 80), with Start/End left blank throughout. The Name field's `
    // maxLength` HTML attribute is a client-side convenience only; it is
    // removed here so this scenario can prove genuine backend validation,
    // not to bypass any documented public contract. No endpoint is mocked
    // or intercepted -- the request reaches the real API.
    await fillAnalysisForm(page, baseUrl, fixtures.sourceMedia, [
      { path: fixtures.matchingCue },
    ]);
    const nameField = page
      .locator(".cue-row")
      .first()
      .getByPlaceholder("e.g. Notification sound");
    await nameField.evaluate((element) => element.removeAttribute("maxlength"));
    await nameField.fill("x".repeat(81));

    const createButton = page.getByRole("button", { name: "Create Analysis" });
    await expect(createButton).toBeEnabled();

    const analysesResponsePromise = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === "/api/v1/analyses",
      { timeout: analysisTimeoutMs },
    );
    const analysesRequestPromise = page.waitForRequest(
      (request) =>
        request.method() === "POST" &&
        new URL(request.url()).pathname === "/api/v1/analyses",
    );
    await createButton.click();

    const analysesRequest = await analysesRequestPromise;
    const requestBody = analysesRequest.postDataJSON() as {
      cues: AnalysisCueRequestBody[];
    };
    expect(requestBody.cues[0].trim_start_seconds ?? null).toBeNull();
    expect(requestBody.cues[0].trim_end_seconds ?? null).toBeNull();

    const analysesResponse = await analysesResponsePromise;
    expect(analysesResponse.status()).toBe(400);
    const error = (await analysesResponse.json()) as ErrorEnvelope;
    expect(error.error_code).toBe("validation_error");

    await expect(page.getByRole("alert")).toBeVisible();
    // The trim-specific advisory must not appear: no trim text was
    // supplied, so this validation_error is never attributable to trim
    // bounds.
    await expect(page.getByRole("status")).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Results" })).toHaveCount(0);
  });

  test("opens Help and configures a persistent per-Analysis similarity setting", async ({
    page,
  }) => {
    test.setTimeout(analysisTimeoutMs * 2 + 30_000);
    const baseUrl = webuiBaseUrl();
    const fixtures = fixturePaths();
    await openHomePage(page, baseUrl);

    await page.evaluate(() =>
      window.localStorage.setItem(
        "audio-cue-locator.minimum-similarity-score.v1",
        "not-a-valid-number",
      ),
    );
    await page.reload({ waitUntil: "domcontentloaded" });
    expect(
      await page.evaluate(() =>
        window.localStorage.getItem(
          "audio-cue-locator.minimum-similarity-score.v1",
        ),
      ),
    ).toBeNull();

    const helpButton = page.getByRole("button", { name: "Help" });
    await helpButton.focus();
    await page.keyboard.press("Enter");
    const helpDialog = page.getByRole("dialog", { name: "Help" });
    await expect(helpDialog).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(helpDialog).toBeHidden();
    await expect(helpButton).toBeFocused();

    const settingsButton = page.getByRole("button", { name: "Settings" });
    await settingsButton.click();
    const settingsDialog = page.getByRole("dialog", { name: "Settings" });
    await expect(settingsDialog).toBeVisible();
    const range = settingsDialog.getByRole("slider", {
      name: "Minimum similarity score",
    });
    await expect(range).toHaveAttribute("min", "0");
    await expect(range).toHaveAttribute("max", "1");
    await expect(range).toHaveAttribute("step", "0.01");
    await expect(range).toHaveValue("0.71");
    await assertRemovedSettingsCopyAbsent(settingsDialog);
    await expect(settingsDialog.locator("output")).toHaveText("0.71");
    const defaultDialogText = (await settingsDialog.innerText()).toLowerCase();
    expect(defaultDialogText).not.toMatch(/%|confidence|probability|precision|accuracy/);

    await range.fill("0.90");
    await expect(settingsDialog.locator("output")).toHaveText("0.90");
    await assertRemovedSettingsCopyAbsent(settingsDialog);
    await settingsDialog.getByRole("button", { name: "Close", exact: true }).click();
    await expect(settingsDialog).toBeHidden();
    await expect(settingsButton).toBeFocused();

    await settingsButton.click();
    await expect(range).toHaveValue("0.9");
    await expect(settingsDialog.locator("output")).toHaveText("0.90");
    await assertRemovedSettingsCopyAbsent(settingsDialog);
    await page.keyboard.press("Escape");
    await expect(settingsButton).toBeFocused();

    await page.reload({ waitUntil: "domcontentloaded" });
    const restoredSettingsButton = page.getByRole("button", { name: "Settings" });
    await restoredSettingsButton.click();
    const restoredDialog = page.getByRole("dialog", { name: "Settings" });
    await expect(
      restoredDialog.getByRole("slider", { name: "Minimum similarity score" }),
    ).toHaveValue("0.9");
    await assertRemovedSettingsCopyAbsent(restoredDialog);
    expect(
      await page.evaluate(() =>
        window.localStorage.getItem(
          "audio-cue-locator.minimum-similarity-score.v1",
        ),
      ),
    ).toBe("0.9");
    await page.keyboard.press("Escape");

    const custom = await submitAnalysis(page, baseUrl, fixtures.sourceMedia, [
      { path: fixtures.matchingCue },
    ]);
    expect(custom.minimumSimilarityScore).toBe(0.9);
    const customResultResponse = await page.request.get(
      `${baseUrl}/api/v1/analyses/${encodeURIComponent(custom.analysisId)}/result`,
    );
    expect(customResultResponse.status()).toBe(200);
    const customResult = (await customResultResponse.json()) as ResultEnvelope;
    expect(
      customResult.result.configuration.matching.acceptance_threshold,
    ).toBe(0.9);

    await page.getByRole("button", { name: "Settings" }).click();
    const resetDialog = page.getByRole("dialog", { name: "Settings" });
    await resetDialog
      .getByRole("button", { name: "Reset to recommended default" })
      .click();
    await expect(resetDialog.locator("output")).toHaveText("0.71");
    await assertRemovedSettingsCopyAbsent(resetDialog);
    expect(
      await page.evaluate(() =>
        window.localStorage.getItem(
          "audio-cue-locator.minimum-similarity-score.v1",
        ),
      ),
    ).toBeNull();
    await page.keyboard.press("Escape");
    await page.reload({ waitUntil: "domcontentloaded" });

    const reset = await submitAnalysis(page, baseUrl, fixtures.sourceMedia, [
      { path: fixtures.matchingCue },
    ]);
    expect(reset.minimumSimilarityScore).toBeUndefined();
  });
});

test.describe("responsive smoke checks", () => {
  const viewports = [
    { label: "mobile", width: 390, height: 844 },
    { label: "tablet", width: 820, height: 1180 },
    { label: "desktop", width: 1440, height: 900 },
  ] as const;

  for (const viewport of viewports) {
    test(`no horizontal scroll and reachable workflow at ${viewport.label} width`, async ({
      page,
    }) => {
      const baseUrl = webuiBaseUrl();
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await openHomePage(page, baseUrl);

      const overflow = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }));
      expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth);

      const logo = page.getByAltText("Audio Cue Locator");
      await expect(logo).toBeVisible();
      const logoBox = await logo.boundingBox();
      expect(logoBox?.width ?? 0).toBeGreaterThan(0);

      const headingTexts = await page.getByRole("heading", { level: 2 }).allTextContents();
      expect(headingTexts.indexOf("Source media")).toBeLessThan(headingTexts.indexOf("Cues"));

      await expect(sourceMediaInput(page)).toBeAttached();
      await expect(page.getByRole("button", { name: "Create Analysis" })).toBeVisible();

      if (viewport.label === "mobile") {
        const nameField = page.getByPlaceholder("e.g. Notification sound");
        const startField = page.getByPlaceholder("e.g. 00:00:01");
        const nameBox = await nameField.boundingBox();
        const startBox = await startField.boundingBox();
        // Optional fields stack vertically rather than overflowing sideways.
        expect((startBox?.y ?? 0)).toBeGreaterThan((nameBox?.y ?? 0));

        const createButton = page.getByRole("button", { name: "Create Analysis" });
        const createBox = await createButton.boundingBox();
        expect(createBox?.height ?? 0).toBeGreaterThanOrEqual(40);
      }

      if (viewport.label === "desktop") {
        const appFrame = page.locator(".app-frame");
        const frameBox = await appFrame.boundingBox();
        expect(frameBox?.width ?? 0).toBeLessThanOrEqual(900);

        const utilityBar = page.locator(".utility-bar");
        const utilityBox = await utilityBar.boundingBox();
        expect(utilityBox?.y ?? 100).toBeLessThan(60);

        const optionalFields = page.locator(".optional-fields").first();
        const fieldsBox = await optionalFields.boundingBox();
        expect(fieldsBox?.width ?? 0).toBeGreaterThan(300);
      }
    });
  }
});
