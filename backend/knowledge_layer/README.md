# 📚 knowledge_layer

A **dynamic, metadata-aware knowledge registry** for 5G network simulators.

This module enables scalable, explainable, and introspectable access to live simulation data using a FastAPI-style route mechanism for **getters** and **explainers**.

---

## 🧠 Features

- **Live knowledge access**: Data is resolved from the current simulator state, not stored snapshots.
- **Explainable values**: Each value can be paired with a human-readable explainer function.
- **Routing-style API**: Routes like `/net/ue/{ue_imsi}/speed_mps` match requests and dispatch to handlers.
- **Tags and relationships**: Knowledge keys are annotated for semantic grouping and dependency tracking.
- **Modular registration**: Just add decorated functions in `knowledge_sources/`, and they’re wired in.

---

## 🧩 Extending the System
Add new files under knowledge_sources/, e.g. ric_getters.py

Define decorated functions using @knowledge_getter and @knowledge_explainer

Add semantic tags and related metadata

No manual registration needed — decorators handle it automatically

## 🧠 Relationships
Leverage standard semantic links for knowledge graph traversal:

```python
from knowledge_layer.relationships import Relationship

related = [
    ("specs/3gpp/38.300/4.1.2", Relationship.RELATED_STANDARD),
    ("/net/cell/{cell_id}/load", Relationship.DEPENDS_ON)
]
```
