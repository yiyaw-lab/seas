SUPERSEDED:
This document reflects an earlier capability-centered architecture.
The current SEAS north star is:
Signal → Opportunity → Experiment → Finding → Theory

---

# SEAS V2 Architecture

Mission

Transform frontier developments into capability-building experiments.

Core Question

What capability can Yiya gain from this frontier development?

---

Old Architecture

Signal
↓
Enrich
↓
Score
↓
Classify
↓
Generate Experiment

---

New Architecture

Frontier Brief
↓
Capability Analysis
↓
Experiment Options
↓
Experiment Selection
↓
Build Plan
↓
Artifact
↓
Reflection

---

Capability Analysis Output

{
  "signal_title": "",
  "classification": "",
  "capability_unlocked": "",
  "weighted_score": 0,
  "experiment_options": []
}

---

Legacy Components

Potentially removable:

- enrich_signal.py
- apply_enrichment.py
- auto_score.py
- apply_score.py
- classify_signal.py
- generate_experiment_job.py
- import_scores.py

---

SEAS North Star

SEAS succeeds when it consistently recommends experiments that create more capability gain than Yiya would have achieved through unstructured exploration.
