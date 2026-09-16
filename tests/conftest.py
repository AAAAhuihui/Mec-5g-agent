from __future__ import annotations

import os


# Unit tests exercise reranker behavior with fakes; do not load the 2.12 GiB model.
os.environ.setdefault("RERANKER_BACKEND", "lightweight")
