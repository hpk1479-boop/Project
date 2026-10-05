"""Run the existing G1 suites once, writing only S1 evidence."""
from staff_s1_evidence import OUT, frozen_guard
import run_staff_s0_regressions as runner

if __name__ == '__main__':
    frozen_guard()
    runner.OUT = OUT / 'regressions'
    if runner.OUT.exists():
        raise FileExistsError('G1 already ran; rerun only a diagnosed failing test')
    raise SystemExit(runner.main())
