from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from marathon_calendar.ics import races_to_ics  # noqa: E402
from marathon_calendar.seed import demo_races  # noqa: E402


output = Path("examples/demo_all.ics")
output.parent.mkdir(parents=True, exist_ok=True)
races = demo_races()
output.write_text(races_to_ics(races), encoding="utf-8", newline="")
print(f"Wrote {output} with {len(races)} fixture events")
