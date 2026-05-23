from __future__ import annotations

import dis
import logging
import re
from typing import Any, Dict, Iterable, List, Optional

from ..commands.endpoint_update import EndpointUpdate

logger = logging.getLogger(__name__)

_DJANGO_PARAM_RE = re.compile(r"<(?:[^:>]+:)?([^>]+)>")


def _normalize_path(path: str) -> str:
    """Convert Django path syntax to Rails-style: ``<int:id>`` -> ``:id``."""
    return _DJANGO_PARAM_RE.sub(r":\1", path)


def register_django_endpoints() -> None:
    """
    Inspects all registered Django URL patterns and publishes the endpoint list
    to the EndPointBlank API.

    Equivalent to the Rails route-scanning logic in the Ruby gem.
    Call once after Django is fully initialized, e.g. in ``AppConfig.ready()``::

        from end_point_blank.django import register_django_endpoints

        class MyAppConfig(AppConfig):
            def ready(self):
                register_django_endpoints()

    """
    from django.urls import get_resolver

    endpoints = _collect_endpoints(get_resolver())
    EndpointUpdate.send_update(endpoints)
    logger.info("Registered %d endpoints with EndPointBlank.", len(endpoints))


def _collect_endpoints(resolver, prefix: str = "") -> List[Dict[str, Any]]:
    endpoints: List[Dict[str, Any]] = []
    from django.urls import URLPattern, URLResolver

    for pattern in resolver.url_patterns:
        try:
            if isinstance(pattern, URLResolver):
                endpoints.extend(_collect_endpoints(pattern, prefix + str(pattern.pattern)))
            elif isinstance(pattern, URLPattern):
                path = prefix + str(pattern.pattern)
                if not path or path.startswith("__"):
                    continue
                callback = pattern.callback
                versions = _extract_versions(callback)
                # Skip routes with no declared version metadata. An empty
                # array under a declared state still counts as "declared".
                if not versions:
                    continue
                normalized = _normalize_path("/" + path.lstrip("/"))
                # Emit one endpoint per allowed HTTP method so the runtime
                # authorize call (which knows the actual method) can find a
                # matching row. Default to GET when methods aren't declared.
                methods = _extract_http_methods(callback) or ["GET"]
                for method in methods:
                    endpoints.append({
                        "path": normalized,
                        "http_method": method.upper(),
                        "endpoint_versions": versions,
                    })
        except Exception:
            continue
    return endpoints


def _extract_http_methods(callback) -> Optional[List[str]]:
    """Walk the wrapper chain looking for the ``request_method_list`` closure
    cell that ``@require_http_methods`` captures. Returns ``None`` if the view
    has no method restriction declared."""
    for func in _wrapper_chain(callback):
        closure = getattr(func, "__closure__", None)
        code = getattr(func, "__code__", None)
        if not closure or not code:
            continue
        for name, cell in zip(code.co_freevars, closure):
            if name == "request_method_list":
                try:
                    value = cell.cell_contents
                except ValueError:
                    continue
                if isinstance(value, Iterable):
                    return [m for m in value]
    return None


def _extract_versions(callback) -> Dict[str, List[str]]:
    """Find ``_epb_versions``. First look directly on the wrapper chain;
    if nothing's declared there, scan the callback's bytecode for any
    global functions it references and aggregate their versions. This
    handles the common Django pattern where a URL-bound dispatcher
    forwards to per-method inner functions decorated with ``@versioned``.
    """
    for func in _wrapper_chain(callback):
        versions = getattr(func, "_epb_versions", None)
        if versions:
            return dict(versions)

    return _scan_referenced_versions(callback)


def _scan_referenced_versions(callback) -> Dict[str, List[str]]:
    aggregated: Dict[str, List[str]] = {}
    for func in _wrapper_chain(callback):
        code = getattr(func, "__code__", None)
        globs = getattr(func, "__globals__", None)
        if code is None or globs is None:
            continue
        for instr in dis.get_instructions(code):
            if instr.opname not in ("LOAD_GLOBAL", "LOAD_NAME"):
                continue
            name = instr.argval
            if not isinstance(name, str) or name not in globs:
                continue
            referenced = globs[name]
            versions = getattr(referenced, "_epb_versions", None)
            if not isinstance(versions, dict):
                continue
            for state, vs in versions.items():
                merged = aggregated.get(state, [])
                # de-dupe while preserving order
                aggregated[state] = list(dict.fromkeys([*merged, *vs]))
    return aggregated


def _wrapper_chain(func) -> Iterable[Any]:
    """Yield ``func`` and everything reachable through ``__wrapped__``."""
    seen = set()
    current = func
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = getattr(current, "__wrapped__", None)
