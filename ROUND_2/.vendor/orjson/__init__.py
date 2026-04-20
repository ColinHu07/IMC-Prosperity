# SPDX-License-Identifier: (Apache-2.0 OR MIT)
# Copyright ijl (2023)

try:
    from .orjson import *  # type: ignore
    from .orjson import __version__  # type: ignore
except ImportError:
    import json as _json

    __version__ = "fallback"

    OPT_APPEND_NEWLINE = 1 << 0
    OPT_INDENT_2 = 1 << 1
    OPT_NAIVE_UTC = 1 << 2
    OPT_NON_STR_KEYS = 1 << 3
    OPT_OMIT_MICROSECONDS = 1 << 4
    OPT_PASSTHROUGH_DATACLASS = 1 << 5
    OPT_PASSTHROUGH_DATETIME = 1 << 6
    OPT_PASSTHROUGH_SUBCLASS = 1 << 7
    OPT_SERIALIZE_DATACLASS = 1 << 8
    OPT_SERIALIZE_NUMPY = 1 << 9
    OPT_SERIALIZE_UUID = 1 << 10
    OPT_SORT_KEYS = 1 << 11
    OPT_STRICT_INTEGER = 1 << 12
    OPT_UTC_Z = 1 << 13

    JSONDecodeError = _json.JSONDecodeError
    JSONEncodeError = TypeError

    class Fragment(bytes):
        pass

    def dumps(obj, *, option=0, default=None):
        kwargs = {"ensure_ascii": False, "separators": (",", ":")}
        if option & OPT_INDENT_2:
            kwargs["indent"] = 2
        if option & OPT_SORT_KEYS:
            kwargs["sort_keys"] = True
        if default is not None:
            kwargs["default"] = default

        payload = _json.dumps(obj, **kwargs)
        if option & OPT_APPEND_NEWLINE:
            payload += "\n"
        return payload.encode("utf-8")

    def loads(obj):
        if isinstance(obj, (bytes, bytearray, memoryview)):
            obj = bytes(obj).decode("utf-8")
        return _json.loads(obj)


__all__ = (
    "__version__",
    "dumps",
    "Fragment",
    "JSONDecodeError",
    "JSONEncodeError",
    "loads",
    "OPT_APPEND_NEWLINE",
    "OPT_INDENT_2",
    "OPT_NAIVE_UTC",
    "OPT_NON_STR_KEYS",
    "OPT_OMIT_MICROSECONDS",
    "OPT_PASSTHROUGH_DATACLASS",
    "OPT_PASSTHROUGH_DATETIME",
    "OPT_PASSTHROUGH_SUBCLASS",
    "OPT_SERIALIZE_DATACLASS",
    "OPT_SERIALIZE_NUMPY",
    "OPT_SERIALIZE_UUID",
    "OPT_SORT_KEYS",
    "OPT_STRICT_INTEGER",
    "OPT_UTC_Z",
)
