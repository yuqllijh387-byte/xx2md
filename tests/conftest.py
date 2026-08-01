import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "research-doc-ingest" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
