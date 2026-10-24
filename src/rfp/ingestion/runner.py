"""Internal isolated parser subprocess. Paths originate only from the parent parser."""

import json
import sys
from pathlib import Path

from rfp.ingestion.parser import _child

if __name__ == "__main__":
    config = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
    _child(
        sys.argv[1],
        sys.argv[2],
        config["relative_path"],
        config["bid_id"],
        config["options"],
        config["executable"],
    )
