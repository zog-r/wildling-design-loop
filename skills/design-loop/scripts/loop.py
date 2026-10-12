#!/usr/bin/env python3
"""design-loop: deterministic core. State, seeds, screenshots, critic calls, decisions, manifest."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import secrets
import shutil
import string
import subprocess
import sys
import tempfile
import time
import urllib.parse
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
RUN = Path.cwd() / ".design-loop"
SUBDIRS = ("seeds", "variants", "shots", "critiques")
DEFAULTS = {"inspiration": "", "moodboard": [], "constraints": "", "mood": [], "audience": "",
            "directions": 3, "max_iters": 6, "critic_model": "fable", "pass_grade": 9, "polish": True,
            "enrich": "images", "media_model": "claude-opus-5-5", "settle_ms": 8000}
ENRICH = ("images", "images+video", "off")
PLATEAU = 3
TOKEN_LOSS_MAX = 0.10
UPSCALE_MAX = 1.3
AUTH_RE = re.compile(r"\b401\b|authenticat|invalid api key|not logged in|/login|oauth token", re.I)
SECRET_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}|\bAIza[0-9A-Za-z_-]{30,}|\bfal-[A-Za-z0-9_-]{16,}")
ENV_FILE = ".env.agents"
AAW = Path.home() / ".claude/skills/avoid-ai-writing/bin/avoid-ai-writing.js"


class LoopError(Exception):
    pass


# ---------- brief ----------

def validate_brief(raw: dict, base: Path) -> dict:
    b = {**DEFAULTS, **raw}
    for key in ("target", "purpose", "must_keep"):
        if not b.get(key):
            raise LoopError(f"brief: '{key}' is required")
    if b["target"] != "new":
        t = (base / b["target"]).resolve()
        if not t.is_file():
            raise LoopError(f"brief: target {t} not found (use \"new\" for a new page)")
        b["target"] = str(t)
    elif not b.get("output_file"):
        raise LoopError("brief: 'output_file' is required when target is \"new\"")
    for key in ("directions", "max_iters"):
        if not isinstance(b[key], int) or b[key] < 1:
            raise LoopError(f"brief: '{key}' must be an integer >= 1")
    if not isinstance(b["settle_ms"], int) or b["settle_ms"] < 0:
        raise LoopError("brief: 'settle_ms' must be an integer >= 0 (0 disables settled screenshots)")
    if not isinstance(b["pass_grade"], (int, float)) or not 0 < b["pass_grade"] <= 10:
        raise LoopError("brief: 'pass_grade' must be in (0, 10]")
    if b["enrich"] not in ENRICH:
        raise LoopError(f"brief: 'enrich' must be one of {', '.join(ENRICH)}")
    if not isinstance(b["moodboard"], list):
        raise LoopError("brief: 'moodboard' must be a list of image paths")
    b["moodboard"] = [str((base / m).resolve()) for m in b["moodboard"]]
    for m in b["moodboard"]:
        if not Path(m).is_file():
            raise LoopError(f"brief: moodboard image {m} not found")
    if b.get("output_file"):
        b["output_file"] = str((base / b["output_file"]).resolve())
    return b


def load(run: Path, name: str) -> dict:
    return json.loads((run / name).read_text())


def save(run: Path, name: str, data: dict) -> None:
    (run / name).write_text(json.dumps(data, indent=2) + "\n")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(run: Path, entry: dict) -> None:
    with open(run / "log.jsonl", "a") as f:
        f.write(json.dumps(entry) + "\n")


def read_env(path: Path) -> dict:
    """KEY=value lines, '#' comments; blank values are dropped (blank = provider off)."""
    env = {}
    if not path.is_file():
        return env
    for line in path.read_text().splitlines():
        line = line.split(" #", 1)[0].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip("'\"")
        if v:
            env[k.strip()] = v
    return env


# ---------- state / manifest ----------

def write_state(run: Path, st: dict) -> None:
    save(run, "state.json", st)
    save(run, "manifest.json", {"iterations": st["iterations"], "winner": st.get("best"),
                                "stop_reason": st.get("stop_reason")})


def record(st: dict, id_: str, phase: str, shots: list, score, verdict, note: str) -> None:
    st["iterations"].append({"id": id_, "phase": phase, "html": f"variants/{id_}.html", "shots": shots,
                             "score": score, "verdict": verdict, "note": note[:200], "ts": now()})


# ---------- parsing ----------

def parse_envelope(stdout: str) -> dict:
    """claude -p --output-format json -> {result, tokens, cost_usd, is_error}."""
    data = json.loads(stdout)
    if isinstance(data, list):
        data = next(d for d in reversed(data) if d.get("type") == "result")
    u = data.get("usage") or {}
    tokens = sum(int(u.get(k) or 0) for k in ("input_tokens", "output_tokens",
                                               "cache_creation_input_tokens", "cache_read_input_tokens"))
    return {"result": data.get("result") or "", "tokens": tokens,
            "cost_usd": float(data.get("total_cost_usd") or 0), "is_error": bool(data.get("is_error"))}


def parse_score(text: str):
    m = re.findall(r"SCORE:\s*(\d+(?:\.\d)?)\s*/\s*10", text)
    if not m:
        return None
    s = float(m[-1])
    return s if 0 <= s <= 10 else None


def parse_winner(text: str):
    m = re.findall(r"WINNER:\s*\**\s*([AB])\b", text)
    return m[-1] if m else None


def first_line(text: str, prefix: str) -> str:
    for line in text.splitlines():
        line = line.strip().strip("`*")
        if line.upper().startswith(prefix + ":"):
            return line.split(":", 1)[1].strip().strip("`*")
    return next((ln.strip() for ln in text.splitlines() if ln.strip()), "")


# ---------- critic ----------

def run_claude(model: str, prompt: str, cwd: Path) -> str:
    # "--allowedTools=Read" (not two args): the flag is variadic and would swallow the prompt.
    cmd = ["claude", "-p", "--model", model, "--output-format", "json",
           # ponytail: lean flags skip the global config load (~100k tokens/call -> ~1k)
           "--setting-sources=project", "--disable-slash-commands", "--strict-mcp-config", "--tools=Read", "--system-prompt=You are a design critic. Use the Read tool to view the image files you are given.", "--allowedTools=Read", "--", prompt]
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=900)
    if p.returncode != 0 and not p.stdout.strip():
        raise LoopError(f"claude -p failed ({p.returncode}): {p.stderr.strip()[:500]}")
    return p.stdout


CALL = run_claude  # swapped in selftest


def critic(run: Path, step: str, prompt: str, parse, model: str | None = None, cwd: Path | None = None) -> tuple:
    brief = load(run, "brief.json")
    for attempt in (1, 2):
        t0 = time.monotonic()
        out = CALL(model or brief["critic_model"], prompt, cwd or run / "shots")
        ms = int((time.monotonic() - t0) * 1000)
        try:
            env = parse_envelope(out)
        except (ValueError, StopIteration):
            env = {"result": out, "tokens": 0, "cost_usd": 0.0, "is_error": True}
        log(run, {"step": step, "kind": "critic", "tokens": env["tokens"], "cost_usd": env["cost_usd"], "ms": ms,
                  "images": [ln for ln in prompt.splitlines() if ln.startswith("/")]})
        if env["is_error"] and AUTH_RE.search(env["result"]):
            raise LoopError(f"AUTH ERROR from claude -p: {env['result'][:300]}")
        value = parse(env["result"])
        if value is not None:
            return value, env["result"]
        print(f"critic output unparseable (attempt {attempt})", file=sys.stderr)
    raise LoopError(f"critic output unparseable twice for {step}")


def image_block(label: str, paths: list, run: Path) -> str:
    return f"{label}\n" + "\n".join(str((run / p).resolve()) for p in paths)


def moodboard_block(run: Path) -> str:
    mb = load(run, "brief.json")["moodboard"]
    return "\n\nMoodboard images:\n" + "\n".join(mb) if mb else ""


def score_call(run: Path, id_: str, shots: list) -> tuple:
    prompt = ((SKILL / "critic-prompt.md").read_text() + "\n" + image_block("Screenshots:", shots, run)
              + moodboard_block(run))
    score, text = critic(run, id_, prompt, parse_score)
    (run / "critiques" / f"{id_}.md").write_text(text)
    return score, text


def compare_call(run: Path, cand: str, cand_shots: list, other: str, other_shots: list) -> tuple:
    """Returns (cand_won, reason). Order randomized to avoid position bias."""
    flip = secrets.randbelow(2) == 1
    a, b = ((other, other_shots), (cand, cand_shots)) if flip else ((cand, cand_shots), (other, other_shots))
    prompt = ((SKILL / "compare-prompt.md").read_text() + "\n" + image_block("Design A:", a[1], run) + "\n\n"
              + image_block("Design B:", b[1], run) + moodboard_block(run))
    winner, text = critic(run, f"{cand}-vs-{other}", prompt, parse_winner)
    (run / "critiques" / f"{cand}-vs-{other}.md").write_text(f"A={a[0]} B={b[0]}\n{text}")
    return (a[0] if winner == "A" else b[0]) == cand, first_line(text, "REASON")


# ---------- screenshots ----------

def shot(run: Path, id_: str) -> list:
    html = run / "variants" / f"{id_}.html"
    if not html.is_file():
        raise LoopError(f"{html} not found")
    body = html.read_text(errors="ignore")
    for seed in (run / "seeds").glob("*.txt"):
        if seed.read_text().strip() in body:
            raise LoopError(f"{html} contains the seed from {seed.name}; the seed must never appear in the design")
    for old in [*(run / "shots").glob(f"{id_}-*.png"), *(run / "shots").glob(f"{id_}-*.json")]:
        old.unlink(missing_ok=True)
    settle = load(run, "brief.json").get("settle_ms", 8000)
    p = subprocess.run(["npx", "-y", "-p", "playwright@1.63.0", "node", str(SKILL / "scripts" / "shot.mjs"),
                        str(html.resolve()), str((run / "shots").resolve()), id_, str(settle)],
                       capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        raise LoopError(f"screenshot failed: {p.stderr.strip()[-800:]}")
    paths = json.loads(p.stdout.strip().splitlines()[-1])
    return [f"shots/{Path(x).name}" for x in paths]


SHOT = shot  # swapped in selftest


def tells(run: Path, id_: str) -> list:
    """Banned AI design tells shot.mjs measured on this variant (see tells.mjs). Any hit rejects the page."""
    side = run / "shots" / f"{id_}-tells.json"
    hits = json.loads(side.read_text()) if side.is_file() else []
    return [f"{h['tell']}: {h['where']}" + (f" ({h['why']})" if h.get("why") else "") for h in hits]


def intrinsic_width(path: Path) -> int | None:
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=60)
    w = p.stdout.strip().split("\n")[0]
    return int(w) if p.returncode == 0 and w.isdigit() else None


def upscaled(run: Path, id_: str) -> list:
    """Assets under variants/assets/ drawn wider than UPSCALE_MAX x their own pixels (from shot.mjs's sidecar).
    CSS can't fix a blurry upscale; the fix is a bigger source or a smaller box."""
    side = run / "shots" / f"{id_}-media.json"
    if not side.is_file() or not shutil.which("ffprobe"):
        return []
    assets = (run / "variants" / "assets").resolve()
    warnings = []
    for m in {(m["src"], m["width"]): m for m in json.loads(side.read_text())}.values():
        u = urllib.parse.urlparse(m["src"])
        path = Path(urllib.parse.unquote(u.path)).resolve()
        if u.scheme != "file" or not path.is_relative_to(assets) or not path.is_file():
            continue
        iw = intrinsic_width(path)
        if iw and m["width"] > UPSCALE_MAX * iw:
            warnings.append(f"assets/{path.relative_to(assets)}: {iw}px wide, shown at {m['width']}px "
                            f"({m['width'] / iw:.1f}x upscale); use a larger source or a smaller box")
    return warnings


# ---------- integrity ----------

class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.heads, self._skip, self._head = [], [], 0, None

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in ("h1", "h2", "h3"):
            self._head = []

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in ("h1", "h2", "h3") and self._head is not None:
            self.heads.append(" ".join(" ".join(self._head).split()))
            self._head = None

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)
            if self._head is not None:
                self._head.append(data)


MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"


def numbers(text: str) -> set:
    return set(re.findall(r"\d+(?:[.:,]\d+)*", text))


def dates(text: str) -> set:
    t = text.lower()
    found = {f"{m[:3]} {int(d)}" for m, d in re.findall(rf"\b({MONTHS})[a-z]*\.?\s+(\d{{1,2}})\b", t)}
    return found | set(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", t))


def extract(html: str) -> dict:
    p = _Text()
    p.feed(html)
    text = " ".join(" ".join(p.parts).split())
    return {"text": text, "tokens": set(re.findall(r"[a-z0-9]+", text.lower())),
            "numbers": numbers(text), "dates": dates(text), "headings": p.heads}


def flatten(v) -> str:
    if isinstance(v, dict):
        return " ".join(flatten(x) for x in v.values())
    if isinstance(v, list):
        return " ".join(flatten(x) for x in v)
    return str(v)


def leaked(html: str, env_values) -> list:
    hits = SECRET_RE.findall(html) + [v for v in env_values if len(v) >= 12 and v in html]
    return sorted({h[:6] + "…" for h in hits})


def integrity(ref_html: str, cand_html: str, must_keep=None, env_values=()) -> dict:
    ref, cand = extract(ref_html), extract(cand_html)
    if must_keep is None:
        keep_nums, keep_dates = ref["numbers"], ref["dates"]
    else:
        mk = flatten(must_keep)
        keep_nums, keep_dates = numbers(mk), dates(mk)
    missing_nums = sorted(keep_nums - cand["numbers"])
    missing_dates = sorted(keep_dates - cand["dates"])
    lost = ref["tokens"] - cand["tokens"]
    loss = len(lost) / len(ref["tokens"]) if ref["tokens"] else 0.0
    reasons = []
    if missing_nums:
        reasons.append("missing numbers " + ", ".join(missing_nums))
    if missing_dates:
        reasons.append("missing dates " + ", ".join(missing_dates))
    if loss > TOKEN_LOSS_MAX:
        reasons.append(f"{loss:.0%} of reference text tokens missing")
    secrets_found = leaked(cand_html, env_values)
    if secrets_found:
        reasons.append("API key in page (" + ", ".join(secrets_found) + "); keys stay in .env.agents")
    return {"ok": not reasons, "reason": "; ".join(reasons), "token_loss": round(loss, 3),
            "missing_numbers": missing_nums, "missing_dates": missing_dates,
            "missing_headings": [h for h in ref["headings"] if h not in cand["headings"]]}


# ---------- decisions ----------

def accept(cand_won: bool, cand_score: float, best_score: float) -> bool:
    return cand_won and cand_score >= best_score


def decide(best_score, rounds: int, stale: int, pass_grade: float, max_iters: int) -> str:
    if best_score is not None and best_score >= pass_grade:
        return "PASS"
    if rounds >= max_iters:
        return "STOP_MAX"
    if stale >= PLATEAU:
        return "STOP_PLATEAU"
    return "CONTINUE"


def knockout(ids: list, compare) -> tuple:
    """A vs B, winner vs C, ... compare(challenger, champion) -> (challenger_won, reason)."""
    champ, notes = ids[0], {}
    for challenger in ids[1:]:
        won, reason = compare(challenger, champ)
        loser = champ if won else challenger
        notes[loser] = reason
        champ = challenger if won else champ
    return champ, notes


# ---------- commands ----------

def cmd_init(run: Path, brief_path: str) -> None:
    src = Path(brief_path).resolve()
    b = validate_brief(json.loads(src.read_text()), Path.cwd())
    if run.exists() and (run / "state.json").exists():
        raise LoopError(f"{run} already holds a run; move it away first")
    for d in SUBDIRS:
        (run / d).mkdir(parents=True, exist_ok=True)
    save(run, "brief.json", b)
    st = {"iterations": [], "best": None, "best_score": None, "reference": None,
          "rounds": 0, "stale": 0, "stop_reason": None}
    if b["target"] != "new":
        shutil.copyfile(b["target"], run / "variants" / "v0.html")
        st["reference"] = "variants/v0.html"
    write_state(run, st)
    print(f"initialized {run} (directions={b['directions']}, max_iters={b['max_iters']}, "
          f"pass_grade={b['pass_grade']}, polish={b['polish']}, critic={b['critic_model']})")


def new_seed() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(64))


