"""Exercise the Dependabot auto-merge workflow's real shell steps."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar, cast
from typing_extensions import override

import yaml

WORKFLOW = Path(__file__).parents[1] / "workflows" / "dependabot-automerge.yml"
PR_URL = "https://github.com/example/repo/pull/1"


class DependabotAutomergeTest(unittest.TestCase):
    steps: ClassVar[list[dict[str, object]]]

    @classmethod
    @override
    def setUpClass(cls) -> None:
        workflow = cast(dict[str, object], yaml.safe_load(WORKFLOW.read_text()))
        jobs = cast(dict[str, object], workflow["jobs"])
        arm = cast(dict[str, object], jobs["arm"])
        cls.steps = cast(list[dict[str, object]], arm["steps"])

    def _run_step(
        self, step_name: str, *, dependencies: list[dict[str, str]] | None = None,
        armed: bool = False, other_authors: int = 0,
    ) -> tuple[str, list[str]]:
        step = next(step for step in self.steps if step.get("name") == step_name)
        script = cast(str, step["run"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gh = root / "gh"
            _ = gh.write_text("\n".join((
                "#!/bin/sh",
                "printf '%s\\n' \"$*\" >> \"$GH_LOG\"",
                "case \"$*\" in",
                "  *commits*) printf '%s\\n' \"$OTHER_AUTHORS\" ;;",
                "  *autoMergeRequest*) printf '%s\\n' \"$ARMED\" ;;",
                "esac",
                "",
            )))
            gh.chmod(0o755)
            output = root / "output"
            log = root / "gh.log"
            result = subprocess.run(
                ["bash", "-e", "-o", "pipefail", "-c", script],
                capture_output=True, text=True, check=False,
                env={
                    **os.environ,
                    "PATH": f"{root}:{os.environ['PATH']}",
                    "GH_LOG": str(log),
                    "OTHER_AUTHORS": str(other_authors),
                    "ARMED": str(armed).lower(),
                    "GITHUB_OUTPUT": str(output),
                    "DEPS": json.dumps(dependencies or []),
                    "PR_URL": PR_URL,
                },
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return output.read_text() if output.exists() else "", log.read_text().splitlines()

    def test_metadata_action_stays_with_a_human_even_in_mixed_groups(self) -> None:
        normal = {
            "dependencyName": "requests", "prevVersion": "2.31.0",
            "newVersion": "2.32.0", "updateType": "version-update:semver-minor",
        }
        for update in ("version-update:semver-patch", "version-update:semver-minor"):
            with self.subTest(update=update):
                action = {
                    "dependencyName": "dependabot/fetch-metadata", "prevVersion": "3.1.0",
                    "newVersion": "3.1.1", "updateType": update,
                }
                for group in ([action], [normal, action]):
                    with self.subTest(group=group):
                        output, _ = self._run_step("Check bump policy", dependencies=group)
                        self.assertEqual(output, "arm=false\n")

    def test_supported_bumps_stay_eligible_and_other_rejections_remain(self) -> None:
        def bump(name: str, previous: str, update: str) -> dict[str, str]:
            return {
                "dependencyName": name, "prevVersion": previous,
                "newVersion": "9.9.9", "updateType": update,
            }

        patch = "version-update:semver-patch"
        minor = "version-update:semver-minor"
        for dependencies, expected in (
            ([bump("requests", "2.0.0", patch)], "arm=true\n"),
            ([bump("requests", "2.0.0", minor)], "arm=true\n"),
            ([bump("requests", "2.0.0", patch), bump("pyyaml", "6.0.0", minor)], "arm=true\n"),
            ([bump("requests", "0.9.0", minor)], "arm=false\n"),
            ([bump("python", "3.11.0", patch)], "arm=false\n"),
            ([bump("requests", "2.0.0", "version-update:semver-major")], "arm=false\n"),
            ([bump("requests", "", patch)], "arm=false\n"),
            ([], "arm=false\n"),
        ):
            with self.subTest(dependencies=dependencies):
                output, _ = self._run_step("Check bump policy", dependencies=dependencies)
                self.assertEqual(output, expected)
        output, _ = self._run_step(
            "Check bump policy", dependencies=[bump("requests", "2.0.0", patch)],
            other_authors=1,
        )
        self.assertEqual(output, "arm=false\n")

    def test_cleanup_runs_after_failure_only_when_policy_did_not_arm(self) -> None:
        step = next(step for step in self.steps if step.get("name") == "Disable auto-merge")
        self.assertEqual(step["if"], "${{ !cancelled() && steps.policy.outputs.arm != 'true' }}")
        _, commands = self._run_step("Disable auto-merge", armed=True)
        self.assertEqual(commands, [
            f"pr view {PR_URL} --json autoMergeRequest --jq .autoMergeRequest != null",
            f"pr merge --disable-auto {PR_URL}",
        ])
        _, commands = self._run_step("Disable auto-merge", armed=False)
        self.assertEqual(commands, [
            f"pr view {PR_URL} --json autoMergeRequest --jq .autoMergeRequest != null",
        ])

    def test_default_branch_source_is_documented_without_premature_activation(self) -> None:
        header = WORKFLOW.read_text().split("name:", 1)[0]
        self.assertIn("pull_request_target runs this file from dist", header)
        self.assertIn("after normal publication", header)


if __name__ == "__main__":
    _ = unittest.main()
