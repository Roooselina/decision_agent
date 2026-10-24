# Human-written cases

Put cases written by teammates here as `*.json` files. Each file is a JSON list of cases in the
schema of `src/decision_agent/eval/dataset.py` (see `TEMPLATE.json.example`).

Rules that keep the evaluation honest:

- The author must not have seen the model outputs or the system prompts when writing gold labels.
- Write the meeting minutes first, then the artifacts, then the gold labels, and only then run the
  system.
- Set `source` to `human_written` and `author` to the writer's name.
- `needs_clarification` is true only when the label cannot be derived from the documents.
- `scope_trap` is true for decoy artifacts that a scope-ignoring system would flag by mistake.
- Add a scripted `user_facts` entry for every question a human would reasonably be asked.
