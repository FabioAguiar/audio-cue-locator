"""Offline CI contract tests and the two pipelines' stdlib-only preflight/report checks.

CLI: python tests/test_ci_pipeline_contract.py prerequisites|junit-structure|junit [report.xml]
These checks never substitute for running pytest, npm ci, or the WebUI build.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
MAX_JUNIT_BYTES = 10 * 1024 * 1024
SHARED_COMMANDS = (
    "python tests/test_ci_pipeline_contract.py prerequisites",
    "python -m pip install -e '.[test]'",
    "python -m pytest -q tests --junitxml=ci-results/pytest.xml",
    "python tests/test_ci_pipeline_contract.py junit-structure ci-results/pytest.xml",
    "python tests/test_ci_pipeline_contract.py junit ci-results/pytest.xml",
    "npm ci",
    "npm run build",
)


def check_lockfile(root: Path = ROOT) -> None:
    package = json.loads((root / "webui/package.json").read_text())
    lock = json.loads((root / "webui/package-lock.json").read_text())
    if lock.get("lockfileVersion") != 3:
        raise ValueError("Expected an npm v3 lockfile")
    locked_root = lock["packages"][""]
    for key in ("name", "version"):
        if lock.get(key) != package[key] or locked_root.get(key) != package[key]:
            raise ValueError(f"Lockfile mismatch: {key}")
    for key in ("dependencies", "devDependencies", "engines"):
        if locked_root.get(key, {}) != package.get(key, {}):
            raise ValueError(f"Lockfile mismatch: {key}")
    for name in package["dependencies"] | package["devDependencies"]:
        entry = lock["packages"][f"node_modules/{name}"]
        if not entry.get("version") or not entry.get("integrity"):
            raise ValueError(f"Incomplete locked dependency: {name}")


def check_prerequisites() -> None:
    if sys.version_info[:2] != (3, 11):
        raise ValueError("CI requires Python 3.11")
    for binary, argument in (("node", "--version"), ("npm", "--version"),
                             ("ffmpeg", "-version"), ("ffprobe", "-version")):
        if shutil.which(binary) is None:
            raise ValueError(f"Missing runner prerequisite: {binary}")
        result = subprocess.run([binary, argument], check=True, capture_output=True,
                                text=True, timeout=15)
        if binary == "node" and not re.fullmatch(r"v20\.\d+\.\d+\s*", result.stdout):
            raise ValueError("CI requires Node 20")
    check_lockfile()


def junit_counts(path: Path) -> dict[str, int]:
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_JUNIT_BYTES:
        raise ValueError("JUnit missing, empty, or larger than 10 MiB")
    content = path.read_bytes()
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise ValueError("JUnit declarations are forbidden")
    root = ET.fromstring(content)
    if root.tag not in {"testsuite", "testsuites"}:
        raise ValueError("Invalid JUnit root")
    cases = list(root.iter("testcase"))
    counts = {"tests": len(cases), **{
        key: sum(case.find(tag) is not None for case in cases)
        for key, tag in (("failures", "failure"), ("errors", "error"),
                         ("skipped", "skipped"))
    }}
    if not cases:
        raise ValueError("JUnit contains no executed test cases")
    for suite in root.iter():
        if suite.tag not in {"testsuite", "testsuites"}:
            continue
        children = list(suite.iter("testcase"))
        observed = {"tests": len(children), **{
            key: sum(case.find(tag) is not None for case in children)
            for key, tag in (("failures", "failure"), ("errors", "error"),
                             ("skipped", "skipped"))
        }}
        for key, value in observed.items():
            if key in suite.attrib and int(suite.attrib[key]) != value:
                raise ValueError(f"JUnit count mismatch: {key}")
    return counts


def require_passing_junit(path: Path) -> dict[str, int]:
    counts = junit_counts(path)
    print(json.dumps(counts, sort_keys=True))
    if any(counts[key] for key in ("failures", "errors", "skipped")):
        raise ValueError("JUnit has failures, errors, or unreviewed skips")
    return counts


def assert_definitions(workflow: str, jenkins: str) -> None:
    for definition in (workflow, jenkins):
        for command in SHARED_COMMANDS:
            assert command in definition, f"Missing shared command: {command}"
        for forbidden in ("|| true", "continue-on-error", "returnStatus", "set +e",
                          "secrets.", "ssh ", "docker ", "/var/run/docker.sock",
                          "contents: write", "git push", "build job:", "curl "):
            assert forbidden not in definition, f"Forbidden CI operation: {forbidden}"
        assert "PYTHONDONTWRITEBYTECODE" in definition
        assert "AUDIO_CUE_LOCATOR_ASSET_STORAGE_ROOT" in definition
        assert "AUDIO_CUE_LOCATOR_ANALYSIS_DB_PATH" in definition
    for required in ("push:", "branches: [main]", "workflow_dispatch:",
                     "contents: read", "runs-on: ubuntu-24.04", "timeout-minutes:",
                     "ref: ${{ github.sha }}", "persist-credentials: false",
                     "python-version: '3.11'", "node-version: '20'",
                     "sudo apt-get install --yes --no-install-recommends ffmpeg",
                     "if: ${{ always() }}", "retention-days: 7",
                     "if-no-files-found: error", "path: ci-results/pytest.xml"):
        assert required in workflow, f"Missing GitHub requirement: {required}"
    assert "self-hosted" not in workflow
    actions = re.findall(r"uses: (\S+)", workflow)
    assert len(actions) == 4
    for action in actions:
        assert re.fullmatch(r"actions/(checkout|setup-python|setup-node|upload-artifact)@[0-9a-f]{40}", action)
    for required in ("agent none", "APPROVED_AGENT_LABEL", "EXPECTED_COMMIT",
                     "JENKINS_RUNTIME_BLOCKED", "checkout scm", "git rev-parse HEAD",
                     "observed != params.EXPECTED_COMMIT", "disableConcurrentBuilds()",
                     "skipDefaultCheckout(true)", "timeout(time: 30, unit: 'MINUTES')",
                     "buildDiscarder(logRotator(", "post {", "always {", "junit ",
                     "allowEmptyResults: false", "keepLongStdio: false",
                     "buildResult: 'FAILURE', stageResult: 'FAILURE'",
                     "testResults: 'ci-results/pytest.xml'", "ci-results/revision.txt"):
        assert required in jenkins, f"Missing Jenkins requirement: {required}"
    assert "debian-tests" not in jenkins
    assert re.search(r"label\s+['\"]\$\{params.APPROVED_AGENT_LABEL\}['\"]", jenkins)
    assert re.search(r"defaultValue: ''", jenkins)
    for body in re.findall(r"sh '''(.*?)'''", jenkins, flags=re.DOTALL):
        assert "params." not in body


def test_ci_definitions_and_documented_shared_contract():
    assert_definitions((ROOT / ".github/workflows/ci.yml").read_text(),
                       (ROOT / "ci/Jenkinsfile").read_text())
    docs = (ROOT / "docs/ci.md").read_text()
    for command in SHARED_COMMANDS:
        assert command in docs
    for boundary in ("npm install", "Dockerfile", "JENKINS_RUNTIME_BLOCKED",
                     "ASF_CANONICAL_CI_DISPATCH=NOT_ENABLED",
                     "ASF_CANONICAL_TEST_EVIDENCE=NOT_CLAIMED", "Playwright"):
        assert boundary in docs


def test_lockfile_matches_package():
    check_lockfile()


def test_lockfile_mismatch_and_absence_fail(tmp_path):
    import pytest

    (tmp_path / "webui").mkdir()
    shutil.copyfile(ROOT / "webui/package.json", tmp_path / "webui/package.json")
    with pytest.raises(FileNotFoundError):
        check_lockfile(tmp_path)
    lock = json.loads((ROOT / "webui/package-lock.json").read_text())
    lock["packages"][""]["dependencies"]["react"] = "0.0.0"
    (tmp_path / "webui/package-lock.json").write_text(json.dumps(lock))
    with pytest.raises(ValueError, match="Lockfile mismatch"):
        check_lockfile(tmp_path)


def test_pipeline_divergence_and_unsafe_operations_fail():
    import pytest

    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    jenkins = (ROOT / "ci/Jenkinsfile").read_text()
    for original in ("python -m pytest -q tests", "npm run build", "allowEmptyResults: false",
                     "python tests/test_ci_pipeline_contract.py prerequisites"):
        with pytest.raises(AssertionError):
            assert_definitions(workflow, jenkins.replace(original, "omitted"))
    for forbidden in ("continue-on-error", "contents: write", "git push", "|| true"):
        with pytest.raises(AssertionError):
            assert_definitions(workflow + "\n" + forbidden, jenkins)


def test_missing_or_incompatible_prerequisites_fail(monkeypatch):
    import pytest

    monkeypatch.setattr(sys, "version_info", (3, 11))
    for missing in ("node", "npm", "ffmpeg", "ffprobe"):
        monkeypatch.setattr(shutil, "which", lambda name: None if name == missing else name)
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "v20.0.0\n"))
        with pytest.raises(ValueError, match="Missing runner prerequisite"):
            check_prerequisites()
    monkeypatch.setattr(shutil, "which", lambda name: name)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "v25.0.0\n"))
    with pytest.raises(ValueError, match="Node 20"):
        check_prerequisites()
    monkeypatch.setattr(sys, "version_info", (3, 12))
    with pytest.raises(ValueError, match="Python 3.11"):
        check_prerequisites()


def test_invalid_empty_failed_or_skipped_junit_fails(tmp_path):
    import pytest

    report = tmp_path / "report.xml"
    with pytest.raises(ValueError, match="JUnit missing"):
        require_passing_junit(report)
    for content in ("", "<broken", "<wrong/>", "<testsuite tests='0'/>",
                    "<testsuite tests='2'><testcase/></testsuite>",
                    "<testsuite><testcase><skipped/></testcase></testsuite>",
                    "<testsuite><testcase><error/></testcase></testsuite>",
                    "<testsuite><testcase><failure/></testcase></testsuite>"):
        report.write_text(content)
        with pytest.raises((ValueError, ET.ParseError)):
            require_passing_junit(report)
    report.write_text("<testsuite tests='1'><testcase name='real'/></testsuite>")
    assert require_passing_junit(report)["tests"] == 1


def test_real_pytest_failure_is_nonzero_and_preserved_in_junit(tmp_path):
    import pytest

    failing_test = tmp_path / "test_deliberate_failure.py"
    failing_test.write_text("def test_deliberate_failure():\n    assert 1 == 2\n")
    report = tmp_path / "failure.xml"
    outcome = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         str(failing_test), f"--junitxml={report}"],
        cwd=tmp_path, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True, text=True, timeout=60,
    )
    assert outcome.returncode == 1
    assert "assert 1 == 2" in outcome.stdout
    assert junit_counts(report) == {"tests": 1, "failures": 1, "errors": 0, "skipped": 0}
    with pytest.raises(ValueError, match="failures"):
        require_passing_junit(report)


def test_real_frontend_build_failure_is_nonzero(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({
        "private": True,
        "scripts": {"build": "node -e \"throw new Error('deliberate CI contract failure')\""},
    }))
    outcome = subprocess.run(
        ["npm", "--cache", str(tmp_path / "npm-cache"), "--logs-max=0", "run", "build"],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert outcome.returncode != 0
    assert "deliberate CI contract failure" in outcome.stderr


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["prerequisites"]:
            check_prerequisites()
        elif len(sys.argv) == 3 and sys.argv[1] in {"junit", "junit-structure"}:
            if sys.argv[1] == "junit-structure":
                print(json.dumps(junit_counts(Path(sys.argv[2])), sort_keys=True))
            else:
                require_passing_junit(Path(sys.argv[2]))
        else:
            raise ValueError("Usage: test_ci_pipeline_contract.py prerequisites|junit-structure|junit [report.xml]")
    except (ValueError, OSError, KeyError, ET.ParseError, subprocess.SubprocessError) as error:
        print(f"CI_CONFIGURATION_OR_TEST_FAILURE: {error}", file=sys.stderr)
        raise SystemExit(1)
