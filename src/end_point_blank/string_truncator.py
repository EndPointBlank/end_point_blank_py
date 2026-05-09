from __future__ import annotations

DEFAULT_LIMIT = 1000
DEFAULT_SUFFIX = "<truncated>"


class StringTruncator:
    """
    Byte-aware string truncator that preserves valid UTF-8 sequences.

    Equivalent to the Ruby gem's ``StringTruncator``.
    """

    @staticmethod
    def truncate(
        s: str | None,
        limit: int = DEFAULT_LIMIT,
        suffix: str = DEFAULT_SUFFIX,
    ) -> str:
        """
        Truncates *s* so that its UTF-8 byte length does not exceed *limit*.
        Appends *suffix* when truncation occurs.  Always returns a ``str``.
        """
        if s is None:
            return ""
        encoded = s.encode("utf-8")
        if len(encoded) <= limit:
            return s

        suffix_bytes = suffix.encode("utf-8")
        max_bytes = limit - len(suffix_bytes)

        truncated = encoded[:max_bytes]
        # Decode with error replacement, then re-encode to remove any partial
        # multi-byte character that was cut in the middle.
        result = truncated.decode("utf-8", errors="ignore")
        return result + suffix