def cmd_seed(run: Path, id_: str | None) -> None:
    if id_ is None:
        id_ = f"d{len(list((run / 'seeds').glob('d*.txt'))) + 1}"
    s = new_seed()
    (run / "seeds" / f"{id_}.txt").write_text(s + "\n")
    print(s)


def discover_ids(run: Path) -> list:
    ids = [p.stem for p in (run / "variants").glob("d*.html") if re.fullmatch(r"d\d+", p.stem)]
    return sorted(ids, key=lambda x: int(x[1:]))


def shots_for(run: Path, id_: str) -> list:
    for it in reversed(load(run, "state.json")["iterations"]):
        if it["id"] == id_:
            return it["shots"]
    existing = sorted((run / "shots").glob(f"{id_}-*.png"), key=_shot_order)
    if existing:
        return [f"shots/{p.name}" for p in existing]
    return SHOT(run, id_)


def _shot_order(p: Path):
    _, device, part = p.stem.split("-", 2)
    return (device != "desktop", {"fold": 0, "settled": 1}.get(part, 2), part)


def cmd_pick(run: Path, winner: str, notes: dict | None = None) -> None:
    st = load(run, "state.json")
    if st["best"]:
        raise LoopError("discover already picked")
    ids = discover_ids(run)
    if winner not in ids:
        raise LoopError(f"{winner} not in discover variants {ids}")
    shots = {i: shots_for(run, i) for i in ids}
    if tells(run, winner):
        raise LoopError(f"{winner} has banned AI tells: " + "; ".join(tells(run, winner)))
    score, text = score_call(run, winner, shots[winner])
    for i in ids:
        if i == winner:
            record(st, i, "discover", shots[i], score, "win", first_line(text, "GAP"))
        else:
            record(st, i, "discover", shots[i], None, "loss", (notes or {}).get(i, "not picked"))
    st.update(best=winner, best_score=score)
    if st["reference"] is None:
        st["reference"] = f"variants/{winner}.html"
    write_state(run, st)
    print(json.dumps({"best": winner, "best_score": score, "critique": f"critiques/{winner}.md"}))


