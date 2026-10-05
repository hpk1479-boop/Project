"""A timed MA expression must declare its replay dependencies precisely."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Part1" / "program"))

from event_selection import resolve
from event_watch_selection import specialize
from watch_orchestrator import ChainTriggerSpec


class TimedChainMAExpressionSelectionTest(unittest.TestCase):
    def test_ma_expression_uses_watch_and_ma_without_fallback(self):
        trigger = ChainTriggerSpec(
            "MA_EXPRESSION", "1m", ma_expression="골든크로스(WMA17,SMA20)"
        )
        trigger.validate()
        plan = resolve(["WATCH"], {})
        chain = SimpleNamespace(
            final_action="OZ", triggers=(trigger,), invalidation_triggers=()
        )
        manager = SimpleNamespace(
            _all_specs_locked=lambda: (),
            timed_chains={"chain": chain},
            fvg_created_watches={},
            _desired_subscriptions_locked=lambda: {},
        )
        kernel = SimpleNamespace(manager=manager, selection=plan)
        engine = SimpleNamespace(
            selection=plan,
            strategy_state={"COMPOSER": {"kernels": {"XAUUSD+": kernel}}},
            processors=tuple(
                SimpleNamespace(name=name)
                for name in ("OZ_STATE", "SWEEP_STATE", "FVG_STATE")
            ),
            strategies=tuple(
                SimpleNamespace(name=name)
                for name in ("COMPOSER", "OZ", "WATCH_CONDITIONS")
            ),
        )

        result = specialize(engine, [])

        self.assertTrue(result["specialized"])
        self.assertTrue({"WATCH", "MA"} <= set(result["capabilities"]))
        self.assertNotIn("conservative_dependencies", result)


if __name__ == "__main__":
    unittest.main()
