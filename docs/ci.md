# ACL CI

S0016 provides the `ACL CI` GitHub workflow and an independent Jenkins
Pipeline-from-SCM definition. Committing definitions is `CODE_IMPLEMENTED`,
not evidence that either service ran. CI uses synthetic committed fixtures and
temporary storage; it requires no production credentials or running ACL service.

## Shared regression contract

Use Linux, Python **3.11** with `venv`/pip, Node **20** with npm, and working
`ffmpeg` **and** `ffprobe` on PATH. Dependency installation needs approved access
to Python/npm registries. GitHub provisions FFmpeg on its hosted runner; Jenkins
requires the operator to provide this toolchain on an already approved runner.
Missing tools and incompatible versions fail preflight. No tool installation or
agent provisioning occurs on Jenkins.

Both definitions execute these commands, from the checkout root except for
the two npm commands. For a local equivalent use a disposable copy of the
checkout so editable-install metadata, dependencies and build output stay there:

```sh
set -eu
export CI_WORKDIR=$(mktemp -d)
export TMPDIR="$CI_WORKDIR/tmp"
export AUDIO_CUE_LOCATOR_ASSET_STORAGE_ROOT="$CI_WORKDIR/assets"
export AUDIO_CUE_LOCATOR_ANALYSIS_DB_PATH="$CI_WORKDIR/analysis.sqlite3"
export PYTHONDONTWRITEBYTECODE=1
export PYTEST_ADDOPTS='-p no:cacheprovider'
export npm_config_engine_strict=true
mkdir -p "$TMPDIR" "$AUDIO_CUE_LOCATOR_ASSET_STORAGE_ROOT" ci-results
python3.11 -m venv "$CI_WORKDIR/venv"
. "$CI_WORKDIR/venv/bin/activate"
python tests/test_ci_pipeline_contract.py prerequisites
python -m pip install -e '.[test]'
python -m pytest -q tests --junitxml=ci-results/pytest.xml
python tests/test_ci_pipeline_contract.py junit-structure ci-results/pytest.xml
python tests/test_ci_pipeline_contract.py junit ci-results/pytest.xml
cd webui
npm ci
npm run build
```

Save pytest's nonzero exit status when collecting a failed local run, then inspect
its authentic JUnit; do not replace it with a success status. Both services publish
JUnit after a red pytest run. `ci-results/pytest.xml` is capped at 10 MiB by the
validator, must contain cases, and must have consistent declared counts. Failures,
errors and **any skips** fail validation: this intentionally makes all skips
visible for review, including media regressions that historically skip without
FFmpeg. Tool presence alone is insufficient if decoding or a test still fails.

`tests/test_ci_pipeline_contract.py` has no extra dependency beyond the existing
pytest extra. Its CLI uses only the standard library. Its offline tests cover
definition constraints, command parity, package/lockfile consistency, missing
tools, invalid/empty JUnit, and a deliberately failing temporary pytest test with
an authentic nonzero result and failure report. They are structural checks, not
a Jenkins execution or GitHub run.

`webui/package-lock.json` locks CI dependency selection; `npm ci` rejects drift
against the unmodified `package.json`. **The existing Dockerfile still uses
`npm install` and does not copy the lockfile before installation.** Image-build
dependency resolution is therefore different; aligning it needs separately
authorized work. No identical image/CI dependency guarantee is claimed. Python
dependency ranges also remain as declared in `pyproject.toml`.

Real-browser Playwright E2E is a separate optional stage, requiring explicit
runner/browser provisioning, controlled media inputs and a running test stack.
It is not installed, executed or counted by this baseline; the existing
`webui/tests/e2e/standalone-flow.spec.ts` stays unchanged.

## GitHub Actions

`.github/workflows/ci.yml` runs on pushes to `main` and manual
`workflow_dispatch`. Expect workflow **ACL CI**, job/check **Python regression
and WebUI build**. It uses `ubuntu-24.04`, read-only `contents` permission,
official actions pinned to full revisions, and a 30-minute timeout. Checkout
uses the event SHA, checks it against `git rev-parse HEAD`, and prints the full
revision. There is no Jenkins trigger, deployment or required PR workflow.

The `acl-junit-<SHA>-<attempt>` artifact contains only the JUnit XML and expires
after seven days. Publication is attempted for structurally valid, bounded reports
even on test failure; a missing report
fails rather than becoming a green absent run. WebUI build still runs after
pytest failure when pytest was reached. Setup/checkout/install failures, red
tests, report validation errors and build failures produce a failing check;
timeouts/cancellation are not success.

After the operator reviews and versions these files, record actual workflow/run
ID and attempt, full SHA, start/end timestamps, terminal conclusion and URL,
executed test/failure/error/skip counts, and WebUI outcome for any claim
`GITHUB_ACTIONS_RUN_OBSERVED`. A published commit or an unrun workflow cannot
support the statement **GitHub CI passed**.

## Dedicated Jenkins job (operator setup)

1. Review an existing non-controller agent's isolation and dependency access.
   It must have a disposable workspace, no production mounts or privileged
   sockets, and no accessible controller/inbound-agent credentials. The existing
   `debian-tests` label is not automatically approved: its shared identity and
   remoting-secret exposure need independent review. If there is no safe runner,
   record **JENKINS_RUNTIME_BLOCKED**; do not create an agent or weaken security.
2. Manually create a dedicated Pipeline job (suggested name `acl-ci`) with
   **Pipeline script from SCM**, Git pointing at ACL, script path
   `ci/Jenkinsfile`, and a trusted full commit SHA pinned in SCM configuration.
   Disable lightweight checkout so the script's revision can be reviewed against
   that pin. Install/approve existing Pipeline, Git and JUnit capabilities through
   the operator's normal process. No job is created by adding this file.
