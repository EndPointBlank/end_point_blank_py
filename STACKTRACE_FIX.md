# Python stacktrace-array contract fix

## Decision: ORPHANED — deleted payload_builder.py + writers/writer.py

Evidence:
- `grep -rn "\bWriter(" src` (excluding DelayedWriter/DirectWriter/RequestWriter/ResponseWriter/LogWriter/ExceptionWriter instantiations) returned **zero** matches for the generic `Writer(...)`. Nothing imports `from ..writers.writer import Writer` or `writers.writer` anywhere in `src` or `tests`.
- `grep -rln "PayloadBuilder"` (excluding `payload_builder.py`/`writer.py` themselves) returned nothing — no test or production code references `PayloadBuilder`.
- The two real, live error-reporting entry points are:
  - `middleware/report_interaction.py:89` → `ExceptionWriter.write(exc)`
  - `django/middleware.py:86,100` → `ExceptionWriter.write(exc)` / `ExceptionWriter.write(exception)`
  Both call `ExceptionWriter.write`, never `Writer.write`.
- `exception_writer.py`'s `_format_stacktrace(exc)` (line 61) already does `traceback.format_exception(...)` then `.splitlines()`, producing a `list[str]` — i.e. the sole live path already sends an array. `payload_builder.py`/`writers/writer.py` were unused dead code doing `"\n".join(stacktrace)`.

## Fix
- Deleted `src/end_point_blank/payload_builder.py`
- Deleted `src/end_point_blank/writers/writer.py`
- No change needed to `exception_writer.py` (already correct); `_format_stacktrace` is a module-level function, importable directly in tests without any visibility changes.
- Added `tests/test_exception_writer_stacktrace.py`, asserting:
  - `_format_stacktrace(exc)` returns a `list`, not a `str`.
  - The list has >1 entries for a real exception (raised through a nested function to get a real traceback).
  - No entry contains an embedded `\n` (frames aren't joined together).
  - At least one entry mentions the exception type (`ValueError`).

## Test result
`./test.sh` → `86 passed in 4.51s` (includes the new test).

## Commit
`1e51a04` — `fix: send stacktrace as array, not joined string (contract)`
Branch: `fix-array-stacktrace` (based on `master`)
Files: payload_builder.py (deleted), writers/writer.py (deleted), tests/test_exception_writer_stacktrace.py (new)
Not pushed.
