import json
import tempfile
import unittest
from pathlib import Path

from eval_harness.trajectory.environment import (
    create_seed_state,
    run_mock_attempt,
    sleep_for,
    state_view,
)
from eval_harness.trajectory.evaluator import evaluate_task
from eval_harness.trajectory.events import read_events
from eval_harness.trajectory.report import write_report
from eval_harness.trajectory.runner import (
    ProcessTimeoutError,
    load_experiment,
    run_attempt,
    run_experiment,
    run_in_process,
)


ROOT = Path(__file__).parents[1]
EXPERIMENT = ROOT / "experiments/001.json"


def _sleep_attempt(_public_task, _arm, _initial_state):
    sleep_for(0.2)


def _fail_attempt(_public_task, _arm, _initial_state):
    raise RuntimeError("deliberate child failure")


def _malformed_attempt(_public_task, _arm, _initial_state):
    return {"answer": "missing required fields"}


def _large_result():
    return "x" * 2_000_000


class TrajectoryP5Tests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="trajectory-p5-")
        self.temp_path = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def _small_experiment(self) -> Path:
        value = json.loads(EXPERIMENT.read_text(encoding="utf-8"))
        value["repetitions"] = 1
        value["tasks"] = [
            str((ROOT / task).resolve())
            for task in (
                "tasks/agent-eval/catalog-01.json",
                "tasks/agent-eval/cart-01.json",
                "tasks/agent-eval/shipping-01.json",
                "tasks/agent-eval/stock-01.json",
            )
        ]
        path = self.temp_path / "experiment.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_mock_matrix_writes_valid_attempts_and_refuses_existing_output(self):
        experiment = self._small_experiment()
        output = self.temp_path / "run"
        manifest, results = run_experiment(experiment, output, mock=True)

        self.assertEqual(manifest["scheduled_attempts"], 8)
        self.assertEqual(len(results), 8)
        self.assertTrue(all(result["verdict"] == "pass" for result in results))
        self.assertEqual(len(list((output / "attempts").glob("*/events.jsonl"))), 8)
        for journal_path in (output / "attempts").glob("*/events.jsonl"):
            events = read_events(journal_path)
            self.assertEqual(events[0]["kind"], "run_started")
            self.assertEqual(events[-1]["kind"], "run_finished")
            self.assertTrue(any(event["kind"] == "evaluator_result" for event in events))
        self.assertTrue((output / "report.md").exists())
        with self.assertRaises(FileExistsError):
            run_experiment(experiment, output, mock=True)

    def test_evaluator_is_outside_the_public_child_contract(self):
        _experiment, tasks = load_experiment(EXPERIMENT)
        public = {
            "id": tasks[0]["id"],
            "kind": tasks[0]["kind"],
            "prompt": tasks[0]["prompt"],
            "inputs": tasks[0]["inputs"],
        }
        with self.assertRaisesRegex(ValueError, "evaluator data"):
            run_mock_attempt({**public, "expected": "hidden"}, "dom", create_seed_state())

        cart_task = next(task for task in tasks if task["id"] == "cart-01")
        initial = state_view(cart_task["initial_state"])
        wrong = evaluate_task(
            cart_task,
            initial,
            initial,
            "The cart now has two Blue Mugs and no USB-C Cable.",
        )
        self.assertEqual(wrong.verdict, "fail")

        catalog_task = next(task for task in tasks if task["id"] == "catalog-01")
        contradictory = evaluate_task(
            catalog_task,
            state_view(catalog_task["initial_state"]),
            state_view(catalog_task["initial_state"]),
            "Blue Mug is not the cheapest in-stock blue mug below $25.",
        )
        self.assertEqual(contradictory.verdict, "fail")

        contradictory_answers = {
            "cart-01": "There are not two Blue Mugs and the USB-C Cable was not removed.",
            "shipping-01": "The details were saved, but not correctly.",
            "stock-01": "Stock is sufficient for 99 Blue Mugs.",
        }
        for task_id, answer in contradictory_answers.items():
            task = next(item for item in tasks if item["id"] == task_id)
            public_task = {
                "id": task["id"],
                "kind": task["kind"],
                "prompt": task["prompt"],
                "inputs": task["inputs"],
            }
            attempt = run_mock_attempt(
                public_task,
                "webmcp",
                task["initial_state"],
            )
            evaluation = evaluate_task(
                task,
                state_view(task["initial_state"]),
                attempt["final_state"],
                answer,
                attempt["trace"],
            )
            self.assertEqual(evaluation.verdict, "fail", task_id)

        paraphrase_answers = {
            "catalog-01": "Blue Mug is the least expensive blue mug that is available.",
            "cart-01": "I set Blue Mug to 2 and removed the USB-C Cable from the cart.",
            "shipping-01": (
                "Shipping details for Alex Example at 42 Test Lane were saved with the "
                "note: Leave at the fictional desk."
            ),
            "stock-01": (
                "I couldn't add the requested quantity because only limited stock is "
                "available."
            ),
        }
        for task_id, answer in paraphrase_answers.items():
            task = next(item for item in tasks if item["id"] == task_id)
            public_task = {
                "id": task["id"],
                "kind": task["kind"],
                "prompt": task["prompt"],
                "inputs": task["inputs"],
            }
            attempt = run_mock_attempt(public_task, "webmcp", task["initial_state"])
            evaluation = evaluate_task(
                task,
                state_view(task["initial_state"]),
                attempt["final_state"],
                answer,
                attempt["trace"],
            )
            self.assertEqual(evaluation.verdict, "pass", task_id)

        stock_task = next(item for item in tasks if item["id"] == "stock-01")
        no_operation = evaluate_task(
            stock_task,
            state_view(stock_task["initial_state"]),
            state_view(stock_task["initial_state"]),
            "I could not set that quantity because there is insufficient stock.",
            trace=[],
        )
        self.assertEqual(no_operation.verdict, "fail")

        dom_visible_error = evaluate_task(
            stock_task,
            state_view(stock_task["initial_state"]),
            state_view(stock_task["initial_state"]),
            "Stock failure: only 4 units are available.",
            trace=[
                {
                    "type": "tool_result",
                    "name": "snapshot",
                    "status": "ok",
                    "result": {"elements": [{"text": "Only 4 units are available"}]},
                }
            ],
        )
        self.assertEqual(dom_visible_error.verdict, "pass")

    def test_both_non_browser_arms_use_the_same_seeded_application_rules(self):
        _experiment, tasks = load_experiment(EXPERIMENT)
        task = next(task for task in tasks if task["id"] == "cart-01")
        public = {
            "id": task["id"],
            "kind": task["kind"],
            "prompt": task["prompt"],
            "inputs": task["inputs"],
        }
        dom = run_mock_attempt(public, "dom", task["initial_state"])
        webmcp = run_mock_attempt(public, "webmcp", task["initial_state"])
        self.assertEqual(dom["final_state"], webmcp["final_state"])
        self.assertEqual(dom["answer"], webmcp["answer"])

    def test_process_timeout_is_hard_and_intervention_changes_autonomy(self):
        with self.assertRaises(ProcessTimeoutError):
            run_in_process(sleep_for, 0.2, timeout_seconds=0.01)
        self.assertEqual(len(run_in_process(_large_result, timeout_seconds=2)), 2_000_000)

        _experiment, tasks = load_experiment(EXPERIMENT)
        task = tasks[0]
        schedule_item = {
            "attempt_index": 1,
            "attempt_id": "attempt-intervention",
            "task_id": task["id"],
            "arm": "dom",
            "repetition": 1,
            "pair_order": 1,
        }
        result = run_attempt(
            task=task,
            schedule_item=schedule_item,
            output_dir=self.temp_path / "intervention-attempt",
            timeout_seconds=2,
            intervention={"action": "clarify", "reason": "test assistance"},
        )
        self.assertEqual(result["autonomy"], "assisted")
        events = read_events(self.temp_path / "intervention-attempt/events.jsonl")
        self.assertTrue(any(event["kind"] == "intervention" for event in events))

        timeout_dir = self.temp_path / "timeout-attempt"
        timeout_result = run_attempt(
            task=task,
            schedule_item={**schedule_item, "attempt_id": "attempt-timeout"},
            output_dir=timeout_dir,
            timeout_seconds=0.01,
            attempt_function=_sleep_attempt,
        )
        self.assertEqual(timeout_result["outcome"], "timeout")
        timeout_evaluation = json.loads((timeout_dir / "evaluation.json").read_text())
        self.assertEqual(timeout_evaluation["status"], "not_run")
        self.assertFalse(json.loads((timeout_dir / "answer.json").read_text())["available"])
        self.assertEqual(
            read_events(timeout_dir / "events.jsonl")[-1]["kind"],
            "run_finished",
        )

        error_dir = self.temp_path / "error-attempt"
        error_result = run_attempt(
            task=task,
            schedule_item={**schedule_item, "attempt_id": "attempt-error"},
            output_dir=error_dir,
            timeout_seconds=2,
            attempt_function=_fail_attempt,
        )
        self.assertEqual(error_result["outcome"], "infrastructure_failure")
        error_evaluation = json.loads((error_dir / "evaluation.json").read_text())
        self.assertEqual(error_evaluation["status"], "not_run")
        self.assertEqual(read_events(error_dir / "events.jsonl")[-1]["kind"], "run_finished")

        malformed_dir = self.temp_path / "malformed-attempt"
        malformed_result = run_attempt(
            task=task,
            schedule_item={**schedule_item, "attempt_id": "attempt-malformed"},
            output_dir=malformed_dir,
            timeout_seconds=2,
            attempt_function=_malformed_attempt,
        )
        self.assertEqual(malformed_result["outcome"], "infrastructure_failure")
        self.assertTrue((malformed_dir / "answer.json").exists())
        self.assertTrue((malformed_dir / "evaluation.json").exists())
        self.assertEqual(read_events(malformed_dir / "events.jsonl")[-1]["kind"], "run_finished")

    def test_report_separates_autonomy_and_shows_incomplete_pair_denominator(self):
        manifest = {
            "experiment_id": "report-test",
            "mode": "mock",
            "model": "gpt-5.6-luna",
            "environment": "shop-fixture-v1",
            "scheduled_attempts": 4,
            "randomization_seed": 1,
            "repetitions": 2,
            "tasks": [{"id": "catalog-01", "prompt": "test"}],
            "summary": {"outcomes": {"pass": 3, "timeout": 1}},
        }
        common = {
            "task_id": "catalog-01",
            "model_requests": 1,
            "tool_invocations": 1,
        }
        results = [
            {
                **common,
                "arm": "dom",
                "repetition": 1,
                "verdict": "pass",
                "autonomy": "autonomous",
                "elapsed_ms": 1,
            },
            {
                **common,
                "arm": "webmcp",
                "repetition": 1,
                "verdict": "pass",
                "autonomy": "assisted",
                "elapsed_ms": 2,
            },
            {
                **common,
                "arm": "dom",
                "repetition": 2,
                "verdict": "pass",
                "autonomy": "autonomous",
                "elapsed_ms": 1,
            },
            {
                **common,
                "arm": "webmcp",
                "repetition": 2,
                "verdict": None,
                "autonomy": "autonomous",
                "elapsed_ms": None,
            },
        ]
        report_path = self.temp_path / "report.md"
        write_report(report_path, manifest, results)
        report = report_path.read_text(encoding="utf-8")
        self.assertIn("Autonomous pass", report)
        self.assertIn("Assisted pass", report)
        self.assertIn("1 / 2", report)


if __name__ == "__main__":
    unittest.main()