def cmd_judge(run: Path) -> None:
    ids = discover_ids(run)
    if not ids:
        raise LoopError("no variants/d*.html to judge")
    shots = {i: shots_for(run, i) for i in ids}
    banned = {i: tells(run, i) for i in ids if tells(run, i)}
    if len(banned) == len(ids):
        raise LoopError("every direction has banned AI tells; fix them and re-run `shot`: " + json.dumps(banned))
    winner, notes = knockout([i for i in ids if i not in banned],
                             lambda c, o: compare_call(run, c, shots[c], o, shots[o]))
    notes.update({i: "tells: " + "; ".join(t) for i, t in banned.items()})
    cmd_pick(run, winner, notes)


def cmd_round(run: Path, src: str, phase: str) -> None:
    st, b = load(run, "state.json"), load(run, "brief.json")
    if not st["best"]:
        raise LoopError("run pick/judge first")
    id_ = f"v{sum(1 for it in st['iterations'] if it['phase'] != 'discover') + 1}"
    shutil.copyfile(src, run / "variants" / f"{id_}.html")
    cand_html = (run / "variants" / f"{id_}.html").read_text(errors="ignore")
    chk = integrity((run / st["reference"]).read_text(errors="ignore"), cand_html, b["must_keep"],
                    read_env(Path.cwd() / ENV_FILE).values())
    counted = phase == "define"
    warnings = []
    if not chk["ok"]:
        log(run, {"step": id_, "kind": "integrity", "ok": False, "reason": chk["reason"]})
        record(st, id_, phase, [], None, "loss", "integrity: " + chk["reason"])
        won, score = False, None
    else:
        shots = SHOT(run, id_)
        banned = tells(run, id_)
        chk["tells"] = banned
        if banned:
            log(run, {"step": id_, "kind": "tells", "hits": banned})
            record(st, id_, phase, shots, None, "loss", "tells: " + "; ".join(banned))
            won, score = False, None
        else:
            warnings = upscaled(run, id_)
            if warnings:
                log(run, {"step": id_, "kind": "resolution", "warnings": warnings})
            score, text = score_call(run, id_, shots)
            best_won, reason = compare_call(run, id_, shots, st["best"], shots_for(run, st["best"]))
            won = accept(best_won, score, st["best_score"])
            note = first_line(text, "GAP") if won else f"lost to {st['best']}: {reason}"
            record(st, id_, phase, shots, score, "win" if won else "loss", note)
    if won:
        st.update(best=id_, best_score=score)
    if counted:
        st["rounds"] += 1
        st["stale"] = 0 if won else st["stale"] + 1
    write_state(run, st)
    print(json.dumps({"id": id_, "verdict": "win" if won else "loss", "score": score,
                      "integrity": chk, "resolution": warnings, "best": st["best"], "best_score": st["best_score"],
                      "next_from": f"variants/{st['best']}.html", "critique": f"critiques/{st['best']}.md"}))


def cmd_decide(run: Path) -> None:
    st, b = load(run, "state.json"), load(run, "brief.json")
    d = decide(st["best_score"], st["rounds"], st["stale"], b["pass_grade"], b["max_iters"])
    if d != "CONTINUE":
        st["stop_reason"] = d
        write_state(run, st)
    print(d)


