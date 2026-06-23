"""Intel package — pure-function pipeline for the information radar.

Stages, each independently callable (the core design difference from the old
project, where these were fused into one build_intel_state call):

  Stage 1  collect  (connectors)    → RawItem[]      fetch from sources
  Stage 1b normalize(normalize.py)  → DiscoveryItem[]  tokenize + dedupe key
  Stage 2  cluster  (cluster.py)    → clusters        union-find merge
  Stage 3  score    (score.py)      → IntelEvent[]    velocity/coverage/freshness
  Stage 4  deep_dive(deep_dive.py)  → EventDeepDive   fetch + extract sources

Every function here is pure (no DB, no global state). The atomic operations in
agent_news/operations/ wrap these and handle persistence.
"""

from .cluster import cluster_discovery_items, event_id_for_cluster
from .normalize import normalize_raw_items
from .score import build_events_from_clusters, score_event
from .tokenizer import tokenize

__all__ = [
    "build_events_from_clusters",
    "cluster_discovery_items",
    "event_id_for_cluster",
    "normalize_raw_items",
    "score_event",
    "tokenize",
]
