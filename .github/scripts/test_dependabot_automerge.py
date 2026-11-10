"""Test Mergify policy bindings and the trusted cleanup shell."""

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar, TypedDict, cast

import yaml
from typing_extensions import override

ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / "workflows" / "dependabot-automerge.yml"
MERGIFY = ROOT.parent / ".mergify.yml"
REPO = "paulnsorensen/skillz-that-grillz"

GH_SHIM = """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

state = Path(os.environ["GH_SHIM_STATE"])
items = json.loads(state.read_text())
entry = items.pop(0)
state.write_text(json.dumps(items))
if sys.argv[1:] != entry["argv"]:
    sys.exit(f"unexpected gh call: {sys.argv[1:]} != {entry['argv']}")
print(entry.get("stdout", ""), end="")
sys.exit(entry.get("exit", 0))
"""


class GhCall(TypedDict):
    argv: list[str]
    stdout: str
    exit: int


def call(argv: list[str], stdout: str = "", exit: int = 0) -> GhCall:
    return {"argv": argv, "stdout": stdout, "exit": exit}


def view(number: str, state: str) -> GhCall:
    return call(["pr", "view", number, "--repo", REPO, "--json",
                 "autoMergeRequest", "--jq", ".autoMergeRequest != null"], state + "\n")