def cmd_finish(run: Path) -> None:
    st, b = load(run, "state.json"), load(run, "brief.json")
    if not st["best"]:
        raise LoopError("nothing to finish")
    if not st["stop_reason"]:
        raise LoopError("run `decide` until it returns a stop decision first")
    if tells(run, st["best"]):
        raise LoopError(f"{st['best']} has banned AI tells; win a round without them first: "
                        + "; ".join(tells(run, st["best"])))
    dest = Path(b.get("output_file") or b["target"])
    shutil.copyfile(run / "variants" / f"{st['best']}.html", dest)
    assets = run / "variants" / "assets"
    if assets.is_dir():
        shutil.copytree(assets, dest.parent / "assets", dirs_exist_ok=True)
    write_state(run, st)
    print(json.dumps({"winner": st["best"], "score": st["best_score"], "stop_reason": st["stop_reason"],
                      "output": str(dest)}))


def cmd_status(run: Path) -> None:
    st = load(run, "state.json")
    print(json.dumps({k: st[k] for k in ("best", "best_score", "reference", "rounds", "stale", "stop_reason")}))


def cmd_tells(target: str) -> int:
    if not re.match(r"(https?|file):", target):
        target = str(Path(target).resolve())
    p = subprocess.run(["npx", "-y", "-p", "playwright@1.63.0", "node", str(SKILL / "scripts" / "tells.mjs"), target],
                       capture_output=True, text=True, timeout=120)
    print(p.stdout.strip() or p.stderr.strip()[-800:])
    return p.returncode


def cmd_integrity(ref: str, cand: str, brief: str | None) -> int:
    mk = None
    bp = Path(brief) if brief else RUN / "brief.json"
    if bp.is_file():
        mk = json.loads(bp.read_text()).get("must_keep")
    r = integrity(Path(ref).read_text(errors="ignore"), Path(cand).read_text(errors="ignore"), mk,
                  read_env(Path.cwd() / ENV_FILE).values())
    print(json.dumps(r))
    return 0 if r["ok"] else 1


# ---------- copy ----------

class _Blocks(_Text):
    BLOCK = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "div", "section", "article", "header", "footer",
             "nav", "button", "td", "th", "figcaption", "blockquote", "label", "br", "dt", "dd", "summary"}

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        super().handle_endtag(tag)
        if tag in self.BLOCK:
            self.parts.append("\n")


def copy_blocks(html: str) -> list:
    p = _Blocks()
    p.feed(html)
    lines = (" ".join(ln.split()) for ln in "".join(p.parts).split("\n"))
    return list(dict.fromkeys(ln for ln in lines if ln))


