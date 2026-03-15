#!/usr/bin/env python3
"""domhunt: local CLI for finding unregistered .com domains.

Behavior:
- 1-3 letters: exhaustive generation, every candidate checked via Verisign RDAP
- 4-7 letters: weighted random generation biased toward short, sayable, brandable patterns
- Only domains that appear unregistered are written to output
- Local SQLite cache supports resume/restart
- Standard library only
"""

from __future__ import annotations

import argparse
import concurrent.futures
import heapq
import json
import random
import shutil
import sqlite3
import string
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

LETTERS = string.ascii_lowercase
PURE_VOWELS = "aeiou"
SEMIVOWELS = "y"
CONSONANTS = "bcdfghjklmnpqrstvwxz"

STYLE_PRESETS: Dict[str, dict] = {
    "brandable": {
        "vowels": PURE_VOWELS,
        "semivowels": SEMIVOWELS,
        "consonants": CONSONANTS,
        "vowel_weights": {"a": 1.00, "e": 1.08, "i": 1.02, "o": 0.96, "u": 0.42},
        "semivowel_weights": {"y": 1.18},
        "consonant_weights": {
            "b": 0.52,
            "c": 0.95,
            "d": 0.62,
            "f": 0.55,
            "g": 0.45,
            "h": 0.42,
            "j": 0.26,
            "k": 0.32,
            "l": 0.92,
            "m": 0.88,
            "n": 0.95,
            "p": 0.58,
            "q": 0.02,
            "r": 1.00,
            "s": 0.82,
            "t": 0.74,
            "v": 0.66,
            "w": 0.16,
            "x": 0.40,
            "z": 0.30,
        },
        "pleasant_bigrams": {
            "ay",
            "ey",
            "iy",
            "oy",
            "ya",
            "ye",
            "yo",
            "cy",
            "ly",
            "ry",
            "ny",
            "my",
            "io",
            "ia",
            "eo",
            "ai",
            "oa",
            "ra",
            "ro",
            "ri",
            "la",
            "li",
            "lo",
            "na",
            "ne",
            "ni",
            "va",
            "vi",
            "vo",
            "el",
            "en",
            "ar",
            "or",
            "on",
            "is",
            "us",
            "ir",
            "er",
            "yr",
            "ix",
        },
        "pleasant_trigrams": {
            "iya",
            "eyo",
            "ayo",
            "ion",
            "eon",
            "ary",
            "ory",
            "yra",
            "lya",
            "rio",
            "nia",
            "vio",
            "ira",
            "iro",
            "exa",
            "ory",
            "ely",
        },
        "preferred_endings": {
            "io",
            "ia",
            "eo",
            "ya",
            "yn",
            "yr",
            "ra",
            "ro",
            "on",
            "or",
            "el",
            "en",
            "is",
            "us",
            "ix",
        },
    },
    "soft": {
        "vowels": PURE_VOWELS,
        "semivowels": SEMIVOWELS,
        "consonants": CONSONANTS,
        "vowel_weights": {"a": 1.02, "e": 1.18, "i": 1.08, "o": 0.92, "u": 0.34},
        "semivowel_weights": {"y": 0.92},
        "consonant_weights": {
            "b": 0.34,
            "c": 0.62,
            "d": 0.45,
            "f": 0.30,
            "g": 0.24,
            "h": 0.28,
            "j": 0.18,
            "k": 0.12,
            "l": 1.15,
            "m": 1.00,
            "n": 1.02,
            "p": 0.26,
            "q": 0.01,
            "r": 1.05,
            "s": 0.70,
            "t": 0.44,
            "v": 0.26,
            "w": 0.10,
            "x": 0.10,
            "z": 0.08,
        },
        "pleasant_bigrams": {
            "la",
            "le",
            "li",
            "lo",
            "ma",
            "me",
            "mi",
            "na",
            "ne",
            "ni",
            "ra",
            "re",
            "ri",
            "ro",
            "el",
            "en",
            "ia",
            "io",
            "eo",
            "ly",
        },
        "pleasant_trigrams": {"lia", "rio", "mia", "nea", "elo", "ria", "nio", "lea"},
        "preferred_endings": {"ia", "io", "ea", "ra", "la", "el", "en", "ly"},
    },
    "hard-tech": {
        "vowels": PURE_VOWELS,
        "semivowels": SEMIVOWELS,
        "consonants": CONSONANTS,
        "vowel_weights": {"a": 0.74, "e": 0.96, "i": 0.86, "o": 0.58, "u": 0.22},
        "semivowel_weights": {"y": 1.26},
        "consonant_weights": {
            "b": 0.16,
            "c": 1.00,
            "d": 0.32,
            "f": 0.32,
            "g": 0.42,
            "h": 0.42,
            "j": 0.06,
            "k": 0.58,
            "l": 0.26,
            "m": 0.24,
            "n": 0.30,
            "p": 0.40,
            "q": 0.01,
            "r": 0.82,
            "s": 0.44,
            "t": 0.68,
            "v": 0.70,
            "w": 0.04,
            "x": 1.36,
            "z": 0.74,
        },
        "pleasant_bigrams": {
            "cx",
            "xt",
            "xr",
            "xy",
            "ix",
            "ex",
            "tr",
            "vr",
            "cr",
            "cy",
            "ty",
            "ry",
            "yr",
        },
        "pleasant_trigrams": {"xyr", "vex", "trix", "zyr", "nex", "ryx", "xer"},
        "preferred_endings": {"ix", "yr", "ex", "on", "or"},
    },
}

