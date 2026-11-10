"""Export an offline dashboard through pennywise's read-only CLI."""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def query(*args):
    return json.loads(
        subprocess.check_output(["uv", "run", "pennywise", *args, "--json"], cwd=ROOT, text=True)
    )


def main():
    report = query("report", "--months")
    accounts = sorted({s["account"] for s in report["statements"]})
    # Every figure comes from pennywise: one calendar-month report for all accounts and one
    # per account. The page only picks figures out of them, never adds them up.
    payload = {
        "reports": {
            "": report,
            **{a: query("report", "--months", "--account", a) for a in accounts},
        },
        "accounts": accounts,
        "transactions": query("transactions"),
        "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    template = (ROOT / "dashboard/template.html").read_text()
    # Escape HTML-significant characters to keep descriptors inside the JSON script.
    encoded = (
        json.dumps(payload).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    )
    target = ROOT / "dashboard/spending.html"
    target.write_text(template.replace("__DATA__", encoded))
    sys.stdout.write(f"Dashboard: {target}\n")


if __name__ == "__main__":
    main()
