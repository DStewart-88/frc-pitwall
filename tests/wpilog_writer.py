"""Minimal WPILOG writer for building synthetic test logs."""

import struct


class LogWriter:
    def __init__(self, path):
        self.f = open(path, "wb")
        self.f.write(b"WPILOG" + struct.pack("<H", 0x0100) + struct.pack("<I", 0))
        self.ids = {}

    def _record(self, entry_id, ts_us, payload):
        self.f.write(bytes([0x7F]) + struct.pack("<IIQ", entry_id, len(payload), ts_us) + payload)

    def _start(self, name, typ):
        eid = len(self.ids) + 1
        self.ids[name] = (eid, typ)

        def s(x):
            b = x.encode()
            return struct.pack("<I", len(b)) + b
        self._record(0, 0, bytes([0]) + struct.pack("<I", eid) + s(name) + s(typ) + s(""))
        return eid

    def put(self, name, t_s, value, typ=None):
        if name not in self.ids:
            typ = typ or ("boolean" if isinstance(value, bool) else "string" if isinstance(value, str)
                          else "int64" if isinstance(value, int) else "double")
            self._start(name, typ)
        eid, typ = self.ids[name]
        if typ == "double":
            p = struct.pack("<d", value)
        elif typ == "int64":
            p = struct.pack("<q", value)
        elif typ == "boolean":
            p = bytes([1 if value else 0])
        else:
            p = value.encode()
        self._record(eid, int(round(t_s * 1e6)), p)

    def close(self):
        self.f.close()
