import {
  expect,
  test,
  type Download,
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
    cues: Array<{
      cue_id: string;
      outcome: {
        kind: "occurrences" | "no_match" | "failure";
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
    await row.getByPlaceholder("e.g. 00:00:10").fill(cue.startText);
  }
  if (cue.endText !== undefined) {
    await row.getByPlaceholder("e.g. 00:00:15").fill(cue.endText);
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
  const analysis = (await analysesResponse.json()) as { analysis_id: string };

  await expect(page.getByRole("heading", { name: "Results" })).toBeVisible({
    timeout: analysisTimeoutMs,
  });
  await expect(page.getByText(/^Status: /)).toHaveCount(0);

  return { analysisId: analysis.analysis_id, requestCues: requestBody.cues };
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
    await expect(page.getByPlaceholder("e.g. 00:00:10")).toBeVisible();
    await expect(page.getByPlaceholder("e.g. 00:00:15")).toBeVisible();

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

    // S0003 fields are actually sent in the Analysis creation request.
    expect(requestCues[0].label).toBe("First cue");
    expect(requestCues[0].trim_start_seconds).toBe(1);
    expect(requestCues[0].trim_end_seconds ?? null).toBeNull();
    expect(requestCues[1].label).toBe("Second cue");

    const resultBodies = captureSuccessfulResultBodies(page, analysisId);

    await expect(page.getByRole("heading", { name: "First cue" })).toBeVisible({
      timeout: analysisTimeoutMs,
    });
    await expect(page.getByRole("heading", { name: "Second cue" })).toBeVisible();
    expect(await page.getByText(/^Position: /).count()).toBeGreaterThanOrEqual(2);

    const downloadButton = page.getByRole("button", { name: "Download result JSON" });
    await expect(downloadButton).toBeVisible({ timeout: analysisTimeoutMs });

    // Raw scores are never presented as confidence or a percentage.
    const resultsText = (await page.locator(".results-panel").innerText()).toLowerCase();
    expect(resultsText).not.toMatch(/confidence/);
    expect(resultsText).not.toMatch(/\d%/);
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
    await expect(page.locator(".result-row--no-match")).toBeVisible();

    const responseBodies = await resolvedResultBodies(resultBodies);
    const envelope = JSON.parse(
      responseBodies[responseBodies.length - 1].toString("utf8"),
    ) as ResultEnvelope;
    expect(envelope.result.analysis_id).toBe(analysisId);
    expect(envelope.result.cues).toHaveLength(1);
    expect(envelope.result.cues[0].outcome.kind).toBe("no_match");

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

    const analysesRequests: string[] = [];
    page.on("request", (request) => {
      if (
        request.method() === "POST" &&
        new URL(request.url()).pathname === "/api/v1/analyses"
      ) {
        analysesRequests.push(request.url());
      }
    });

    await page.locator(".cue-row").first().getByPlaceholder("e.g. 00:00:10").fill("");
    await expect(createButton).toBeEnabled();

    // Cross-field validation: start >= end is rejected before any upload.
    await page.locator(".cue-row").first().getByPlaceholder("e.g. 00:00:10").fill("00:00:10");
    await page.locator(".cue-row").first().getByPlaceholder("e.g. 00:00:15").fill("00:00:05");
    await expect(createButton).toBeDisabled();
    await expect(page.getByText("Start time must be before end time.")).toBeVisible();

    expect(analysesRequests).toHaveLength(0);

    // 20-cue admission guard: starting from 1 row, 19 additions reach the
    // limit; the button then disables and no 21st row can be created.
    await page.locator(".cue-row").first().getByPlaceholder("e.g. 00:00:10").fill("");
    await page.locator(".cue-row").first().getByPlaceholder("e.g. 00:00:15").fill("");
    const addButton = page.getByRole("button", { name: "Add another cue" });
    for (let i = 0; i < 19; i += 1) {
      await addButton.click();
    }
    await expect(page.locator(".cue-row")).toHaveCount(20);
    await expect(addButton).toBeDisabled();
    await expect(page.getByText("Maximum of 20 cues reached.")).toBeVisible();
  });

  test("proxies a source-media upload larger than the old Nginx body-size ceiling through to the API (S0001)", async ({
    page,
  }) => {
    test.setTimeout(analysisTimeoutMs + 30_000);
    const baseUrl = webuiBaseUrl();
    const fixtures = fixturePaths();
    const calls = beginPublicApiAudit(page);

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
    await page.getByRole("button", { name: "Create Analysis" }).click();

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
    // Results card appears and no status card is rendered) -- exactly
    // what this scenario needs for "an Analysis is created and reaches a
    // visible terminal state".
    const analysisPromise = submitAnalysis(page, baseUrl, webmSourceMedia, [
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

    await analysisPromise;

    await expect(
      page.getByRole("button", { name: "Download result JSON" }),
    ).toBeVisible({ timeout: analysisTimeoutMs });

    assertOnlyDocumentedPublicApiCalls(calls, baseUrl);
    expect(
      countCalls(calls, "POST", /^\/api\/v1\/assets\/source-media$/),
    ).toBe(1);
  });

  test("opens and closes Help and Settings dialogs accessibly", async ({ page }) => {
    const baseUrl = webuiBaseUrl();
    await openHomePage(page, baseUrl);

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
    await expect(
      settingsDialog.getByText(/no user-configurable application/i),
    ).toBeVisible();
    await settingsDialog.getByRole("button", { name: "Close", exact: true }).click();
    await expect(settingsDialog).toBeHidden();
    await expect(settingsButton).toBeFocused();
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
        const startField = page.getByPlaceholder("e.g. 00:00:10");
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
