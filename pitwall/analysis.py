"""Analyze logs into match results, using the cache and several processes.

`analyze_logs` is what the command line (and later the app) calls. Each log comes back as
one of:
  {"ok": payload}     payload = identity, flags, power result, source file info
  {"skip": reason}    a log Pitwall can't report on (never enabled, no motor current, ...)
  {"error": message}  a broken or unreadable log
"""

import dataclasses
import multiprocessing
import os
import re
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Callable, Dict, List, Optional

from . import __version__, power
from .cache import Cache, cache_key, file_sha256
from .match import SkipLog, build_match
from .profile import ProfileError, load_profile
from .wpilog import WPILog, WPILogError

_STAMP = re.compile(r"(\d{2})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})")


def log_stamp(file_name: str) -> str:
    """'akit_26-11-07_15-00-32_nhalt_q7.wpilog' -> '2026-11-07 15:00:32' (as the robot wrote it)."""
    m = _STAMP.search(file_name)
    if not m:
        return ""
    y, mo, d, h, mi, s = m.groups()
    return f"20{y}-{mo}-{d} {h}:{mi}:{s}"


def identity_dict(m) -> Dict[str, Any]:
    d = dataclasses.asdict(m.identity)
    d["label"] = m.identity.label
    d["is_fms"] = m.identity.is_fms
    d["stamp"] = log_stamp(m.identity.file)
    return d


def _analyze_one(path: str, profile_path: str, current_mode: Optional[str],
                 cache_dir: Optional[str], profile_sha: str) -> Dict[str, Any]:
    profile = load_profile(profile_path)
    if current_mode:
        profile.current_mode = current_mode
    try:
        sha = file_sha256(path)
    except OSError as e:
        return {"error": str(e)}
    cache = Cache(cache_dir)
    key = cache_key(sha, profile_sha, profile.current_mode)
    hit = cache.get(key)
    if hit is not None:
        hit["cached"] = True
        if "ok" in hit:
            hit["ok"]["source"]["path"] = path
        return hit
    source = {"file": os.path.basename(path), "path": path, "sha256": sha, "bytes": os.path.getsize(path)}
    try:
        log = WPILog(path)
        m = build_match(log, profile)
        result = power.analyze(m)
        entry = {"ok": {"pitwall": __version__, "profile": profile.name, "identity": identity_dict(m),
                        "flags": m.flags, "silent": m.silent, "power": result, "source": source}}
    except SkipLog as e:
        entry = {"skip": str(e), "source": source}
    except (WPILogError, ValueError, KeyError, IndexError) as e:
        return {"error": str(e), "source": source}        # errors aren't cached: a fix should retry
    cache.put(key, entry)
    entry["cached"] = False
    return entry


def expand_paths(paths: List[str]) -> List[str]:
    """Files as given; folders are searched (recursively) for .wpilog files."""
    out = []
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs.sort()
                out += [os.path.join(root, f) for f in sorted(files) if f.lower().endswith(".wpilog")]
        else:
            out.append(p)
    return out


def analyze_logs(paths: List[str], profile_path: str, current_mode: Optional[str] = None,
                 cache_dir: Optional[str] = None, jobs: int = 1,
                 progress: Optional[Callable[[str, Dict[str, Any]], None]] = None) -> List[Dict[str, Any]]:
    """Analyze every log; results come back in the order of `paths`."""
    load_profile(profile_path)               # fail fast (ProfileError) before starting workers
    profile_sha = file_sha256(profile_path)
    args = [(p, profile_path, current_mode, cache_dir, profile_sha) for p in paths]
    results: List[Dict[str, Any]] = []
    if jobs <= 1 or len(paths) <= 1:
        for a in args:
            r = _analyze_one(*a)
            results.append(r)
            if progress:
                progress(a[0], r)
        return results
    # "spawn" behaves the same on every OS and is safe when the caller has threads (the app).
    with ProcessPoolExecutor(max_workers=jobs, mp_context=multiprocessing.get_context("spawn")) as ex:
        futures = [ex.submit(_analyze_one, *a) for a in args]
        for a, fu in zip(args, futures):
            try:
                r = fu.result()
            except Exception as e:            # a crashed worker shouldn't lose the other logs
                r = {"error": f"{type(e).__name__}: {e}"}
            results.append(r)
            if progress:
                progress(a[0], r)
    return results


__all__ = ["analyze_logs", "expand_paths", "identity_dict", "log_stamp", "ProfileError"]