def detect(text: str) -> dict:
    """avoid-ai-writing detector; available=False when the skill isn't installed."""
    if not AAW.is_file():
        return {"available": False, "score": None, "issues": []}
    p = subprocess.run(["node", str(AAW), "--context", "marketing"], input=text,
                       capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise LoopError(f"avoid-ai-writing failed: {p.stderr.strip()[:300]}")
    d = json.loads(p.stdout)
    return {"available": True, "score": d.get("score"), "issues": d.get("issues") or []}


DETECT = detect  # swapped in selftest


def cmd_copy(run: Path, html: str, baseline: str | None) -> int:
    blocks = copy_blocks(Path(html).read_text(errors="ignore"))
    run.mkdir(exist_ok=True)
    (run / "copy.md").write_text("\n\n".join(blocks) + "\n")
    r = DETECT("\n\n".join(blocks))
    out = {"copy": str(run / "copy.md"), "blocks": len(blocks), **r}
    if baseline and r["available"]:
        base = DETECT("\n\n".join(copy_blocks(Path(baseline).read_text(errors="ignore"))))
        out["baseline_issues"] = len(base["issues"])
        out["ok"] = len(r["issues"]) <= len(base["issues"])
    print(json.dumps(out))
    return 0 if out.get("ok", True) else 1


# ---------- selftest ----------

def selftest() -> None:
    global CALL, SHOT, DETECT
    # parsing
    env = parse_envelope(json.dumps({"type": "result", "result": "x\nSCORE: 7.5/10", "is_error": False,
                                     "total_cost_usd": 0.12, "usage": {"input_tokens": 10, "output_tokens": 5,
                                     "cache_creation_input_tokens": 100, "cache_read_input_tokens": 1000}}))
    assert env["tokens"] == 1115 and env["cost_usd"] == 0.12 and not env["is_error"]
    assert parse_envelope(json.dumps([{"type": "system"}, {"type": "result", "result": "r"}]))["result"] == "r"
    assert parse_score("SCORE: 7/10") == 7.0
    assert parse_score("SCORE: 3/10 early\n...\nSCORE: 8.5 / 10") == 8.5
    assert parse_score("score maybe 8") is None and parse_score("SCORE: 11/10") is None
    assert parse_winner("WINNER: B\nREASON: tighter grid") == "B"
    assert parse_winner("**WINNER: A**") == "A" and parse_winner("A is better") is None
    assert first_line("GAP: hero is weak\n- x", "GAP") == "hero is weak"
    assert first_line("no prefix here\nmore", "GAP") == "no prefix here"
    assert AUTH_RE.search("API Error: 401 {\"type\":\"authentication_error\"}")
    assert not AUTH_RE.search("SCORE: 4/10")
    # decisions: every branch, PASS precedence
    assert decide(9.0, 1, 0, 9, 6) == "PASS"
    assert decide(9.5, 6, 3, 9, 6) == "PASS"
    assert decide(8.0, 6, 0, 9, 6) == "STOP_MAX"
    assert decide(8.0, 4, 3, 9, 6) == "STOP_PLATEAU"
    assert decide(8.0, 2, 2, 9, 6) == "CONTINUE"
    assert decide(None, 0, 0, 9, 6) == "CONTINUE"
    # acceptance: needs pairwise win AND score >= best
    assert accept(True, 8.0, 8.0) and accept(True, 8.5, 8.0)
    assert not accept(True, 7.9, 8.0) and not accept(False, 9.5, 8.0)
    # knockout: A vs B, winner vs C
    order = []

    def cmp(c, o):
        order.append((c, o))
        return c == "d2", "why"
    assert knockout(["d1", "d2", "d3"], cmp) == ("d2", {"d1": "why", "d3": "why"})
    assert order == [("d2", "d1"), ("d3", "d2")]
    assert knockout(["d1"], cmp)[0] == "d1"
    # integrity
    ref = ("<html><head><style>.x{}</style><script>var hidden=1</script></head><body>"
           "<h1>Frost &amp; Fireside</h1><p>Saturday, December 19, 2026 at 4:00 PM</p>"
           "<p>88 Birch Hollow Road bring a dish or drink to share</p></body></html>")
    mk = {"date": "Saturday, December 19, 2026", "time": "4:00 PM", "location": "88 Birch Hollow Road"}
    ex = extract(ref)
    assert "hidden" not in ex["tokens"] and ex["headings"] == ["Frost & Fireside"]
    assert {"19", "2026", "4:00", "88"} <= ex["numbers"] and "dec 19" in ex["dates"]
    assert integrity(ref, ref, mk)["ok"]
    reordered = ("<main><p>88 Birch Hollow Road</p><p>Frost &amp; Fireside</p>"
                 "<p>Saturday, December 19, 2026 at 4:00 PM, bring a dish or drink to share</p></main>")
    r = integrity(ref, reordered, mk)
    assert r["ok"] and r["missing_headings"] == ["Frost & Fireside"]
    r = integrity(ref, ref.replace("4:00", "4"), mk)
    assert not r["ok"] and r["missing_numbers"] == ["4:00"]
    r = integrity(ref, ref.replace("December 19", "Dec. 19"), mk)
    assert r["ok"], r
    r = integrity(ref, ref.replace("December 19", "November 19"), mk)
    assert not r["ok"] and r["missing_dates"] == ["dec 19"]
    r = integrity(ref, ref.replace("bring a dish or drink to share", ""), mk)
    assert not r["ok"] and "tokens missing" in r["reason"]
    assert not integrity(ref, ref.replace("88", "89"), None)["ok"]
    # secrets never ship
    key = "sk-proj-" + "a1B2" * 8
    r = integrity(ref, ref.replace("</body>", f"<script>const k='{key}'</script></body>"), mk)
    assert not r["ok"] and "API key" in r["reason"] and key not in r["reason"]
    r = integrity(ref, ref.replace("</body>", "<i data-k='tok_0123456789abcdef'></i></body>"), mk,
                  ["tok_0123456789abcdef", "short"])
    assert not r["ok"] and "API key" in r["reason"]
    assert integrity(ref, ref, mk, ["short"])["ok"]
    # env parsing: blank = off, comments stripped
    with tempfile.TemporaryDirectory() as td:
        ep = Path(td) / ".env.agents"
        ep.write_text("# c\nOPENAI_AUTH=subscription   # sub | api\nOPENAI_API_KEY=\nFAL_KEY='abc'\nBAD LINE\n")
        assert read_env(ep) == {"OPENAI_AUTH": "subscription", "FAL_KEY": "abc"}
        assert read_env(Path(td) / "missing") == {}
    # copy blocks
    page = ("<nav><a>Home</a> <a>Pricing</a></nav><h1>Stop <em>guessing</em></h1><p>Your logs say 1.4s.</p>"
            "<script>x()</script><ul><li>One</li><li>One</li></ul><button>Start free</button>")
    assert copy_blocks(page) == ["Home Pricing", "Stop guessing", "Your logs say 1.4s.", "One", "Start free"]
    # seed
    s = new_seed()
    assert len(s) == 64 and s.isalnum() and s.isascii()
    # brief validation
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        b = validate_brief({"target": "new", "purpose": "p", "must_keep": {"a": 1}, "output_file": "o.html"}, base)
        assert b["directions"] == 3 and b["max_iters"] == 6 and b["critic_model"] == "fable"
        assert b["pass_grade"] == 9 and b["polish"] is True
        assert b["enrich"] == "images" and b["media_model"] == "claude-opus-5-5" and b["settle_ms"] == 8000
        for bad in ({"purpose": "p", "must_keep": 1}, {"target": "new", "purpose": "p", "must_keep": 1},
                    {"target": "new", "purpose": "p", "must_keep": 1, "output_file": "o", "enrich": "video"},
                    {"target": "missing.html", "purpose": "p", "must_keep": 1},
                    {"target": "new", "purpose": "p", "must_keep": 1, "output_file": "o", "directions": 0},
                    {"target": "new", "purpose": "p", "must_keep": 1, "output_file": "o", "settle_ms": -1}):
            try:
                validate_brief(bad, base)
                raise AssertionError(f"accepted bad brief {bad}")
            except LoopError:
                pass
    names = ["d1-mobile-01", "d1-desktop-02", "d1-mobile-fold", "d1-desktop-settled", "d1-desktop-01",
             "d1-desktop-fold", "d1-mobile-settled"]
    assert [n for n in sorted(names, key=lambda n: _shot_order(Path(n + ".png")))] == [
        "d1-desktop-fold", "d1-desktop-settled", "d1-desktop-01", "d1-desktop-02",
        "d1-mobile-fold", "d1-mobile-settled", "d1-mobile-01"]
    # end-to-end flow with fake critic + fake screenshots
    replies = []

    def fake_call(model, prompt, cwd):
        text = replies.pop(0)
        return text if text.startswith("{") else json.dumps(
            {"type": "result", "result": text, "is_error": False, "total_cost_usd": 0.01,
             "usage": {"input_tokens": 1, "output_tokens": 1}})

    def fake_shot(run, id_):
        return [f"shots/{id_}-desktop-fold.png"]

    old = CALL, SHOT
    CALL, SHOT = fake_call, fake_shot
    try:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            run = base / ".design-loop"
            (base / "brief.json").write_text(json.dumps(
                {"target": "new", "purpose": "p", "must_keep": mk, "output_file": str(base / "out.html"),
                 "max_iters": 2, "polish": True}))
            cwd = os.getcwd()
            os.chdir(base)
            quiet = contextlib.ExitStack()
            quiet.enter_context(contextlib.redirect_stdout(io.StringIO()))
            quiet.enter_context(contextlib.redirect_stderr(io.StringIO()))
            try:
                cmd_init(run, "brief.json")
                for i in (1, 2):
                    (run / "variants" / f"d{i}.html").write_text(ref)
                replies[:] = ["WINNER: A\nREASON: bolder", "GAP: hero\nSCORE: 6/10"]
                real = secrets.randbelow
                secrets.randbelow = lambda n: 0  # candidate/challenger always shown as A
                try:
                    cmd_judge(run)
                    st = load(run, "state.json")
                    assert st["best"] == "d2" and st["best_score"] == 6.0
                    cand = base / "cand.html"
                    cand.write_text(ref.replace("4:00", "four"))
                    cmd_round(run, str(cand), "define")  # integrity fail, no critic call
                    cand.write_text(ref)
                    garbage = json.dumps({"type": "result", "result": "no score", "is_error": False})
                    replies[:] = [garbage, "GAP: type\nSCORE: 7/10", "WINNER: A\nREASON: x"]
                    cmd_round(run, str(cand), "define")
                    cmd_decide(run)
                    (run / "shots" / "v3-tells.json").write_text(json.dumps(
                        [{"tell": "accent-emphasis", "where": "<em> in <h1>", "why": "color+italic"}]))
                    cmd_round(run, str(cand), "deliver")  # tells -> loss, no critic call
                    replies[:] = ["GAP: less\nSCORE: 9/10", "WINNER: B\nREASON: best still better"]
                    cmd_round(run, str(cand), "deliver")
                    (run / "variants" / "assets").mkdir()
                    (run / "variants" / "assets" / "hero.png").write_bytes(b"png")
                    cmd_finish(run)
                    assert (base / "assets" / "hero.png").read_bytes() == b"png"
                finally:
                    secrets.randbelow = real
                st = load(run, "state.json")
                assert st["rounds"] == 2 and st["stop_reason"] == "STOP_MAX" and st["best"] == "v2"
                m = load(run, "manifest.json")
                assert m["winner"] == "v2" and m["stop_reason"] == "STOP_MAX"
                assert [(i["id"], i["phase"], i["verdict"]) for i in m["iterations"]] == [
                    ("d1", "discover", "loss"), ("d2", "discover", "win"),
                    ("v1", "define", "loss"), ("v2", "define", "win"),
                    ("v3", "deliver", "loss"), ("v4", "deliver", "loss")]
                assert m["iterations"][4]["note"].startswith("tells: accent-emphasis") and m["iterations"][4]["score"] is None
                assert m["iterations"][2]["note"].startswith("integrity:") and m["iterations"][2]["score"] is None
                assert set(m["iterations"][0]) == {"id", "phase", "html", "shots", "score", "verdict", "note", "ts"}
                logs = [json.loads(x) for x in (run / "log.jsonl").read_text().splitlines()]
                assert sum(1 for x in logs if x["kind"] == "critic") == 7
                sent = [i for x in logs if x["kind"] == "critic" for i in x["images"]]
                assert any(i.endswith("v2-desktop-fold.png") for i in sent)
                assert any(x["kind"] == "integrity" and not x["ok"] for x in logs)
                assert (base / "out.html").read_text() == ref
                # unparseable twice -> error
                replies[:] = [garbage, garbage]
                try:
                    critic(run, "x", "p", parse_score)
                    raise AssertionError("expected failure")
                except LoopError:
                    pass
                replies[:] = [json.dumps({"type": "result", "is_error": True,
                                          "result": "Failed to authenticate. API Error: 401"})]
                try:
                    critic(run, "x", "p", parse_score)
                    raise AssertionError("expected auth failure")
                except LoopError as e:
                    assert "AUTH" in str(e)
                # model override reaches CALL
                seen = []
                CALL = lambda model, prompt, cwd: (seen.append(model), fake_call(model, prompt, cwd))[1]
                replies[:] = ["SCORE: 5/10"]
                critic(run, "m", "p", parse_score, model="claude-opus-5-5")
                assert seen == ["claude-opus-5-5"]
                # copy gate: more detector issues than baseline -> exit 1
                real_detect = DETECT
                DETECT = lambda text: {"available": True, "score": 0,
                                       "issues": [{"text": w} for w in ("seamless", "elevate") if w in text]}
                try:
                    (base / "a.html").write_text("<p>Elevate your seamless flow</p>")
                    (base / "b.html").write_text("<p>Plan your week</p>")
                    assert cmd_copy(run, str(base / "b.html"), str(base / "a.html")) == 0
                    assert cmd_copy(run, str(base / "a.html"), str(base / "b.html")) == 1
                    assert (run / "copy.md").read_text() == "Elevate your seamless flow\n"
                finally:
                    DETECT = real_detect
            finally:
                quiet.close()
                os.chdir(cwd)
    finally:
        CALL, SHOT = old
    if shutil.which("npx") and shutil.which("ffmpeg"):
        selftest_shots()
    else:
        print("selftest: skipped real screenshots (needs npx + ffmpeg)")
    print("selftest: OK")


def selftest_shots() -> None:
    """Real browser. A page that changes at 2s (the fold lands ~1.1s after load) gets a settled shot and an
    upscale warning; a static page gets neither."""
    with tempfile.TemporaryDirectory() as td:
        run = Path(td) / ".design-loop"
        for d in SUBDIRS:
            (run / d).mkdir(parents=True)
        save(run, "brief.json", {"settle_ms": 3000})
        (run / "variants" / "assets").mkdir()
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=200x120",
                        "-frames:v", "1", str(run / "variants" / "assets" / "a.png")], check=True)
        img = '<img src="assets/a.png" style="width:{}px">'
        (run / "variants" / "timed.html").write_text(
            "<body style='background:#111'>" + img.format(600) +
            "<script>setTimeout(()=>document.body.style.background='#e33',2000)</script></body>")
        (run / "variants" / "static.html").write_text("<body style='background:#111'>" + img.format(180) + "</body>")
        timed, static = shot(run, "timed"), shot(run, "static")
        assert timed[:2] == ["shots/timed-desktop-fold.png", "shots/timed-desktop-settled.png"], timed
        assert "shots/timed-mobile-settled.png" in timed
        assert (run / timed[0]).read_bytes() != (run / timed[1]).read_bytes()
        assert not any("settled" in x for x in static) and not list((run / "shots").glob("static-*-settled.png"))
        w = upscaled(run, "timed")
        assert len(w) == 1 and w[0].startswith("assets/a.png: 200px wide, shown at 600px"), w
        assert upscaled(run, "static") == []
        assert tells(run, "static") == []
        (run / "variants" / "slop.html").write_text(
            "<body style='font-family:Georgia'><p style='font:12px Arial;text-transform:uppercase'>Retreat</p>"
            "<h1 style='font-size:64px'>Make space <em style='color:#e60'>better</em></h1>"
            "<p>Plain <em>stress</em> in running text is fine, <span style='font-family:Courier'>this</span> is not.</p>"
            "<section style='height:600px;background:linear-gradient(#03f,#0cf)'>"
            "<div style='width:400px;height:300px;padding:20px;box-shadow:0 1px 4px #000'>"
            "<div style='width:300px;height:200px;border:1px solid #ccc'>x</div></div></section></body>")
        shot(run, "slop")
        assert sorted(t.split(":")[0] for t in tells(run, "slop")) == [
            "accent-emphasis", "accent-emphasis", "background-gradient", "eyebrow", "nested-cards",
            "too-many-fonts"], tells(run, "slop")
        with contextlib.redirect_stdout(io.StringIO()):
            assert cmd_tells((run / "variants" / "slop.html").resolve().as_uri()) == 1  # URL form
            assert cmd_tells(str(run / "variants" / "static.html")) == 0  # path form


