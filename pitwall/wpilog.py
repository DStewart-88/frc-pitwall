"""Dependency-free reader for WPILOG files (WPILib DataLog / AdvantageKit).

Format (WPILib Data Log v1.0):
  Header:  b"WPILOG" | u16 version (LE) | u32 extra-header length | extra header
  Records: 1 header byte (length bitfield) | entry id | payload size | timestamp (us) | payload
    header byte bits: id_len = (b & 0x3) + 1, size_len = ((b >> 2) & 0x3) + 1,
                      ts_len = ((b >> 4) & 0x7) + 1
  Control records (entry id 0): byte0 = 0 Start (u32 id, str name, str type, str metadata),
                                1 Finish, 2 SetMetadata.   (str = u32 length + UTF-8)

Struct, protobuf and raw entries come back as raw bytes for now; the power report reads
only plain numbers, booleans and strings.

Times are returned in seconds (FPGA microseconds / 1e6).
"""

import struct
from typing import Any, Callable, Dict, List, Optional, Tuple

class WPILogError(Exception):
    pass


class Entry:
    __slots__ = ("id", "name", "type", "metadata")

    def __init__(self, entry_id: int, name: str, type_str: str, metadata: str):
        self.id = entry_id
        self.name = name
        self.type = type_str
        self.metadata = metadata

    def __repr__(self) -> str:
        return f"Entry(id={self.id}, name={self.name!r}, type={self.type!r})"


def _read_uint_le(data: bytes, pos: int, length: int) -> int:
    return int.from_bytes(data[pos:pos + length], "little")


