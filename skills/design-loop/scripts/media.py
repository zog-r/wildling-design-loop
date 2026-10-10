#!/usr/bin/env python3
"""design-loop media: provider doctor, image/video generation, 3D scene recording, ffmpeg helpers, Opus compare.

Keys come from ./.env.agents (see .env.agents.example). A blank value means "I don't have this provider".
Values are never printed or logged.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loop  # noqa: E402  (same dir; reuses env parsing, logging, critic plumbing)

SCRIPTS = Path(__file__).resolve().parent
CODEX = ["npx", "-y", "@openai/codex@0.157.1"]
PLAYWRIGHT = ["npx", "-y", "-p", "playwright@1.63.0", "node"]
CODEX_AUTH_RE = re.compile(r"could not be refreshed|unauthorized \(401\)|not logged in|log out and sign in", re.I)
CODEX_LOGIN = "npx -y @openai/codex@0.157.1 login"
MAGIC = {b"\x89PNG": "png", b"\xff\xd8\xff": "jpg", b"RIFF": "webp", b"GIF8": "gif"}
VEO_MODEL = "veo-3.1-generate-preview"
GEMINI = "https://generativelanguage.googleapis.com/v1beta"
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm"}
# ffmpeg looks that survived a 16-round critic run. Heavier grades (strong teal/orange, crushed highlights)
# were called "sludge"; keep new looks this subtle.
LOOKS = {
    "dusk": "curves=all='0/0 0.3/0.24 0.65/0.6 1/0.9',eq=contrast=1.12:saturation=0.98:gamma=0.94,"
            "colorbalance=rs=-0.04:bs=0.07:rm=0.1:gm=0.03:bm=-0.07:rh=0.04:gh=0.01:bh=-0.03,unsharp=5:5:0.5",
    "warm-evening": "curves=all='0/0.02 0.25/0.2 0.6/0.6 1/0.93',eq=contrast=1.16:saturation=1.0:gamma=0.95,"
                    "colorbalance=rs=-0.02:bs=0.05:rm=0.08:gm=0.02:bm=-0.07:rh=0.02:bh=-0.02",
    "cool-day": "eq=contrast=1.05:saturation=0.96:gamma=1.02,colorbalance=rs=-0.03:bs=0.03:rm=-0.02:bm=0.04",
    "none": "",
}
BLOOM = "exp(-(pow(X/W-0.55,2)+pow(Y/H-0.45,2))/0.07)"  # warm radial glow, centered a little right of middle

# provider -> capabilities its adapter covers. ponytail: fal/minimax/openai-api/compat adapters are
# documented in .env.agents.example but written the first time someone fills in that key.
PROVIDERS = {
    "openai": {"image": "subscription"},          # Codex CLI image_generation, ChatGPT plan
    "google": {"video": "api"},                   # Veo via GEMINI_API_KEY
    "anthropic": {"compare": "subscription"},     # claude -p (Opus) judges images and video contact sheets
    "fal": {}, "minimax": {}, "openai_compat": {}, "ollama": {}, "lmstudio": {},
}


class MediaError(loop.LoopError):
    pass


def env() -> dict:
    return loop.read_env(Path.cwd() / loop.ENV_FILE)


def run(cmd: list, timeout: int = 900, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)


def mlog(entry: dict) -> None:
    if loop.RUN.is_dir():
        loop.log(loop.RUN, {"kind": "media", **entry})


def image_kind(path: Path) -> str | None:
    head = path.read_bytes()[:4] if path.is_file() else b""
    return next((k for m, k in MAGIC.items() if head.startswith(m)), None)


# ---------- doctor ----------

def codex_status(live: bool = False) -> dict:
    p = run(CODEX + ["login", "status"], timeout=120)
    out = (p.stdout + p.stderr).strip()
    if live and "ChatGPT" in out:
        # `login status` reads auth.json and can't tell that a token no longer refreshes; one tiny call can.
        child = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        probe = run(CODEX + ["exec", "--skip-git-repo-check", "-s", "read-only", "Reply with OK only."],
                    timeout=180, env=child)
        if CODEX_AUTH_RE.search(probe.stdout + probe.stderr):
            out = "expired"
    feats = run(CODEX + ["features", "list"], timeout=120).stdout
    on = {ln.split()[0] for ln in feats.splitlines() if ln.split() and ln.split()[-1] == "true"}
    return {"logged_in_chatgpt": "ChatGPT" in out, "image": "image_generation" in on,
            "video": any("video" in f for f in on)}


def local_models(url: str) -> list:
    try:
        with urllib.request.urlopen(url.rstrip("/") + ("/api/tags" if ":11434" in url else "/models"), timeout=3) as r:
            d = json.load(r)
        return [m.get("name") or m.get("id") for m in d.get("models") or d.get("data") or []]
    except (OSError, ValueError):
        return []


def doctor(live: bool = False) -> dict:
    e = env()
    rep = {"env_file": str(Path.cwd() / loop.ENV_FILE), "env_found": (Path.cwd() / loop.ENV_FILE).is_file(),
           "ffmpeg": bool(shutil.which("ffmpeg")), "providers": {}}
    auth = lambda p: e.get(f"{p.upper()}_AUTH", "")  # noqa: E731
    cx = codex_status(live)
    rep["providers"]["openai"] = {
        "auth": auth("openai") or ("subscription" if cx["logged_in_chatgpt"] else ""),
        "usable": cx["logged_in_chatgpt"], "image": cx["image"], "video": cx["video"],
        "note": "" if cx["logged_in_chatgpt"] else f"run: {CODEX_LOGIN}"}
    rep["providers"]["google"] = {"auth": auth("google"), "usable": bool(e.get("GEMINI_API_KEY")),
                                  "video": bool(e.get("GEMINI_API_KEY")), "note": "Veo via GEMINI_API_KEY"}
    rep["providers"]["anthropic"] = {"auth": auth("anthropic") or "subscription", "usable": True, "compare": True}
    for p, key in (("fal", "FAL_KEY"), ("minimax", "MINIMAX_API_KEY"), ("openai_compat", "OPENAI_COMPAT_API_KEY")):
        rep["providers"][p] = {"auth": auth(p), "usable": False,
                               "note": "key set; adapter not written yet" if e.get(key) else "blank = off"}
    for p, key in (("ollama", "OLLAMA_HOST"), ("lmstudio", "LMSTUDIO_BASE_URL")):
        models = local_models(e[key]) if e.get(key) else []
        rep["providers"][p] = {"usable": False, "models": models[:10],
                               "note": "text/vision models only; no image or video generation"}
    rep["image"] = [p for p, v in rep["providers"].items() if v.get("usable") and v.get("image")]
    rep["video"] = ["scene"] + [p for p, v in rep["providers"].items() if v.get("usable") and v.get("video")]
    return rep


# ---------- image ----------

def codex_cmd(out: Path, refs: list) -> list:
    # No positional prompt: `-i` is variadic and would swallow it as another image path. The prompt goes on stdin.
    return CODEX + ["exec", "--skip-git-repo-check", "-s", "workspace-write", "--enable", "image_generation",
                    "-C", str(out.parent)] + [a for r in refs for a in ("-i", str(Path(r).resolve()))]


def codex_image(prompt: str, out: Path, refs: list = ()) -> None:
    for r in refs:
        if not image_kind(Path(r)):
            raise MediaError(f"--ref {r} is not a png/jpg/webp/gif image")
    out.parent.mkdir(parents=True, exist_ok=True)
    child = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}  # bill the ChatGPT plan, not a key
    verb = "edit the attached image(s) as follows" if refs else "create this image"
    ask = (f"Use your built-in image generation tool to {verb}: {prompt}\n"
           f"Save the generated image in the current directory as {out.name}. Create no other files. "
           f"Reply with just the saved filename.")
    p = run(codex_cmd(out, refs), timeout=900, env=child, input=ask)
    if CODEX_AUTH_RE.search(p.stdout + p.stderr):
        raise MediaError(f"AUTH ERROR: Codex login expired. Run: {CODEX_LOGIN}")
    if not image_kind(out):
        raise MediaError(f"codex did not produce an image at {out}: {(p.stderr or p.stdout).strip()[-400:]}")


def cmd_image(prompt: str, out: str, n: int, refs: list = ()) -> list:
    outs = []
    base = Path(out)
    for i in range(n):
        dest = base if n == 1 else base.with_name(f"{base.stem}-{i + 1}{base.suffix}")
        t0 = time.monotonic()
        codex_image(prompt, dest, refs)
        mlog({"step": "image", "provider": "openai-subscription", "out": str(dest), "refs": list(refs),
              "ms": int((time.monotonic() - t0) * 1000)})
        outs.append(str(dest))
    return outs


# ---------- video ----------

def b64img(path: str) -> dict:
    kind = image_kind(Path(path)) or "png"
    return {"bytesBase64Encoded": base64.b64encode(Path(path).read_bytes()).decode(),
            "mimeType": f"image/{'jpeg' if kind == 'jpg' else kind}"}


def http(url: str, key: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"x-goog-api-key": key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def veo(prompt: str, out: Path, key: str, first: str | None, last: str | None) -> None:
    inst: dict = {"prompt": prompt}
    if first:
        inst["image"] = b64img(first)
    if last:
        inst["lastFrame"] = b64img(last)
    op = http(f"{GEMINI}/models/{VEO_MODEL}:predictLongRunning", key, {"instances": [inst]})
    deadline = time.monotonic() + 900
    while not op.get("done"):
        if time.monotonic() > deadline:
            raise MediaError("veo: timed out after 15 min")
        time.sleep(10)
        op = http(f"{GEMINI}/{op['name']}", key)
    if "error" in op:
        raise MediaError(f"veo: {op['error'].get('message', op['error'])}")
    uri = op["response"]["generateVideoResponse"]["generatedSamples"][0]["video"]["uri"]
    req = urllib.request.Request(uri, headers={"x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=300) as r:
        out.write_bytes(r.read())


def cmd_video(prompt: str, out: str, first: str | None, last: str | None) -> str:
    e, dest = env(), Path(out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if e.get("GEMINI_API_KEY"):
        t0 = time.monotonic()
        veo(prompt, dest, e["GEMINI_API_KEY"], first, last)
        mlog({"step": "video", "provider": "google-veo", "out": out, "ms": int((time.monotonic() - t0) * 1000)})
        return out
    raise MediaError("no generative video provider: fill GEMINI_API_KEY in .env.agents, or build the motion "
                     "as a 3D/shader scene and `record` it (exit 3)")


def last_frame(video: str, out: str) -> str:
    ff(["-sseof", "-0.1", "-i", video, "-frames:v", "1", "-update", "1", out])
    return out


def cmd_chain(prompts_file: str, first: str, outdir: str) -> list:
    """Suitcase technique: each clip ends where the next begins (last frame seeds the next clip)."""
    prompts, clips, seed = json.loads(Path(prompts_file).read_text()), [], first
    Path(outdir).mkdir(parents=True, exist_ok=True)
    for i, p in enumerate(prompts, 1):
        clip = str(Path(outdir) / f"clip{i}.mp4")
        cmd_video(p, clip, seed, None)
        seed = last_frame(clip, str(Path(outdir) / f"clip{i}-last.png"))
        clips.append(clip)
    return clips


# ---------- ffmpeg ----------

def ff(args: list) -> None:
    p = run(["ffmpeg", "-y", "-loglevel", "error"] + args, timeout=600)
    if p.returncode != 0:
        raise MediaError(f"ffmpeg failed: {p.stderr.strip()[-400:]}")


def duration(video: str) -> float:
    p = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video])
    return float(p.stdout.strip() or 0)


def cmd_frames(video: str, n: int, outdir: str | None) -> dict:
    d = Path(outdir or Path(video).with_suffix("")).resolve()
    d.mkdir(parents=True, exist_ok=True)
    dur = duration(video)
    frames = []
    for i in range(n):
        f = d / f"f{i + 1:02d}.png"
        ff(["-ss", f"{dur * (i + 0.5) / n:.3f}", "-i", video, "-frames:v", "1", "-update", "1", str(f)])
        frames.append(str(f))
    sheet = d / "sheet.png"
    cols = min(n, 4)
    ff(["-i", str(d / "f%02d.png"), "-vf", f"scale=480:-2,tile={cols}x{-(-n // cols)}:padding=6:color=white",
        "-frames:v", "1", "-update", "1", str(sheet)])
    return {"frames": frames, "sheet": str(sheet)}


def cmd_key(video: str, color: str, out: str | None) -> str:
    dest = out or str(Path(video).with_name(Path(video).stem + "-alpha.webm"))
    ff(["-i", video, "-vf", f"colorkey=0x{color}:0.3:0.15,format=yuva420p", "-c:v", "libvpx-vp9",
        "-pix_fmt", "yuva420p", "-auto-alt-ref", "0", "-b:v", "0", "-crf", "30", dest])
    return dest


def cmd_scrub(video: str, out: str | None) -> str:
    """All-intra re-encode: every frame is a keyframe, so currentTime scrubbing on scroll stays smooth."""
    dest = out or str(Path(video).with_name(Path(video).stem + "-scrub.mp4"))
    ff(["-i", video, "-an", "-c:v", "libx264", "-g", "1", "-crf", "20", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", dest])
    return dest


def grade_filter(look: str, vignette: float, bloom: float) -> str:
    chain = [LOOKS[look]] if LOOKS[look] else []
    if bloom > 0:
        chain.append(f"format=gbrp,split[a][b];[b]geq=r='255*{BLOOM}':g='150*{BLOOM}':b='60*{BLOOM}'[g];"
                     f"[a][g]blend=all_mode=screen:all_opacity={0.24 * bloom:.3f}")
    if vignette > 0:
        chain.append(f"vignette=angle={vignette * 0.898:.3f}")  # 0.5 -> PI/7, the field value
    return ",".join(chain) or "null"


def cmd_grade(src: str, out: str, look: str, vignette: float, bloom: float) -> str:
    if not 0 <= vignette <= 1 or not 0 <= bloom <= 1:
        raise MediaError("--vignette and --bloom take 0..1")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    vf = ["-vf", grade_filter(look, vignette, bloom)]
    if Path(src).suffix.lower() in VIDEO_EXT:
        codec = (["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "32"] if out.endswith(".webm")
                 else ["-c:v", "libx264", "-crf", "28", "-movflags", "+faststart"])
        ff(["-i", src] + vf + ["-an", "-pix_fmt", "yuv420p"] + codec + [out])
    else:
        ff(["-i", src] + vf + ["-frames:v", "1", "-update", "1", out])
    mlog({"step": "grade", "look": look, "out": out})
    return out


def mean_luma(path: str) -> float:
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-frames:v", "1", "-vf", "format=gray",
                        "-f", "rawvideo", "-"], capture_output=True, timeout=120)
    return sum(p.stdout) / max(1, len(p.stdout))


def size(path: str) -> tuple:
    p = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
             "-of", "csv=p=0", path])
    return tuple(int(x) for x in p.stdout.strip().split(","))


# ---------- 3D scene recording ----------

def cmd_record(scene: str, out: str, seconds: float, fps: int, bg: str | None) -> str:
    with tempfile.TemporaryDirectory() as td:
        p = run(PLAYWRIGHT + [str(SCRIPTS / "rec.mjs"), str(Path(scene).resolve()), td, str(seconds), str(fps),
                              bg or ""], timeout=900)
        if p.returncode != 0:
            raise MediaError(f"record failed: {p.stderr.strip()[-600:]}")
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        codec = (["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "30"] if out.endswith(".webm")
                 else ["-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p"])
        ff(["-framerate", str(fps), "-i", f"{td}/%05d.png"] + codec + [out])
    mlog({"step": "record", "provider": "scene", "out": out})
    return out


# ---------- compare (Opus) ----------

def cmd_compare(a: str, b: str, kind: str) -> dict:
    run_dir = loop.RUN
    if not (run_dir / "brief.json").is_file():
        raise MediaError("compare needs an initialized .design-loop run (for the media_model and log)")
    model = loop.load(run_dir, "brief.json")["media_model"]
    paths = {}
    for label, src in (("A", a), ("B", b)):
        paths[label] = cmd_frames(src, 8, None)["sheet"] if kind == "video" else str(Path(src).resolve())
    prompt = ((SCRIPTS.parent / "media-compare-prompt.md").read_text()
              + f"\nKind: {kind}\nA: {paths['A']}\nB: {paths['B']}\n")
    winner, text = loop.critic(run_dir, f"media:{Path(a).name}-vs-{Path(b).name}", prompt, loop.parse_winner,
                               model=model, cwd=Path(paths["A"]).parent)
    return {"winner": a if winner == "A" else b, "loser": b if winner == "A" else a,
            "reason": loop.first_line(text, "REASON")}


# ---------- selftest ----------

def selftest() -> None:
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        (t / "p.png").write_bytes(b"\x89PNG\r\n\x1a\nxx")
        (t / "x.txt").write_text("nope")
        assert image_kind(t / "p.png") == "png" and image_kind(t / "x.txt") is None
        assert image_kind(t / "missing.png") is None
        # --ref: every ref is a separate -i and no positional prompt follows (it would be read as an image)
        (t / "r.jpg").write_bytes(b"\xff\xd8\xffxx")
        cmd = codex_cmd(t / "o.png", [str(t / "p.png"), str(t / "r.jpg")])
        assert cmd[-4:] == ["-i", str((t / "p.png").resolve()), "-i", str((t / "r.jpg").resolve())], cmd
        try:
            codex_image("x", t / "o.png", [str(t / "x.txt")])
            raise AssertionError("expected bad-ref error")
        except MediaError as err:
            assert "--ref" in str(err)
        assert CODEX_AUTH_RE.search("Your access token could not be refreshed. Please log out and sign in again.")
        if shutil.which("ffmpeg"):
            clip = str(t / "c.mp4")
            ff(["-f", "lavfi", "-i", "color=c=0x00ff00:s=160x120:d=2,drawbox=x=t*40:y=40:w=30:h=30:c=red:t=fill",
                "-r", "15", "-pix_fmt", "yuv420p", clip])
            assert 1.5 < duration(clip) < 2.5
            fr = cmd_frames(clip, 4, str(t / "fr"))
            assert len(fr["frames"]) == 4 and image_kind(Path(fr["sheet"])) == "png"
            assert Path(cmd_key(clip, "00ff00", None)).stat().st_size > 0
            assert Path(cmd_scrub(clip, None)).stat().st_size > 0
            assert image_kind(Path(last_frame(clip, str(t / "last.png")))) == "png"
            # grade: dims kept, luminance moves the expected way
            pat = str(t / "pat.png")
            ff(["-f", "lavfi", "-i", "testsrc2=s=64x64", "-frames:v", "1", pat])
            base = mean_luma(pat)
            # (look, vignette, bloom, expected luma shift range)
            for look, v, b, lo, hi in (("dusk", 0, 0, -99, -5), ("warm-evening", 0, 0, -99, -5),
                                       ("cool-day", 0, 0, -3, 3), ("none", 0, 0, -1, 1),
                                       ("none", 0.6, 0, -99, -3), ("none", 0, 1, 3, 99)):
                g = cmd_grade(pat, str(t / f"g-{look}-{v}-{b}.png"), look, v, b)
                assert size(g) == (64, 64) and lo < mean_luma(g) - base < hi, (look, v, b, mean_luma(g), base)
            g = cmd_grade(clip, str(t / "g.mp4"), "dusk", 0.3, 0.3)
            assert size(g) == (160, 120) and mean_luma(g) < mean_luma(clip)
        cwd = os.getcwd()
        os.chdir(td)
        try:
            (t / ".env.agents").write_text("OPENAI_AUTH=subscription\nGEMINI_API_KEY=\nFAL_KEY=secretvalue123456\n")
            e = env()
            assert "GEMINI_API_KEY" not in e and e["FAL_KEY"] == "secretvalue123456"
            try:
                cmd_video("x", str(t / "v.mp4"), None, None)
                raise AssertionError("expected no-provider error")
            except MediaError as err:
                assert "no generative video provider" in str(err)
        finally:
            os.chdir(cwd)
    print("media selftest: OK")


def main() -> int:
    ap = argparse.ArgumentParser(prog="media.py", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor").add_argument("--live", action="store_true",
                                          help="one tiny Codex call to catch expired tokens")
    s = sub.add_parser("image")
    s.add_argument("--prompt", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--n", type=int, default=1)
    s.add_argument("--ref", action="append", default=[], help="photo to edit/relight (repeatable)")
    s = sub.add_parser("video")
    s.add_argument("--prompt", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--first")
    s.add_argument("--last")
    s = sub.add_parser("chain")
    s.add_argument("--prompts", required=True, help="JSON list of per-transition prompts")
    s.add_argument("--first", required=True)
    s.add_argument("--outdir", required=True)
    s = sub.add_parser("record")
    s.add_argument("scene")
    s.add_argument("--out", required=True)
    s.add_argument("--seconds", type=float, default=6)
    s.add_argument("--fps", type=int, default=30)
    s.add_argument("--bg", help="solid background hex (e.g. 00ff00) to key out later")
    s = sub.add_parser("frames")
    s.add_argument("video")
    s.add_argument("--n", type=int, default=8)
    s.add_argument("--outdir")
    s = sub.add_parser("key")
    s.add_argument("video")
    s.add_argument("--color", default="00ff00")
    s.add_argument("--out")
    s = sub.add_parser("scrub")
    s.add_argument("video")
    s.add_argument("--out")
    s = sub.add_parser("grade")
    s.add_argument("src")
    s.add_argument("--out", required=True)
    s.add_argument("--look", choices=tuple(LOOKS), required=True)
    s.add_argument("--vignette", type=float, default=0)
    s.add_argument("--bloom", type=float, default=0, help="warm radial glow, for lit interiors")
    s = sub.add_parser("compare")
    s.add_argument("a")
    s.add_argument("b")
    s.add_argument("--kind", choices=("image", "video"), default="image")
    sub.add_parser("selftest")
    a = ap.parse_args()
    try:
        if a.cmd == "doctor":
            r = doctor(a.live)
        elif a.cmd == "image":
            r = cmd_image(a.prompt, a.out, a.n, a.ref)
        elif a.cmd == "video":
            r = cmd_video(a.prompt, a.out, a.first, a.last)
        elif a.cmd == "chain":
            r = cmd_chain(a.prompts, a.first, a.outdir)
        elif a.cmd == "record":
            r = cmd_record(a.scene, a.out, a.seconds, a.fps, a.bg)
        elif a.cmd == "frames":
            r = cmd_frames(a.video, a.n, a.outdir)
        elif a.cmd == "key":
            r = cmd_key(a.video, a.color, a.out)
        elif a.cmd == "scrub":
            r = cmd_scrub(a.video, a.out)
        elif a.cmd == "grade":
            r = cmd_grade(a.src, a.out, a.look, a.vignette, a.bloom)
        elif a.cmd == "compare":
            r = cmd_compare(a.a, a.b, a.kind)
        else:
            selftest()
            return 0
    except loop.LoopError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3 if "no generative video provider" in str(e) else 2
    print(json.dumps(r, indent=2 if a.cmd == "doctor" else None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