PATTERNS = {
    4: ["CVCV", "VCVC", "CVSV", "CVVC", "CVSC"],
    5: ["CVCVC", "CVSVC", "VCVCV", "CVSCV", "CVCCV", "CVCSV"],
    6: ["CVCVCV", "CVSVCV", "CVCSVC", "VCVCVC", "CVCCVC", "CVCSCV"],
    7: ["CVCVCVC", "CVSVCVC", "CVCSVCV", "VCVCVCV", "CVCCVCV", "CVCSCVC"],
}

COMMON_CLUSTERS = {
    "bl",
    "br",
    "cl",
    "cr",
    "dr",
    "fl",
    "fr",
    "gl",
    "gr",
    "pl",
    "pr",
    "sl",
    "sm",
    "sn",
    "sp",
    "st",
    "tr",
    "ch",
    "sh",
    "th",
    "ph",
    "vr",
    "cy",
    "ly",
    "ry",
}
BAD_BIGRAMS = {
    "qj",
    "jq",
    "zx",
    "xq",
    "qx",
    "jj",
    "ww",
    "wu",
    "uq",
    "qo",
    "qa",
    "qe",
    "qi",
}
BAD_TRIGRAMS = {"qzx", "zxq", "jjj", "www", "qxj", "jqx", "yyy"}


@dataclass(order=True)
class PrioritizedCandidate:
    priority: float
    payload: Tuple[str, str, float] = field(compare=False)


class TokenBucket:
    def __init__(self, rate_per_sec: float, burst: int):
        self.rate = max(rate_per_sec, 0.1)
        self.capacity = max(burst, 1)
        self.tokens = float(self.capacity)
        self.updated = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                elapsed = now - self.updated
                self.updated = now
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
            time.sleep(0.02 + random.random() * 0.04)


