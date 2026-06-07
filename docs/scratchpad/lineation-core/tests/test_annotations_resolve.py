# research-pure: every module's annotations resolve — catches an undefined name `__future__` hides.
"""Under `from __future__ import annotations` an annotation is an unevaluated string, so a name that
does not resolve (a missing import, a typo, a lazily-imported SDK type that leaked into a signature)
is invisible to plain import AND to the rest of the suite. This sweep force-evaluates every module's
function/class annotations via `typing.get_type_hints`, so such a name fails LOUD here — and it
proves the network SDK stays lazy (no SDK type may appear in a signature, since this runs SDK-free)."""
from __future__ import annotations

import importlib
import inspect
import pkgutil
import typing

import lineation_core


def test_all_module_annotations_resolve():
    errors: list[str] = []
    for mod in pkgutil.walk_packages(lineation_core.__path__, lineation_core.__name__ + "."):
        m = importlib.import_module(mod.name)
        for name, obj in vars(m).items():
            if getattr(obj, "__module__", None) != m.__name__:
                continue                                  # only this module's own functions/classes
            targets = [obj] if inspect.isfunction(obj) or inspect.isclass(obj) else []
            if inspect.isclass(obj):
                targets += [v for v in vars(obj).values() if inspect.isfunction(v)]
            for t in targets:
                try:
                    typing.get_type_hints(t)
                except Exception as e:                    # noqa: BLE001 — report any resolution failure
                    errors.append(f"{mod.name}.{getattr(t, '__qualname__', t)}: "
                                  f"{type(e).__name__}: {e}")
    assert not errors, "unresolved annotations:\n" + "\n".join(errors)
