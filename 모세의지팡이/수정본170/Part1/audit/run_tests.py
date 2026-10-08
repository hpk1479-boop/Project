"""Run without operational dependencies; persist a reviewable test report."""
import hashlib
import json
import platform
import sys
import unittest
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=ResourceWarning)
sys.dont_write_bytecode = True
from harness import FIXTURES, ROOT
from test_baseline import Baseline
from test_config_deadline import ConfigDeadline
from test_state_io import StateIO
from test_portable_paths import PortablePaths
from test_slow_feed import SlowFeed
from test_ack_pressure import AckPressure
from test_watch_reply import WatchReply
from test_oz_trigger_profiles import OZTriggerProfiles, ControlTriggerSlots
from test_indicator_split import IndicatorSplit
from test_oz_fvg_optimization import OZFVGOptimization
from source_integrity import verify_sources


class Result(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []
    def addSuccess(self, test):
        super().addSuccess(test)
        self.records.append({"test": test.id(), "status": "PASS"})
    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.records.append({"test": test.id(), "status": "FAIL", "detail": self._exc_info_to_string(err, test)})
    def addError(self, test, err):
        super().addError(test, err)
        self.records.append({"test": test.id(), "status": "ERROR", "detail": self._exc_info_to_string(err, test)})
    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err:
            self.records.append({"test": subtest.id(), "status": "FAIL" if issubclass(err[0], test.failureException) else "ERROR",
                                 "detail": self._exc_info_to_string(err, test)})


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Baseline)
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(ConfigDeadline))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(StateIO))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(PortablePaths))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(SlowFeed))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(AckPressure))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(WatchReply))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(OZTriggerProfiles))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(ControlTriggerSlots))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(IndicatorSplit))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(OZFVGOptimization))
    result = unittest.TextTestRunner(verbosity=2, resultclass=Result).run(suite)
    integrity = verify_sources()
    report = {"mode": "offline production-source remediation regression; in-memory pyobj transport",
              "python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__,
              "source_integrity": integrity,
              "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "skipped": len(result.skipped), "tests": result.records}
    path = Path(__file__).resolve().parent / "results" / "remediation_test_report.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {path}")
    sys.exit(0 if result.wasSuccessful() and not integrity['integrity_errors'] else 1)