class DomainDB:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS domains (
                    name TEXT PRIMARY KEY,
                    length INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    score REAL NOT NULL,
                    source TEXT NOT NULL,
                    checked_at REAL NOT NULL,
                    http_status INTEGER,
                    note TEXT
                )
                """
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_domains_status_length ON domains(status, length)"
            )
            self.conn.commit()

    def get_status(self, name: str) -> Optional[str]:
        with self.lock:
            row = self.conn.execute(
                "SELECT status FROM domains WHERE name = ?", (name,)
            ).fetchone()
        return row[0] if row else None

    def upsert(
        self,
        name: str,
        status: str,
        score: float,
        source: str,
        http_status: Optional[int],
        note: str,
    ) -> None:
        with self.lock:
            self.conn.execute(
                """
                INSERT INTO domains(name, length, status, score, source, checked_at, http_status, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    length=excluded.length,
                    status=excluded.status,
                    score=excluded.score,
                    source=excluded.source,
                    checked_at=excluded.checked_at,
                    http_status=excluded.http_status,
                    note=excluded.note
                """,
                (
                    name,
                    len(name),
                    status,
                    score,
                    source,
                    time.time(),
                    http_status,
                    note,
                ),
            )
            self.conn.commit()

    def export_status(self, status: str, out_path: str) -> None:
        with self.lock:
            rows = self.conn.execute(
                "SELECT name FROM domains WHERE status = ? ORDER BY length ASC, score DESC, name ASC",
                (status,),
            ).fetchall()
        Path(out_path).write_text(
            "".join(f"{name}.com\n" for (name,) in rows), encoding="utf-8"
        )

    def stats(self) -> dict:
        with self.lock:
            rows = self.conn.execute(
                "SELECT status, COUNT(*) FROM domains GROUP BY status ORDER BY status"
            ).fetchall()
        return {k: v for k, v in rows}


def weighted_choice(chars: str, weight_map: Dict[str, float]) -> str:
    weights = [max(0.0001, float(weight_map.get(ch, 0.1))) for ch in chars]
    return random.choices(list(chars), weights=weights, k=1)[0]


def classify_char(ch: str, style: dict) -> str:
    if ch in style["vowels"]:
        return "V"
    if ch in style["semivowels"]:
        return "S"
    return "C"


def pattern_for_name(name: str, style: dict) -> str:
    return "".join(classify_char(ch, style) for ch in name)


def pronounce_score(name: str, style: dict) -> float:
    score = 0.0
    n = len(name)
    patt = pattern_for_name(name, style)
    vowels_like = set(style["vowels"] + style["semivowels"])

    if n in (5, 6):
        score += 4.0
    elif n in (4, 7):
        score += 1.5

    if patt in PATTERNS.get(n, []):
        score += 7.0

    if name[0] == "y":
        score -= 2.2
    if "y" in name[1:-1]:
        score += 2.0
    if name.endswith("y"):
        score += 1.3
    if name.count("y") >= 2:
        score -= 1.8

    vcount = sum(1 for ch in name if ch in vowels_like)
    ccount = n - vcount
    if vcount == 0 or ccount == 0:
        score -= 30.0

    prev = "V" if name[0] in vowels_like else "C"
    run = 1
    max_run = 1
    for ch in name[1:]:
        cur = "V" if ch in vowels_like else "C"
        if cur == prev:
            run += 1
            max_run = max(max_run, run)
        else:
            prev = cur
            run = 1
    if max_run == 2:
        score -= 1.2
    elif max_run >= 3:
        score -= 7.0

    for i in range(n - 1):
        bg = name[i : i + 2]
        if bg in style["pleasant_bigrams"]:
            score += 2.0
        if bg in COMMON_CLUSTERS:
            score += 1.2
        if bg in BAD_BIGRAMS:
            score -= 7.0

    for i in range(n - 2):
        tg = name[i : i + 3]
        if tg in style["pleasant_trigrams"]:
            score += 3.0
        if tg in BAD_TRIGRAMS:
            score -= 9.0

    if name[-2:] in style["preferred_endings"]:
        score += 3.2
    if name[-1] in "aeioryxnlm":
        score += 0.8
    if name[-1] in "qjw":
        score -= 4.0

    if "q" in name and "qu" not in name:
        score -= 6.0
    if any(ch * 3 in name for ch in LETTERS):
        score -= 10.0

    return score


def random_name(length: int, style: dict) -> str:
    pattern = random.choice(PATTERNS[length])
    out: List[str] = []
    for token in pattern:
        if token == "V":
            out.append(weighted_choice(style["vowels"], style["vowel_weights"]))
        elif token == "S":
            out.append(weighted_choice(style["semivowels"], style["semivowel_weights"]))
        else:
            out.append(weighted_choice(style["consonants"], style["consonant_weights"]))
    return "".join(out)


def exhaustive_names(length: int) -> Iterator[str]:
    if length == 1:
        for a in LETTERS:
            yield a
        return
    if length == 2:
        for a in LETTERS:
            for b in LETTERS:
                yield a + b
        return
    if length == 3:
        for a in LETTERS:
            for b in LETTERS:
                for c in LETTERS:
                    yield a + b + c
        return
    raise ValueError("exhaustive_names only supports lengths 1..3")


def rdap_lookup(
    fqdn: str, user_agent: str, timeout: float
) -> Tuple[str, Optional[int], str]:
    url = f"https://rdap.verisign.com/com/v1/domain/{urllib.parse.quote(fqdn)}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/rdap+json, application/json;q=0.9, */*;q=0.1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")[:1000]
            if resp.getcode() == 200:
                return "registered", resp.getcode(), body
            return "unknown", resp.getcode(), body
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:1000]
        except Exception:
            pass
        if e.code == 404:
            return "available", e.code, body
        if e.code in (429, 500, 502, 503, 504):
            return "unknown", e.code, body
        return "unknown", e.code, body
    except Exception as e:
        return "unknown", None, repr(e)


def candidate_stream(
    min_length: int, max_length: int, style: dict
) -> Iterator[Tuple[str, str, float]]:
    lengths = list(range(min_length, max_length + 1))

    for n in (1, 2, 3):
        if n in lengths:
            for name in exhaustive_names(n):
                yield name, "exhaustive", 0.0

    seen_random = set()
    longer_lengths = [n for n in lengths if n >= 4]
    if not longer_lengths:
        return

    while True:
        weights = []
        for n in longer_lengths:
            if n == 4:
                weights.append(1.0)
            elif n == 5:
                weights.append(1.6)
            elif n == 6:
                weights.append(1.8)
            else:
                weights.append(1.1)
        length = random.choices(longer_lengths, weights=weights, k=1)[0]
        name = random_name(length, style)
        if name in seen_random:
            continue
        seen_random.add(name)
        score = pronounce_score(name, style)
        if score < 4.0:
            continue
        yield name, "random", score


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Find unregistered .com domains locally with RDAP checks and a brandable generator."
    )
    p.add_argument("--min-length", type=int, default=1)
    p.add_argument("--max-length", type=int, default=7)
    p.add_argument("--exact-length", type=int, default=None)
    p.add_argument("--style", choices=sorted(STYLE_PRESETS), default="brandable")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument(
        "--rate", type=float, default=2.0, help="Global RDAP requests per second."
    )
    p.add_argument("--burst", type=int, default=2)
    p.add_argument("--timeout", type=float, default=8.0)
    p.add_argument(
        "--top-buffer",
        type=int,
        default=400,
        help="Priority queue depth for 4-7 letter candidates.",
    )
    p.add_argument("--max-results", type=int, default=100, help="0 means unlimited.")
    p.add_argument("--max-checks", type=int, default=5000, help="0 means unlimited.")
    p.add_argument("--db", default="domhunt.sqlite3")
    p.add_argument("--output", default="available.txt")
    p.add_argument("--unknown-output", default="unknown.txt")
    p.add_argument("--user-agent", default="domhunt/0.1 (+local script)")
    p.add_argument(
        "--resume",
        action="store_true",
        help="Skip candidates already present in the SQLite cache.",
    )
    p.add_argument(
        "--retry-unknowns",
        action="store_true",
        help="Allow re-checking candidates previously marked unknown.",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional RNG seed for repeatable random generation.",
    )
    p.add_argument("--verbose", action="store_true")
    p.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable live progress indicator on stderr.",
    )
    p.add_argument(
        "--bell-on-found",
        action="store_true",
        help="Emit a terminal bell when an available domain is found.",
    )
    return p.parse_args(argv)


def should_stop(found: int, checked: int, max_results: int, max_checks: int) -> bool:
    if max_results > 0 and found >= max_results:
        return True
    if max_checks > 0 and checked >= max_checks:
        return True
    return False


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    if args.seed is not None:
        random.seed(args.seed)

    if args.exact_length is not None:
        args.min_length = args.max_length = args.exact_length

    if not (1 <= args.min_length <= args.max_length <= 7):
        print("Lengths must be within 1..7 and min <= max.", file=sys.stderr)
        return 2

    style = STYLE_PRESETS[args.style]
    db = DomainDB(args.db)
    bucket = TokenBucket(rate_per_sec=args.rate, burst=args.burst)
    stream = candidate_stream(args.min_length, args.max_length, style)

    pq: List[PrioritizedCandidate] = []
    unknowns_seen = set()
    found = 0
    checked = 0
    generated = 0
    started = time.monotonic()
    spinner_frames = "|/-\\"
    spinner_index = 0
    progress_enabled = sys.stderr.isatty() and not args.no_progress and not args.verbose
    last_progress_render = 0.0
    progress_interval = 0.15

    def render_progress(in_flight: int, force: bool = False) -> None:
        nonlocal spinner_index, last_progress_render
        if not progress_enabled:
            return
        now = time.monotonic()
        if not force and (now - last_progress_render) < progress_interval:
            return
        elapsed = max(now - started, 0.001)
        rate = checked / elapsed
        spinner = spinner_frames[spinner_index % len(spinner_frames)]
        spinner_index += 1
        line = (
            f"\r{spinner} generated={generated} checked={checked} found={found} "
            f"in_flight={in_flight} queue={len(pq)} rate={rate:.2f}/s"
        )
        width = shutil.get_terminal_size((120, 20)).columns
        clipped = line[: max(1, width - 1)]
        sys.stderr.write(clipped)
        if len(clipped) < width - 1:
            sys.stderr.write(" " * (width - 1 - len(clipped)))
        sys.stderr.flush()
        last_progress_render = now

    def clear_progress_line() -> None:
        if not progress_enabled:
            return
        width = shutil.get_terminal_size((120, 20)).columns
        sys.stderr.write("\r" + (" " * max(1, width - 1)) + "\r")
        sys.stderr.flush()

    def fill_queue() -> None:
        nonlocal generated
        target = max(args.workers * 4, args.top_buffer)
        while len(pq) < target:
            try:
                name, source, score = next(stream)
            except StopIteration:
                return
            prior = db.get_status(name) if args.resume else None
            if prior is not None:
                if prior == "unknown" and args.retry_unknowns:
                    pass
                else:
                    continue
            heapq.heappush(
                pq, PrioritizedCandidate(priority=-score, payload=(name, source, score))
            )
            generated += 1

    def lookup_task(name: str, source: str, score: float):
        bucket.acquire()
        status, http_status, note = rdap_lookup(
            f"{name}.com", args.user_agent, args.timeout
        )
        if status == "unknown":
            time.sleep(0.6 + random.random() * 0.8)
        return name, source, score, status, http_status, note

    fill_queue()
    futures = set()

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        while True:
            fill_queue()
            render_progress(in_flight=len(futures))

            while (
                len(futures) < args.workers
                and pq
                and not should_stop(
                    found, checked + len(futures), args.max_results, args.max_checks
                )
            ):
                item = heapq.heappop(pq)
                name, source, score = item.payload
                futures.add(executor.submit(lookup_task, name, source, score))
                render_progress(in_flight=len(futures))

            if not futures:
                break

            done, futures = concurrent.futures.wait(
                futures, return_when=concurrent.futures.FIRST_COMPLETED
            )
            for fut in done:
                name, source, score, status, http_status, note = fut.result()
                checked += 1
                db.upsert(
                    name=name,
                    status=status,
                    score=score,
                    source=source,
                    http_status=http_status,
                    note=note,
                )

                if status == "available":
                    found += 1
                    print(f"{name}.com")
                    if args.bell_on_found:
                        print("\a", end="", file=sys.stderr, flush=True)
                elif status == "unknown":
                    unknowns_seen.add(name)

                if args.verbose:
                    print(
                        json.dumps(
                            {
                                "name": name,
                                "fqdn": f"{name}.com",
                                "status": status,
                                "http_status": http_status,
                                "score": score,
                                "source": source,
                                "checked": checked,
                                "found": found,
                            }
                        ),
                        file=sys.stderr,
                    )

                if should_stop(found, checked, args.max_results, args.max_checks):
                    break
                render_progress(in_flight=len(futures))

            if should_stop(found, checked, args.max_results, args.max_checks):
                break

    clear_progress_line()
    db.export_status("available", args.output)
    Path(args.unknown_output).write_text(
        "".join(f"{name}.com\n" for name in sorted(unknowns_seen)), encoding="utf-8"
    )

    print("", file=sys.stderr)
    print("done", file=sys.stderr)
    print(f"generated={generated}", file=sys.stderr)
    print(f"checked={checked}", file=sys.stderr)
    print(f"found_available={found}", file=sys.stderr)
    print(f"db={args.db}", file=sys.stderr)
    print(f"available_output={args.output}", file=sys.stderr)
    print(f"unknown_output={args.unknown_output}", file=sys.stderr)
    print(f"stats={json.dumps(db.stats(), sort_keys=True)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
