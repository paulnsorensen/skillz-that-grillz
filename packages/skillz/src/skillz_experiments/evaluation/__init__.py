"""The evaluation slice: score one candidate on one case. Import only this barrel from outside the slice."""
from skillz_experiments._evaluation import CheckResult
from skillz_experiments.evaluation._evaluator import Reply, Transcript, answer_schema, evaluate, task_tokens
from skillz_experiments.evaluation._memo import CheckMemo

__all__ = ["CheckMemo", "CheckResult", "Reply", "Transcript", "answer_schema", "evaluate", "task_tokens"]