class WPILog:
    """A parsed .wpilog file."""

    def __init__(self, path: str):
        self.path = path
        with open(path, "rb") as f:
            self._data = f.read()
        self._entries_by_id: Dict[int, Entry] = {}
        self._records_by_id: Dict[int, List[Tuple[int, bytes]]] = {}
        self._name_to_ids: Dict[str, List[int]] = {}
        self._series_cache: Dict[str, Tuple[List[float], List[Any]]] = {}
        self.extra_header = ""
        self.version = (0, 0)
        self.truncated = False
        self._parse()

    def _parse(self) -> None:
        data = self._data
        if data[0:6] != b"WPILOG":
            raise WPILogError(f"{self.path}: not a WPILOG file")
        ver = data[6] | (data[7] << 8)
        self.version = (ver >> 8, ver & 0xFF)
        extra_len = struct.unpack_from("<I", data, 8)[0]
        pos = 12
        self.extra_header = data[pos:pos + extra_len].decode("utf-8", "replace")
        pos += extra_len
        n = len(data)
        records = self._records_by_id
        while pos < n:
            hb = data[pos]
            pos += 1
            id_len = (hb & 0x3) + 1
            size_len = ((hb >> 2) & 0x3) + 1
            ts_len = ((hb >> 4) & 0x7) + 1
            if pos + id_len + size_len + ts_len > n:
                self.truncated = True
                break
            entry_id = _read_uint_le(data, pos, id_len)
            pos += id_len
            size = _read_uint_le(data, pos, size_len)
            pos += size_len
            ts = _read_uint_le(data, pos, ts_len)
            pos += ts_len
            if pos + size > n:
                self.truncated = True
                break
            payload = data[pos:pos + size]
            pos += size
            if entry_id == 0:
                self._handle_control(payload)
            else:
                lst = records.get(entry_id)
                if lst is None:
                    records[entry_id] = [(ts, payload)]
                else:
                    lst.append((ts, payload))

    def _handle_control(self, payload: bytes) -> None:
        if not payload or payload[0] != 0:
            return
        entry_id = struct.unpack_from("<I", payload, 1)[0]
        name, pos = self._read_str(payload, 5)
        type_str, pos = self._read_str(payload, pos)
        metadata, pos = self._read_str(payload, pos)
        self._entries_by_id[entry_id] = Entry(entry_id, name, type_str, metadata)
        ids = self._name_to_ids.setdefault(name, [])
        if entry_id not in ids:
            ids.append(entry_id)

    @staticmethod
    def _read_str(payload: bytes, pos: int) -> Tuple[str, int]:
        length = struct.unpack_from("<I", payload, pos)[0]
        pos += 4
        return payload[pos:pos + length].decode("utf-8", "replace"), pos + length

    def _decoder(self, t: str) -> Callable[[bytes], Any]:
        if t == "double":
            return lambda p: struct.unpack("<d", p)[0] if len(p) >= 8 else None
        if t == "float":
            return lambda p: struct.unpack("<f", p)[0] if len(p) >= 4 else None
        if t == "int64":
            return lambda p: struct.unpack("<q", p)[0] if len(p) >= 8 else None
        if t == "boolean":
            return lambda p: p[0] != 0 if p else None
        if t in ("string", "json"):
            return lambda p: p.decode("utf-8", "replace")
        if t == "boolean[]":
            return lambda p: [b != 0 for b in p]
        if t == "int64[]":
            return lambda p: list(struct.unpack("<%dq" % (len(p) // 8), p[:len(p) // 8 * 8]))
        if t == "double[]":
            return lambda p: list(struct.unpack("<%dd" % (len(p) // 8), p[:len(p) // 8 * 8]))
        if t == "float[]":
            return lambda p: list(struct.unpack("<%df" % (len(p) // 4), p[:len(p) // 4 * 4]))
        if t == "string[]":
            def dec(p: bytes) -> List[str]:
                count = struct.unpack_from("<I", p, 0)[0]
                pos, out = 4, []
                for _ in range(count):
                    s, pos = self._read_str(p, pos)
                    out.append(s)
                return out
            return dec
        return lambda p: p

    # ---- public API ----

    def entries(self, pattern: Optional[str] = None) -> List[Tuple[str, str]]:
        """(name, type) pairs, sorted. `pattern`: case-insensitive substring filter."""
        out = []
        for name, ids in self._name_to_ids.items():
            type_str = self._entries_by_id[ids[-1]].type if ids else ""
            if pattern is None or pattern.lower() in name.lower():
                out.append((name, type_str))
        return sorted(out)

    def has(self, name: str) -> bool:
        return name in self._name_to_ids

    def type_of(self, name: str) -> str:
        return self._entries_by_id[self._name_to_ids[name][-1]].type

    def sample_count(self, name: str) -> int:
        return sum(len(self._records_by_id.get(eid, [])) for eid in self._name_to_ids.get(name, []))

    def series(self, name: str) -> Tuple[List[float], List[Any]]:
        """(times in seconds, values), merged across reused entry ids, sorted by time."""
        cached = self._series_cache.get(name)
        if cached is not None:
            return cached
        ids = self._name_to_ids.get(name)
        if not ids:
            raise KeyError(f"no entry named {name!r}")
        recs: List[Tuple[int, Callable[[bytes], Any], bytes]] = []
        for eid in ids:
            dec = self._decoder(self._entries_by_id[eid].type)
            for ts, payload in self._records_by_id.get(eid, []):
                recs.append((ts, dec, payload))
        recs.sort(key=lambda r: r[0])
        out = ([ts / 1e6 for ts, _, _ in recs], [dec(p) for _, dec, p in recs])
        self._series_cache[name] = out
        return out

    def last_value(self, name: str, default: Any = None) -> Any:
        if not self.has(name):
            return default
        _, v = self.series(name)
        return v[-1] if v else default

    @property
    def t_span(self) -> Tuple[float, float]:
        lo = hi = None
        for recs in self._records_by_id.values():
            if recs:
                a, b = recs[0][0], recs[-1][0]
                lo = a if lo is None else min(lo, a)
                hi = b if hi is None else max(hi, b)
        return ((lo or 0) / 1e6, (hi or 0) / 1e6)


def hold(times: List[float], values: List[Any], grid: List[float]) -> List[Any]:
    """Sample-and-hold `values` onto ascending `grid` (None before the first sample)."""
    out = []
    j, n, cur = 0, len(times), None
    for g in grid:
        while j < n and times[j] <= g:
            cur = values[j]
            j += 1
        out.append(cur)
    return out
