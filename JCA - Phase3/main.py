"""Entry point.

Usage:
    python main.py                          # runs the built-in example PICO set
    python main.py phase2_pico_output.xlsx   # runs every PICO set in the given Phase 2 .xlsx export
    python main.py --template                # writes an example .xlsx template to ./phase2_template.xlsx
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from jca_pipeline import run_phase3_batch  # noqa: E402
from jca_pipeline.output_writer import write_json_output, write_xlsx_output  # noqa: E402
from jca_pipeline.xlsx_loader import generate_template_bytes, load_pico_sets_from_xlsx  # noqa: E402

# INFO logs every phase (node) as it starts/finishes with its elapsed time —
# set to DEBUG for a full before/after state dump per phase.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

OUTPUT_DIR = Path("output")

EXAMPLE_PICO = {
    "pico_set_id": "osimertinib-gefitinib-nsclc-denmark-italy",
    "population": "Locally advanced or metastatic NSCLC, EGFR exon 19 deletion",
    "intervention": "Osimertinib",
    "comparator": {
        "display_value": "Gefitinib",
        "member_states": ["Denmark", "Italy"],
    },
    "outcomes": {
        "clinical_effectiveness": [
            "Overall survival",
            "Progression-free survival",
            "Objective response rate",
        ],
        "safety": ["Grade \u2265 3 adverse events"],
        "quality_of_life": [],
        "clinician_patient_reported": [],
    },
    "metadata": {
        "source_articles": ["PMID:12345678"],
        "phase2_validation_status": "PASS",
    },
}

if __name__ == "__main__":
    args = sys.argv[1:]

    if args and args[0] == "--template":
        out_path = Path("phase2_template.xlsx")
        out_path.write_bytes(generate_template_bytes())
        print(f"Wrote example template to {out_path.resolve()}")

    else:
        # Same code path whether run against one built-in example PICO set or a
        # whole Phase 2 .xlsx export of many — every PICO set that's actually
        # handed to the pipeline gets run and shows up in the "results" envelope
        # below, and that envelope is emitted regardless of how many sets there are.
        if args:
            xlsx_path = args[0]
            pico_sets = load_pico_sets_from_xlsx(xlsx_path)
            print(f"Loaded {len(pico_sets)} PICO set(s) from {xlsx_path}")
        else:
            pico_sets = [EXAMPLE_PICO]

        batch = run_phase3_batch(pico_sets)
        print(json.dumps({"results": batch["results"]}, indent=2))
        print("\n--- Aggregate token usage & latency ---")
        print(json.dumps(batch["aggregate_metrics"], indent=2))

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_path = OUTPUT_DIR / f"phase3_results_{timestamp}.json"
        xlsx_out_path = OUTPUT_DIR / f"phase3_results_{timestamp}.xlsx"
        write_json_output(batch, json_path)
        write_xlsx_output(batch["results"], xlsx_out_path)
        print(f"\nWrote {json_path.resolve()}")
        print(f"Wrote {xlsx_out_path.resolve()}")