3. Restrict configuration/build access to trusted operators; leave SCM polling,
   webhooks and remote/ASF triggers off. On the first run, the declarative job
   parameters may need to be materialized before a parameterized execution.
   Blank defaults intentionally fail before allocating an agent.
4. For each approved execution, supply one exact `APPROVED_AGENT_LABEL` (letters,
   digits, underscore or hyphen; no label expressions) and `EXPECTED_COMMIT`
   (40 lowercase hex characters). This parameter is an assertion of operator
   approval, not an isolation verifier. Pin the same SHA in the job SCM setting:
   the parameter never selects arbitrary code, test paths or shell commands.
5. The job checks out SCM in its isolated workspace and rejects any mismatch
   among requested SHA, Git plugin SHA and actual full HEAD. It logs/retains
   `ci-results/revision.txt` and associates the SHA with the build description.
   Verify the job's SCM pin also identifies the Jenkinsfile that was executed.

Concurrent builds of this job are disabled, execution is limited to 30 minutes
(including agent wait), and only ten builds are retained. JUnit publication uses
`post { always { ... } }`, `allowEmptyResults: false`, and reduced console output.
Pytest failure is caught with explicit build/stage **FAILURE**, retaining red
status while publishing the report. Missing/malformed/empty reports, failures,
errors or skips cannot pass. WebUI failure is fatal. A post-validation failure
is also recorded as FAILURE while allowing the frontend check to run; an
unexecuted build is never a passed frontend check.
Temporary storage and the dedicated workspace are removed at completion; only
JUnit and reduced revision metadata are retained, not media or databases.

For **Jenkins build and JUnit observed**, record the actual job/build number,
requested and checked-out SHA, Jenkinsfile/SCM pin, timestamps, terminal status,
JUnit test/failure/error/skip totals, WebUI outcome and observation URL. A queued,
blocked or cancelled job is not a passing build. Any later ASF read-only
observation needs its own supported authorization.

## Diagnostics and proof levels

| Observation | Interpretation |
| --- | --- |
| `Missing runner prerequisite`, Python/Node version error, or `JENKINS_RUNTIME_BLOCKED` | Fix/review the runner; no successful baseline was executed. |
| Pytest assertion/error and nonzero exit | Real regression failure; inspect JUnit and request a narrowly scoped correction. |
| Missing, empty, malformed, count-inconsistent or oversized JUnit | Failed/invalid evidence, even if a command otherwise returned zero. |
| JUnit skipped count above zero | Failed baseline pending review; media tests must execute. |
| `npm ci` rejects a missing/mismatched lockfile | Dependency contract failure; never fall back to `npm install`. |
| `npm run build` exits nonzero | Frontend failure; preserve the failing check/build. |
| Definition committed | Source entry points exist; no live execution implied. |
| GitHub CI passed | Requires an observed successful run at the recorded SHA. |
| Jenkins build and JUnit observed | Requires an actual operator-approved build and its real report. |
| ASF canonical Test Gate not integrated | This baseline creates no canonical ASF dispatch or evidence. |

Keep `SPEC_VALIDATED`, `CODE_IMPLEMENTED`, `OFFLINE_TESTS`,
`GITHUB_ACTIONS_RUN_OBSERVED`, `JENKINS_JOB_CONFIGURED`,
`JENKINS_BUILD_OBSERVED` and `JENKINS_JUNIT_OBSERVED` separate, with actual
outcomes or NOT_RUN/BLOCKED as appropriate. Always retain
`ASF_CANONICAL_CI_DISPATCH=NOT_ENABLED` and
`ASF_CANONICAL_TEST_EVIDENCE=NOT_CLAIMED`. This ACL job does not activate
`execute_jenkins_job`, GitHub execute adapters, n8n routing, or `test_evidence.v1`.
Source versioning, external setup and live executions remain operator actions.

## S0016 offline observation

At baseline `e9cf9e441d263475ccb7cf984e9bc6d59b066c75`, an isolated run on
2026-10-26 used Python 3.11.15, Node 20.20.2, npm 11.11.0 and FFmpeg/ffprobe
9.0.1. The eight CI contract tests passed; actionlint 1.7.12 accepted the workflow;
`npm ci` and the WebUI build passed. Jenkins validation was structural inspection
only. The full Python run returned **exit 1: 552 passed, 5 failed, 0 skipped**.
The report validator retained all 557 cases and rejected the failed baseline.

| Existing test file | Observed failure requiring separately scoped review |
| --- | --- |
| `tests/operational/test_observability.py` | Persisted matching-stage message differs from the public sanitized message. |
| `tests/operational/test_retention_cleanup.py` | Startup recovery changes the supposedly active RUNNING analysis to FAILED. |
| `tests/test_api_v1_contracts.py` (two tests) | Generated OpenAPI omits expected error responses and defaulted fields from schema `required`. |
| `tests/test_matching_robustness.py` | Fixture manifest records FFmpeg absent at authoring, conflicting with the required real toolchain. |

Resolved Python packages included pytest 8.4.2, FastAPI 0.162.0, Pydantic 2.14.1,
NumPy 2.4.6, SciPy 1.17.1 and httpx 0.28.1. These are observations, not new pins.
`OFFLINE_TESTS=FAILED`; no existing product code, tests or manifest was changed
to hide those failures. Neither a GitHub run nor Jenkins job/build/JUnit was
observed, so all live proof levels remain NOT_RUN and Jenkins runtime approval
remains `JENKINS_RUNTIME_BLOCKED`. The definitions will report this suite as red
until separately authorized corrections are made.