class DependabotAutoMergeTest(unittest.TestCase):
    workflow: ClassVar[dict[str | bool, object]]
    config: ClassVar[dict[str, object]]
    policy: ClassVar[list[str]]

    @classmethod
    @override
    def setUpClass(cls) -> None:
        cls.workflow = cast(dict[str | bool, object], yaml.safe_load(WORKFLOW.read_text()))
        cls.config = cast(dict[str, object], yaml.safe_load(MERGIFY.read_text()))
        cls.policy = cast(list[str], cast(dict[str, object], cls.config["shared"])["dependabot_policy"])

    def test_native_queue_rechecks_only_bot_policy_and_keeps_human_path(self) -> None:
        queue = cast(list[dict[str, object]], self.config["queue_rules"])
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["name"], "default")
        self.assertEqual(queue[0]["batch_size"], 1)
        self.assertEqual(queue[0]["batch_max_wait_time"], "30s")
        self.assertNotIn("allow_inplace_checks", queue[0])
        for key in ("queue_conditions", "merge_conditions"):
            conditions = cast(list[object], queue[0][key])
            self.assertEqual(conditions[0], "base = main")
            branches = cast(list[object], cast(dict[str, object], conditions[1])["or"])
            self.assertEqual(branches[0], "author != dependabot[bot]")
            self.assertIs(cast(dict[str, object], branches[1])["and"], self.policy)
        protections = cast(dict[str, object], self.config["merge_protections_settings"])
        self.assertIs(protections["auto_merge_conditions"], self.policy)
        self.assertEqual(set(self.config), {"shared", "queue_rules", "merge_protections_settings"})

    def test_policy_uses_immutable_commit_identity_and_complete_metadata(self) -> None:
        for condition in (
            "author = dependabot[bot]", "base = main", "-from-fork",
            f"head-repo-full-name = {REPO}", "#commits = 1",
            "commits[*].author = dependabot[bot]",
            "commits[*].email_author = 49699333+dependabot[bot]@users.noreply.github.com",
            "commits[*].commit_verification_verified",
            "#dependabot-dependency-name > 0", "#dependabot-dependency-type > 0",
            "#dependabot-update-type > 0",
            "dependabot-update-type != version-update:semver-major",
        ):
            self.assertIn(condition, self.policy)
        self.assertFalse(any("label" in item or "title" in item or "body" in item
                             for item in self.policy))

    def test_native_message_patterns_reject_unsafe_metadata(self) -> None:
        patterns = [item.split(" ~= ", 1) for item in self.policy if " ~= " in item]

        def accepted(message: str) -> bool:
            return all(
                bool(re.search(pattern, message)) != attribute.startswith("-")
                for attribute, pattern in patterns
                if attribute.endswith("commit_message")
            )

        def message(old: str = "6.170.13", new: str = "6.170.14",
                    update: str = "patch", name: str = "hypothesis",
                    group: str = "") -> str:
            return (f"chore(deps-dev): bump {name}\n\n"
                    f"Updates `{name}` from {old} to {new}\n\n"
                    "---\nupdated-dependencies:\n"
                    f"- dependency-name: {name}\n  dependency-version: {new}\n"
                    "  dependency-type: direct:development\n"
                    f"  update-type: version-update:semver-{update}\n"
                    f"{group}...\n")

        self.assertTrue(accepted(message()))
        self.assertTrue(accepted(message("2.31.0", "2.32.0", "minor")))
        self.assertTrue(accepted(message("0.9.0", "0.9.1")))
        self.assertTrue(accepted(message(group="  dependency-group: uv-minor-and-patch\n")))
        mixed = message("0.9.0", "0.9.1").replace(
            "---\nupdated-dependencies:",
            "Updates `other` from 1.2.0 to 1.3.0\n\n---\nupdated-dependencies:",
        ).replace(
            "...\n",
            "- dependency-name: other\n  dependency-version: 1.3.0\n"
            + "  dependency-type: direct:development\n"
            + "  update-type: version-update:semver-minor\n...\n",
        )
        self.assertTrue(accepted(mixed))
        for unsafe in (
            message("0.9.0", "0.10.0", "minor"),
            message("unknown", "6.170.14"),
            message("6.170.13", "unknown"),
            message("6.170.13", "6.170.14", "major"),
            message().replace("  dependency-version:", "  missing-version:"),
            message().replace("  dependency-type:", "  missing-type:"),
        ):
            with self.subTest(unsafe=unsafe):
                self.assertFalse(accepted(unsafe))
        self.assertTrue(any("dependabot/fetch-metadata" in item for item in self.policy))
        self.assertTrue(any("rust|node|nodejs|python|go|golang" in item for item in self.policy))

    def test_workflow_has_only_trusted_cleanup_trigger_and_permissions(self) -> None:
        triggers = cast(dict[str, object], self.workflow.get("on", self.workflow.get(True)))
        self.assertEqual(triggers["workflow_run"],
                         {"workflows": ["validate"], "types": ["completed"]})
        self.assertEqual(triggers["schedule"], [{"cron": "17 * * * *"}])
        self.assertIn("workflow_dispatch", triggers)
        self.assertEqual(self.workflow["permissions"], {})
        self.assertFalse(cast(dict[str, object], self.workflow["concurrency"])["cancel-in-progress"])
        jobs = cast(dict[str, object], self.workflow["jobs"])
        job = cast(dict[str, object], jobs["cleanup"])
        self.assertEqual(job["permissions"], {"pull-requests": "write"})
        steps = cast(list[object], job["steps"])
        self.assertEqual(len(steps), 1)
        step = cast(dict[str, object], steps[0])
        self.assertEqual(step["env"],
                         {"GH_TOKEN": "${{ secrets.GITHUB_TOKEN }}",
                          "GH_REPO": "${{ github.repository }}"})
        self.assertEqual(step["shell"], "bash")
        self.assertNotIn("uses:", WORKFLOW.read_text())
        run = cast(str, step["run"])
        self.assertNotIn("--auto", run)
        self.assertNotIn("--squash", run)
        self.assertNotIn("statuses:", run)

    def run_shell(self, calls: list[GhCall]) -> tuple[subprocess.CompletedProcess[str], list[GhCall]]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shim = root / "gh"
            _ = shim.write_text(GH_SHIM)
            _ = shim.chmod(0o755)
            state = root / "calls.json"
            _ = state.write_text(json.dumps(calls))
            env = os.environ | {
                "PATH": f"{root}:{os.environ['PATH']}",
                "GH_SHIM_STATE": str(state),
                "GH_TOKEN": "test-token",
                "GH_REPO": REPO,
            }
            jobs = cast(dict[str, object], self.workflow["jobs"])
            job = cast(dict[str, object], jobs["cleanup"])
            step = cast(dict[str, object], cast(list[object], job["steps"])[0])
            run = cast(str, step["run"])
            result = subprocess.run(["bash", "-c", run], env=env,
                                    capture_output=True, text=True, check=False)
            return result, cast(list[GhCall], json.loads(state.read_text()))

    def test_shell_disables_all_armed_bot_prs_and_verifies_each(self) -> None:
        listing = call(["api", "--paginate",
                        f"repos/{REPO}/pulls?state=open&per_page=100",
                        "--jq", '.[] | select(.user.login == "dependabot[bot]") | .number'],
                       "1\n2\n")
        result, remaining = self.run_shell([
            listing, view("1", "true"),
            call(["pr", "merge", "1", "--repo", REPO, "--disable-auto"]),
            view("1", "false"), view("2", "false"),
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(remaining, [])

    def test_shell_continues_after_failed_disarm_and_exits_nonzero(self) -> None:
        listing = call(["api", "--paginate",
                        f"repos/{REPO}/pulls?state=open&per_page=100",
                        "--jq", '.[] | select(.user.login == "dependabot[bot]") | .number'],
                       "1\n2\n")
        result, remaining = self.run_shell([
            listing, view("1", "true"),
            call(["pr", "merge", "1", "--repo", REPO, "--disable-auto"], exit=1),
            view("2", "true"),
            call(["pr", "merge", "2", "--repo", REPO, "--disable-auto"]),
            view("2", "false"),
        ])
        self.assertEqual(result.returncode, 1)
        self.assertIn("PR 1: could not disable auto-merge", result.stderr)
        self.assertEqual(remaining, [])

    def test_shell_fails_if_readback_remains_armed(self) -> None:
        listing = call(["api", "--paginate",
                        f"repos/{REPO}/pulls?state=open&per_page=100",
                        "--jq", '.[] | select(.user.login == "dependabot[bot]") | .number'],
                       "1\n")
        result, remaining = self.run_shell([
            listing, view("1", "true"),
            call(["pr", "merge", "1", "--repo", REPO, "--disable-auto"]),
            view("1", "true"),
        ])
        self.assertEqual(result.returncode, 1)
        self.assertIn("auto-merge remains armed", result.stderr)
        self.assertEqual(remaining, [])


    def test_shell_fails_if_listing_fails(self) -> None:
        listing = call(["api", "--paginate",
                        f"repos/{REPO}/pulls?state=open&per_page=100",
                        "--jq", '.[] | select(.user.login == "dependabot[bot]") | .number'],
                       exit=1)
        result, remaining = self.run_shell([listing])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(remaining, [])

    def test_shell_succeeds_if_no_bot_prs_are_open(self) -> None:
        listing = call(["api", "--paginate",
                        f"repos/{REPO}/pulls?state=open&per_page=100",
                        "--jq", '.[] | select(.user.login == "dependabot[bot]") | .number'])
        result, remaining = self.run_shell([listing])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(remaining, [])

    def test_shell_continues_after_state_read_failure(self) -> None:
        listing = call(["api", "--paginate",
                        f"repos/{REPO}/pulls?state=open&per_page=100",
                        "--jq", '.[] | select(.user.login == "dependabot[bot]") | .number'],
                       "1\n2\n")
        bad_view = view("1", "true")
        bad_view["exit"] = 1
        result, remaining = self.run_shell([
            listing, bad_view, view("2", "true"),
            call(["pr", "merge", "2", "--repo", REPO, "--disable-auto"]),
            view("2", "false"),
        ])
        self.assertEqual(result.returncode, 1)
        self.assertIn("PR 1: could not read auto-merge state", result.stderr)
        self.assertEqual(remaining, [])

    def test_shell_rejects_malformed_auto_merge_state(self) -> None:
        listing = call(["api", "--paginate",
                        f"repos/{REPO}/pulls?state=open&per_page=100",
                        "--jq", '.[] | select(.user.login == "dependabot[bot]") | .number'],
                       "1\n2\n")
        result, remaining = self.run_shell([
            listing, view("1", "unknown"), view("2", "true"),
            call(["pr", "merge", "2", "--repo", REPO, "--disable-auto"]),
            view("2", "false"),
        ])
        self.assertEqual(result.returncode, 1)
        self.assertIn("PR 1: invalid auto-merge state", result.stderr)
        self.assertEqual(remaining, [])

    def test_shell_fails_if_post_disable_read_fails(self) -> None:
        listing = call(["api", "--paginate",
                        f"repos/{REPO}/pulls?state=open&per_page=100",
                        "--jq", '.[] | select(.user.login == "dependabot[bot]") | .number'],
                       "1\n2\n")
        bad_view = view("1", "false")
        bad_view["exit"] = 1
        result, remaining = self.run_shell([
            listing, view("1", "true"),
            call(["pr", "merge", "1", "--repo", REPO, "--disable-auto"]),
            bad_view, view("2", "false"),
        ])
        self.assertEqual(result.returncode, 1)
        self.assertIn("PR 1: could not verify auto-merge state", result.stderr)
        self.assertEqual(remaining, [])

if __name__ == "__main__":
    _ = unittest.main()