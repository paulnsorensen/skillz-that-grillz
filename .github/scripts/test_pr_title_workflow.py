"""Exercise the PR-title job against pull request event payloads."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar, cast
from typing_extensions import override

import yaml


WORKFLOWS = Path(__file__).parents[1] / "workflows"
WORKFLOW = WORKFLOWS / "pr-title.yml"
REPOSITORY = "paulnsorensen/skillz-that-grillz"


class PrTitleWorkflowTest(unittest.TestCase):
    workflow: ClassVar[dict[object, object]]
    job: ClassVar[dict[str, object]]
    steps: ClassVar[list[dict[str, object]]]

    @classmethod
    @override
    def setUpClass(cls) -> None:
        cls.workflow = cast(dict[object, object], yaml.safe_load(WORKFLOW.read_text()))
        jobs = cast(dict[str, object], cls.workflow["jobs"])
        cls.job = cast(dict[str, object], jobs["pr-title"])
        cls.steps = cast(list[dict[str, object]], cls.job["steps"])

    @staticmethod
    def _event() -> dict[str, dict[str, object]]:
        return {
            "pull_request": {
                "draft": True,
                "title": "merge queue: checking #595 on main (37905a7)",
                "user": {"login": "mergify[bot]", "type": "Bot"},
                "head": {
                    "repo": {"full_name": REPOSITORY},
                    "ref": "mergify/merge-queue/a666e4a325",
                },
            },
            "sender": {"login": "maintainer"},
        }

    def _classify(self, event: dict[str, dict[str, object]]) -> str:
        step = next(step for step in self.steps if step.get("id") == "queue")
        script = cast(str, step["run"])
        with tempfile.TemporaryDirectory() as directory:
            event_path = Path(directory) / "event.json"
            output_path = Path(directory) / "output"
            _ = event_path.write_text(json.dumps(event))
            result = subprocess.run(
                ["bash", "-e", "-o", "pipefail", "-c", script],
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "GITHUB_EVENT_PATH": str(event_path),
                    "GITHUB_OUTPUT": str(output_path),
                    "GITHUB_REPOSITORY": REPOSITORY,
                },
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return output_path.read_text()

    def test_only_mergify_owned_queue_draft_skips_title_check(self) -> None:
        self.assertEqual(self._classify(self._event()), "queue_draft=true\n")
        for change in (
            {"draft": False},
            {"draft": None},
            {"user": {"login": "dependabot[bot]", "type": "Bot"}},
            {"user": {"login": "mergify[bot]", "type": "User"}},
            {"head": {"repo": {"full_name": "attacker/fork"}, "ref": "mergify/merge-queue/main/pr-595"}},
            {"head": {"repo": {"full_name": REPOSITORY}, "ref": "feature/merge-queue-main"}},
        ):
            with self.subTest(change=change):
                event = self._event()
                event["pull_request"].update(change)
                self.assertEqual(self._classify(event), "queue_draft=false\n")
        for path in (
            ("draft",),
            ("user", "login"),
            ("user", "type"),
            ("head", "repo", "full_name"),
            ("head", "ref"),
        ):
            with self.subTest(missing=path):
                event = self._event()
                node = event["pull_request"]
                for key in path[:-1]:
                    node = cast(dict[str, object], node[key])
                del node[path[-1]]
                self.assertEqual(self._classify(event), "queue_draft=false\n")

    def test_dependabot_and_human_titles_are_checked(self) -> None:
        for login, title in (
            ("dependabot[bot]", "chore(deps-dev): update hatchling requirement from >=1.32.4 to >=1.33.0"),
            ("maintainer", "fix: correct title handling"),
        ):
            with self.subTest(login=login):
                event = self._event()
                event["pull_request"]["title"] = title
                event["pull_request"]["user"] = {"login": login, "type": "Bot" if login.endswith("[bot]") else "User"}
                self.assertEqual(self._classify(event), "queue_draft=false\n")

    def test_job_wiring_preserves_required_check_and_other_jobs(self) -> None:
        self.assertEqual(self.job["name"], "conventional commits pr title")
        self.assertEqual(self.job["permissions"], {"pull-requests": "read"})
        action = next(step for step in self.steps if step.get("name") == "Validate PR title")
        self.assertEqual(action["if"], "steps.queue.outputs.queue_draft != 'true'")
        self.assertEqual(
            action["uses"],
            "amannn/action-semantic-pull-request@48f256284bd46cdaab1048c3721360e808335d50",
        )
        triggers = cast(dict[str, object], self.workflow[True])
        pr = cast(dict[str, object], triggers["pull_request"])
        self.assertEqual(
            pr["types"],
            ["opened", "synchronize", "reopened", "edited", "ready_for_review", "converted_to_draft"],
        )
        self.assertEqual(set(cast(dict[str, object], self.workflow["jobs"])), {"pr-title"})
        validate = cast(dict[object, object], yaml.safe_load((WORKFLOWS / "validate.yml").read_text()))
        self.assertEqual(validate[True], {"pull_request": None, "push": {"branches": ["main"]}})
        jobs = cast(dict[str, dict[str, object]], validate["jobs"])
        self.assertNotIn("pr-title", jobs)
        for name, job in jobs.items():
            with self.subTest(job=name):
                self.assertNotIn("queue", str(job.get("if", "")))


if __name__ == "__main__":
    _ = unittest.main()
