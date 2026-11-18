"""Analyze logs into match results, using the cache and several processes.

`analyze_logs` is what the command line (and later the app) calls. Each log comes back as
one of:
  {"ok": payload}     payload = identity, flags, one result per module (power, scoring, ...), source file info
  {"parts": [payload, ...]}  a log with no FMS match but one or more Driver Station practice-mode
                      matches in it: one payload each, named <log>.pm1.wpilog, <log>.pm2.wpilog, ...
  {"skip": reason}    a log Pitwall can't report on (never enabled, no motor current, ...)
  {"error": message}  a broken or unreadable log
"""

import dataclasses
import multiprocessing
import os
import re
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import __version__
from .cache import Cache, cache_key, file_sha256
from .match import SkipLog, _identity, build_match, practice_windows
from .modules import MODULES
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
    d["is_match"] = m.identity.is_match
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
        for p in hit.get("parts") or []:
            p["source"]["path"] = path
        return hit
    source = {"file": os.path.basename(path), "path": path, "sha256": sha, "bytes": os.path.getsize(path)}
    try:
        log = WPILog(path)
        windows = [] if _identity(log).is_fms else practice_windows(log, profile)
        if windows:
            entry = {"parts": _practice_parts(log, profile, windows, source), "source": source}
            if not entry["parts"]:
                entry = {"skip": "its practice matches couldn't be reported on", "source": source}
        else:
            entry = {"ok": _payload(build_match(log, profile), profile, source)}
    except SkipLog as e:
        entry = {"skip": str(e), "source": source}
    except (WPILogError, ValueError, KeyError, IndexError) as e:
        return {"error": str(e), "source": source}        # errors aren't cached: a fix should retry
    cache.put(key, entry)
    entry["cached"] = False
    return entry


def _payload(m, profile, source: Dict[str, Any]) -> Dict[str, Any]:
    ok = {"pitwall": __version__, "profile": profile.name, "identity": identity_dict(m),
          "flags": m.flags, "silent": m.silent, "source": source}
    for mod in MODULES:
        if mod.applies(profile):
            ok[mod.name] = mod.analyze(m)
    return ok


def _practice_parts(log: WPILog, profile, windows, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One payload per practice-mode match in the log, each with its own name and start time."""
    from .fetch.identify import _clock_start, stamp_from_name
    import datetime as dt
    start = _clock_start(log) or stamp_from_name(source["file"])
    stem = os.path.splitext(source["file"])[0]
    parts = []
    for k, w in enumerate(windows, 1):
        try:
            m = build_match(log, profile, w, k)
        except SkipLog:
            continue
        src = dict(source, file=f"{stem}.pm{k}.wpilog", log_file=source["file"], window=list(w))
        p = _payload(m, profile, src)
        if start:
            t = dt.datetime.fromisoformat(start) + dt.timedelta(seconds=w[0] - log.t_span[0])
            p["identity"]["stamp"] = t.strftime("%Y-%m-%d %H:%M:%S")
            p["identity"]["day"] = t.strftime("%Y-%m-%d")
        parts.append(p)
    return parts


def payloads_of(results) -> List[Tuple[str, Dict[str, Any]]]:
    """[(path, payload)] for every match in analysis results, practice-mode parts included."""
    out = []
    for path, r in results:
        if "ok" in r:
            out.append((path, r["ok"]))
        for p in r.get("parts") or []:
            out.append((path, p))
    return out


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


__all__ = ["analyze_logs", "expand_paths", "identity_dict", "log_stamp", "payloads_of", "ProfileError"]
