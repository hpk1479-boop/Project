"""Allow independent execution of the supplied component fixture dependencies."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'validation_suite'))
