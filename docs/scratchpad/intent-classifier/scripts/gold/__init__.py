# research-pure: the lineation gold-rebuild core (aggregation · blocks · audit · manifest).
"""Consolidates the proven scoring/merge logic into one typed, pure, tested unit. Mirrors the
target `pancratius/ml/lineation/adjudicate.py` (ARCHITECTURE §10–11) so promotion is a move, not
a rewrite. The core (`types`/`aggregate`/`blocks`/`audit`/`manifest`) is import-pure; `run` is the
only module that touches the substrate and disk.
"""
