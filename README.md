# domhunt

Local CLI for finding unregistered `.com` domains using Verisign RDAP.

## What it does

- Exhaustively generates **all** 1, 2, and 3 letter lowercase candidates and checks each one with `.com` RDAP.
- Switches to weighted random generation for 4-7 letters.
- Biases longer names toward short, sayable, brandable patterns rather than dictionary words.
- Writes out **only** domains that appear unregistered.
- Stores all lookup results locally in SQLite so scans can be resumed.
- Uses only the Python standard library for the client itself.

## Behavior

### 1-3 letters

These lengths are exhaustive.

That means:
- no pronounceability filter
- no vowel-structure filter
- every lowercase combination is generated
- every candidate still gets an RDAP lookup

### 4-7 letters

These lengths are discovery-oriented.

The generator uses:
- weighted consonant / vowel / semivowel selection
- a special higher-value role for `y`
- phonetic scoring to favor sayable synthetic names
- heavier sampling for 5 and 6 letter names

## Why rate limiting exists

This script uses Verisign RDAP for live `.com` lookup checks. Even though it is a local script, it still makes automated network requests to a public registry-backed service.

Use conservative defaults unless you have a very good reason not to.

Recommended defaults:
- `--workers 4`
- `--rate 2`
- `--burst 2`

## Repository layout

```text
.
├── domhunt.py
├── README.md
├── pyproject.toml
└── .gitignore
```

## Requirements

- Python 3.10+
- outbound HTTPS access to `https://rdap.verisign.com`

No third-party Python packages are required.

## Quick start

Run directly:

```bash
python3 domhunt.py --resume --style brandable --workers 4 --rate 2 --burst 2 --max-results 100 --max-checks 5000
```

Or install locally as a CLI:

```bash
python3 -m pip install -e .
domhunt --resume --style brandable --workers 4 --rate 2 --burst 2 --max-results 100 --max-checks 5000
```

## Common commands

### Exhaustive 1-3 letters only

```bash
python3 domhunt.py --min-length 1 --max-length 3 --resume --max-results 0 --max-checks 0
```

### Focus only on 5-letter brandables

```bash
python3 domhunt.py --exact-length 5 --style brandable --resume --rate 2 --workers 4 --max-results 250 --max-checks 10000
```

### Focus only on 6-letter soft names

```bash
python3 domhunt.py --exact-length 6 --style soft --resume --rate 2 --workers 4 --max-results 250 --max-checks 10000
```

### Focus only on sharper tech-ish names

```bash
python3 domhunt.py --min-length 5 --max-length 6 --style hard-tech --resume --rate 2 --workers 4 --max-results 250 --max-checks 10000
```

### Re-check unknowns from prior runs

```bash
python3 domhunt.py --resume --retry-unknowns --max-results 0 --max-checks 2000
```

## Outputs

### Standard output

Every newly discovered available domain is printed immediately:

```text
abq.com
xoi.com
neyra.com
```

### Standard error

- Live spinner/progress line is shown by default when stderr is a TTY.
- Use `--no-progress` to disable it.
- Use `--bell-on-found` to ring the terminal bell each time an available domain is found.

### Files

- `domhunt.sqlite3` — local cache of all checked names and statuses
- `available.txt` — all cached domains currently marked `available`
- `unknown.txt` — names that produced ambiguous or retry-worthy responses in the current run

## Options

```text
--min-length INT       Minimum length to scan, default 1
--max-length INT       Maximum length to scan, default 7
--exact-length INT     Shortcut that sets both min and max length
--style NAME           brandable | soft | hard-tech
--workers INT          Concurrent worker threads, default 4
--rate FLOAT           Global RDAP requests per second, default 2.0
--burst INT            Token bucket burst capacity, default 2
--timeout FLOAT        Per-request timeout in seconds, default 8.0
--top-buffer INT       Queue depth for 4-7 letter candidates, default 400
--max-results INT      Stop after this many available domains; 0 = unlimited
--max-checks INT       Stop after this many RDAP lookups; 0 = unlimited
--db PATH              SQLite cache path, default domhunt.sqlite3
--output PATH          Available output path, default available.txt
--unknown-output PATH  Unknown output path, default unknown.txt
--user-agent TEXT      User-Agent header for RDAP requests
--resume               Skip names already cached
--retry-unknowns       Re-run names previously marked unknown
--seed INT             Optional RNG seed for repeatable random generation
--verbose              Emit JSON progress records to stderr
--no-progress          Disable live progress indicator on stderr
--bell-on-found        Emit terminal bell for each available domain
```

## Notes on interpretation

- A name is only written to `available.txt` when the lookup path returns a clear not-found style result that the script maps to `available`.
- Ambiguous results are stored as `unknown` instead of being treated as available.
- This tool assumes lowercase ASCII letters `a-z` only.
- It does **not** attempt to search digits, hyphens, IDNs, or other TLDs.
