import {
  expect,
  test,
  type Download,
  type Page,
  type Request,
} from "@playwright/test";

/**
 * Browser-level validation for M6-05.
 *
 * The test runner is intentionally supplied by the controlled validation
 * environment: M6-05 does not authorize package-manifest or lockfile changes.
 * These tests never intercept, fulfill, or mock a request. Every assertion is
 * made against the running WebUI and its real REST API v1 backend.
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

async function openSubmissionPage(page: Page, baseUrl: string): Promise<void> {
  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await expect(
    page.getByRole("heading", { name: "Audio Cue Locator" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Submit media and cues" }),
  ).toBeVisible();
}

async function submitAnalysis(
  page: Page,
  baseUrl: string,
  sourceMediaPath: string,
  cuePaths: string[],
): Promise<string> {
  await openSubmissionPage(page, baseUrl);
  await page.getByLabel("Source media", { exact: true }).setInputFiles(
    sourceMediaPath,
  );

  for (let index = 0; index < cuePaths.length; index += 1) {
    if (index > 0) {
      await page.getByRole("button", { name: "Add another cue" }).click();
    }
    await page
      .getByLabel(`Cue ${index + 1}`, { exact: true })
      .setInputFiles(cuePaths[index]);
  }

  const submitButton = page.getByRole("button", {
    name: "Submit and create Analysis",
  });
  await expect(submitButton).toBeEnabled();
  await submitButton.click();

  const analysisHeading = page.getByRole("heading", {
    name: /^Analysis [^\s]+$/,
  });
  await expect(analysisHeading).toBeVisible({ timeout: analysisTimeoutMs });
  await expect(page.getByText(/^Status: /)).toContainText("succeeded", {
    timeout: analysisTimeoutMs,
  });
  await expect(
    page.getByRole("button", { name: "View results" }),
  ).toBeVisible({ timeout: analysisTimeoutMs });

  const headingText = await analysisHeading.textContent();
  const analysisId = headingText?.replace(/^Analysis\s+/, "").trim();
  if (!analysisId) {
    throw new Error("The WebUI did not expose the server-issued Analysis ID");
  }
  return analysisId;
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

async function openResultPage(page: Page): Promise<void> {
  await page.getByRole("button", { name: "View results" }).click();
  await expect(
    page.getByRole("heading", { name: "Analysis result" }),
  ).toBeVisible({ timeout: analysisTimeoutMs });
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
  test("submits multiple cues, renders their results, and downloads the exact Result JSON", async ({
    page,
  }) => {
    test.setTimeout(analysisTimeoutMs + 30_000);
    const baseUrl = webuiBaseUrl();
    const fixtures = fixturePaths();
    const calls = beginPublicApiAudit(page);

    const analysisId = await submitAnalysis(page, baseUrl, fixtures.sourceMedia, [
      fixtures.matchingCue,
      fixtures.secondMatchingCue,
    ]);
    const resultBodies = captureSuccessfulResultBodies(page, analysisId);
    await openResultPage(page);

    await expect(page.getByRole("heading", { name: "Cue: cue-1" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Cue: cue-2" })).toBeVisible();
    expect(await page.getByText(/^Position: /).count()).toBeGreaterThanOrEqual(2);

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
    await page.getByRole("button", { name: "Download result JSON" }).click();
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

  test("presents no-match as a successful cue outcome", async ({ page }) => {
    test.setTimeout(analysisTimeoutMs + 30_000);
    const baseUrl = webuiBaseUrl();
    const fixtures = fixturePaths();
    const calls = beginPublicApiAudit(page);

    const analysisId = await submitAnalysis(page, baseUrl, fixtures.sourceMedia, [
      fixtures.noMatchCue,
    ]);
    const resultBodies = captureSuccessfulResultBodies(page, analysisId);
    await openResultPage(page);

    await expect(page.getByRole("heading", { name: "Cue: cue-1" })).toBeVisible();
    await expect(page.getByText("No match found for this cue.")).toBeVisible();

    const responseBodies = await resolvedResultBodies(resultBodies);
    const envelope = JSON.parse(
      responseBodies[responseBodies.length - 1].toString("utf8"),
    ) as ResultEnvelope;
    expect(envelope.result.analysis_id).toBe(analysisId);
    expect(envelope.result.cues).toHaveLength(1);
    expect(envelope.result.cues[0].outcome.kind).toBe("no_match");

    assertOnlyDocumentedPublicApiCalls(calls, baseUrl);
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

    await openSubmissionPage(page, baseUrl);
    await page.getByLabel("Source media", { exact: true }).setInputFiles({
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
    await page
      .getByRole("button", { name: "Submit and create Analysis" })
      .click();

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

    await openSubmissionPage(page, baseUrl);
    await page.getByLabel("Source media", { exact: true }).setInputFiles({
      name: "unsupported-source.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("M6-05 deliberately unsupported media"),
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
    await page
      .getByRole("button", { name: "Submit and create Analysis" })
      .click();

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
      /unsupported-source\.txt|M6-05 deliberately|Traceback|\/home\/|\/tmp\/|SELECT\s|INSERT\s/i,
    );

    await expect(page.getByRole("alert")).toHaveText(
      `${error.message} (error_code: ${error.error_code}, correlation_id: ${error.correlation_id})`,
    );
    expect(countCalls(calls, "POST", /^\/api\/v1\/analyses$/)).toBe(0);
    assertOnlyDocumentedPublicApiCalls(calls, baseUrl);
  });
});