def main() -> int:
    ap = argparse.ArgumentParser(prog="loop.py", description=__doc__)
    ap.add_argument("--run", default=str(RUN), help="run dir (default ./.design-loop)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").add_argument("--brief", required=True)
    sub.add_parser("seed").add_argument("--id")
    sub.add_parser("shot").add_argument("id")
    sub.add_parser("judge")
    sub.add_parser("pick").add_argument("id")
    r = sub.add_parser("round")
    r.add_argument("html")
    r.add_argument("--phase", choices=("define", "deliver"), default="define")
    sub.add_parser("decide")
    sub.add_parser("finish")
    sub.add_parser("status")
    i = sub.add_parser("integrity")
    i.add_argument("reference")
    i.add_argument("candidate")
    i.add_argument("--brief")
    sub.add_parser("tells", help="exit 1 if the page (file or URL) has banned AI design tells").add_argument("html")
    c = sub.add_parser("copy")
    c.add_argument("html")
    c.add_argument("--baseline", help="gate: exit 1 if html has more detector issues than this file")
    sub.add_parser("selftest")
    a = ap.parse_args()
    run = Path(a.run).resolve()
    try:
        if a.cmd == "init":
            cmd_init(run, a.brief)
        elif a.cmd == "seed":
            cmd_seed(run, a.id)
        elif a.cmd == "shot":
            print(json.dumps(shot(run, a.id)))
        elif a.cmd == "judge":
            cmd_judge(run)
        elif a.cmd == "pick":
            cmd_pick(run, a.id)
        elif a.cmd == "round":
            cmd_round(run, a.html, a.phase)
        elif a.cmd == "decide":
            cmd_decide(run)
        elif a.cmd == "finish":
            cmd_finish(run)
        elif a.cmd == "status":
            cmd_status(run)
        elif a.cmd == "integrity":
            return cmd_integrity(a.reference, a.candidate, a.brief)
        elif a.cmd == "tells":
            return cmd_tells(a.html)
        elif a.cmd == "copy":
            return cmd_copy(run, a.html, a.baseline)
        elif a.cmd == "selftest":
            selftest()
    except LoopError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
