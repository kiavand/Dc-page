"""
Persian Reel builder v2.

Input file (input.txt):
  line 1 starting with "#"  -> cover hook text (also spoken first)
  every other non-empty line -> one scene (one AI background image)
The narration is split into short phrases; each phrase is spoken and shown
on screen at the same time, so text and voice always match.

Optional: put .mp3/.wav files (royalty-free music) next to this script or in
a folder called "music". One is picked and mixed quietly under the voice.

Usage: python make_reel.py input.txt out/reel.mp4
Outputs: reel.mp4 and cover.png (same folder)
"""
import asyncio
import base64
import hashlib
import html
import random
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

W, H, FPS = 1080, 1920, 30
VOICE = "fa-IR-FaridNeural"      # male Persian voice
RATE = "-3%"
GAP = 0.12                       # silence between phrases (s)
TAIL = 0.30                      # silence at the end of each scene (s)
XFADE = 0.5                      # crossfade between scenes (s)
MUSIC_VOLUME = 0.12
COVER_LABEL = "آموزش رهبری"       # small label on every cover (change freely)
STYLE = ", vertical 9:16, cinematic soft lighting, shallow depth of field, no text, no letters, no logos"
FALLBACK_PROMPT = "calm professional abstract background, soft warm light, blurred bokeh"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

FONT_LINK = ('<link href="https://fonts.googleapis.com/css2?family=Vazirmatn:'
             'wght@500;800&display=swap" rel="stylesheet">')
FONT = "'Vazirmatn','Tahoma','DejaVu Sans',sans-serif"


# ---------------------------------------------------------------- text
def parse(text):
    hook, scenes = None, []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#") and hook is None:
            hook = line.lstrip("#").strip()
        else:
            scenes.append(line)
    if hook is None:
        hook = re.split(r"[.؟?!]", scenes[0])[0].strip() if scenes else ""
    return hook, scenes


def split_chunks(text, max_chars=42):
    parts = re.split(r"(?<=[،,\.؟\?!؛:;…])\s+", text.strip())
    out = []
    for p in parts:
        p = p.strip()
        while len(p) > max_chars * 1.4:
            cut = p.rfind(" ", 0, max_chars)
            cut = cut if cut > 10 else max_chars
            out.append(p[:cut].strip())
            p = p[cut:].strip()
        if p:
            out.append(p)
    merged = []
    for p in out:
        if merged and len(p) < 12 and len(merged[-1]) + len(p) < max_chars:
            merged[-1] += " " + p
        else:
            merged.append(p)
    return merged


# ---------------------------------------------------------------- AI images
def _get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def image_prompt(text):
    ask = ("Write ONE short English image-generation prompt (max 25 words) for a calm, "
           "professional photographic background that visually matches this Persian text. "
           "No text, no letters, no faces. Reply with only the prompt. Text: " + text)
    q = urllib.parse.quote(ask)
    for base in ("https://text.pollinations.ai/", "https://gen.pollinations.ai/text/"):
        try:
            out = _get(base + q, 45).decode("utf-8", "ignore").strip().splitlines()[0]
            ascii_ratio = sum(c.isascii() for c in out) / max(len(out), 1)
            if 10 < len(out) < 400 and ascii_ratio > 0.9 and not out.startswith(("{", "<")):
                return out
        except Exception as e:
            print("prompt service failed:", e)
    return FALLBACK_PROMPT


def fetch_image(prompt, path, seed):
    q = urllib.parse.quote(prompt + STYLE)
    urls = [
        f"https://image.pollinations.ai/prompt/{q}?width={W}&height={H}&nologo=true&seed={seed}",
        f"https://gen.pollinations.ai/image/{q}?width={W}&height={H}&seed={seed}",
    ]
    for u in urls:
        try:
            data = _get(u, 90)
            if len(data) > 5000 and (data[:3] == b"\xff\xd8\xff" or data[:4] == b"\x89PNG"):
                path.write_bytes(data)
                return True
        except Exception as e:
            print("image service failed:", e)
    return False


# ---------------------------------------------------------------- speech
async def tts(text, out_mp3):
    import edge_tts
    last = None
    for _ in range(3):
        try:
            await edge_tts.Communicate(text, VOICE, rate=RATE).save(str(out_mp3))
            return
        except Exception as e:
            last = e
            await asyncio.sleep(2)
    raise last


def run(cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def to_wav(src, dst, pad):
    run(["ffmpeg", "-y", "-i", str(src), "-ar", "44100", "-ac", "2",
         "-af", f"apad=pad_dur={pad}", str(dst)])


def concat_wavs(paths, dst, d):
    lst = d / (dst.stem + "_list.txt")
    lst.write_text("".join(f"file '{p}'\n" for p in paths), encoding="utf-8")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c", "copy", str(dst)])


async def build_scene_audio(idx, chunks, d):
    """Return (scene_wav, [(chunk_text, start, end)], total_duration)."""
    wavs, timing, t = [], [], 0.0
    for j, ch in enumerate(chunks):
        mp3 = d / f"a{idx:02d}_{j:02d}.mp3"
        wav = d / f"a{idx:02d}_{j:02d}.wav"
        await tts(ch, mp3)
        last = j == len(chunks) - 1
        to_wav(mp3, wav, TAIL if last else GAP)
        dur = duration(wav)
        timing.append((ch, t, t + dur - (TAIL if last else GAP)))
        wavs.append(wav)
        t += dur
    scene_wav = d / f"scene{idx:02d}.wav"
    concat_wavs(wavs, scene_wav, d)
    return scene_wav, timing, t


# ---------------------------------------------------------------- rendering
def img_b64(path):
    mime = "image/png" if path.suffix == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def bg_html(image_path):
    bg = (f"url({img_b64(image_path)}) center/cover no-repeat" if image_path
          else "linear-gradient(160deg,#0f172a,#1e3a5f)")
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>"
            f"html,body{{margin:0;width:{W}px;height:{H}px}}"
            f"body{{background:{bg}}}"
            f".s{{position:absolute;inset:0;background:linear-gradient(to bottom,"
            f"rgba(0,0,0,.30),rgba(0,0,0,.55))}}</style></head>"
            f"<body><div class='s'></div></body></html>")


def sub_html(text):
    n = len(text)
    size = 70 if n < 28 else 62 if n < 40 else 56
    return (f"<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'>{FONT_LINK}"
            f"<style>html,body{{margin:0;width:{W}px;height:{H}px;background:transparent}}"
            f".w{{position:absolute;left:0;right:0;top:1130px;display:flex;justify-content:center}}"
            f".b{{max-width:900px;background:rgba(0,0,0,.58);color:#fff;font-family:{FONT};"
            f"font-weight:800;font-size:{size}px;line-height:1.7;padding:24px 44px;"
            f"border-radius:36px;text-align:center}}</style></head>"
            f"<body><div class='w'><div class='b'>{html.escape(text)}</div></div></body></html>")


def cover_html(hook):
    n = len(hook)
    size = 120 if n < 30 else 100 if n < 55 else 84
    return (f"<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'>{FONT_LINK}"
            f"<style>html,body{{margin:0;width:{W}px;height:{H}px}}"
            f"body{{background:linear-gradient(160deg,#0b1020,#1e3a5f 60%,#7c3aed);"
            f"font-family:{FONT};color:#fff;display:flex;flex-direction:column;"
            f"align-items:center;justify-content:center;text-align:center}}"
            f".tag{{background:#fbbf24;color:#111;font-weight:800;font-size:46px;"
            f"padding:10px 40px;border-radius:60px;margin-bottom:70px}}"
            f".h{{font-weight:800;font-size:{size}px;line-height:1.6;padding:0 80px}}"
            f".bar{{width:220px;height:14px;background:#fbbf24;border-radius:8px;margin-top:70px}}"
            f"</style></head><body><div class='tag'>{html.escape(COVER_LABEL)}</div>"
            f"<div class='h'>{html.escape(hook)}</div><div class='bar'></div></body></html>")


def render_pages(jobs):
    """jobs: list of (html_string, out_png, transparent)."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H})
        for content, out, transparent in jobs:
            page.set_content(content, wait_until="networkidle")
            page.evaluate("document.fonts.ready.then(() => true)")
            page.screenshot(path=str(out), omit_background=transparent)
        browser.close()


# ---------------------------------------------------------------- video
def scene_video(idx, bg_png, subs, dur, is_last, out):
    """bg with slow zoom + timed subtitle overlays. subs: [(png, start, end)]."""
    length = dur + (0 if is_last else XFADE)
    frames = int(length * FPS) + 2
    inc = 0.12 / frames
    if idx % 2 == 0:
        z = f"min(zoom+{inc:.6f},1.12)"
    else:
        z = f"if(eq(on,0),1.12,max(zoom-{inc:.6f},1.0))"
    cmd = ["ffmpeg", "-y", "-i", str(bg_png)]
    for png, _, _ in subs:
        cmd += ["-i", str(png)]
    fc = (f"[0:v]scale={W*2}:{H*2},zoompan=z='{z}':x='iw/2-(iw/zoom/2)':"
          f"y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS},format=yuv420p[v0]")
    last = "v0"
    for k, (_, a, b) in enumerate(subs, start=1):
        fc += (f";[{last}][{k}:v]overlay=0:0:enable='between(t,{a:.3f},{b:.3f})'[v{k}]")
        last = f"v{k}"
    cmd += ["-filter_complex", fc, "-map", f"[{last}]", "-t", f"{length:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", str(FPS), str(out)]
    run(cmd)


def pick_music(seed_text):
    here = Path(__file__).resolve().parent
    files = sorted([*here.glob("music/*.mp3"), *here.glob("music/*.wav"),
                    *here.glob("*.mp3"), *here.glob("*.wav")])
    if not files:
        return None
    rnd = random.Random(int(hashlib.md5(seed_text.encode()).hexdigest(), 16))
    return rnd.choice(files)


def assemble(clips, durs, voice_wav, music, out):
    total = sum(durs)
    cmd = ["ffmpeg", "-y"]
    for c in clips:
        cmd += ["-i", str(c)]
    cmd += ["-i", str(voice_wav)]
    vi = len(clips)
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
    fc, last, start = "", "[0:v]", 0.0
    for i in range(1, len(clips)):
        start += durs[i - 1]
        fc += (f"{last}[{i}:v]xfade=transition=fade:duration={XFADE}:"
               f"offset={start:.3f}[x{i}];")
        last = f"[x{i}]"
    if len(clips) == 1:
        fc += "[0:v]null[vout];"
    else:
        fc += f"{last}null[vout];"
    if music:
        fc += (f"[{vi+1}:a]volume={MUSIC_VOLUME},afade=t=in:d=1,"
               f"afade=t=out:st={max(total-1.5,0):.3f}:d=1.5[m];"
               f"[{vi}:a][m]amix=inputs=2:duration=first:normalize=0[aout]")
    else:
        fc += f"[{vi}:a]anull[aout]"
    cmd += ["-filter_complex", fc, "-map", "[vout]", "-map", "[aout]",
            "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)]
    run(cmd)


# ---------------------------------------------------------------- main
async def build(hook, scenes, d, dst):
    # 1) audio for cover + scenes
    all_scenes = [("cover", [hook])] + [("scene", split_chunks(s)) for s in scenes]
    audio = []
    for i, (_, chunks) in enumerate(all_scenes):
        audio.append(await build_scene_audio(i, chunks, d))

    # 2) AI backgrounds
    bg_images = [None]
    for i, s in enumerate(scenes, start=1):
        path = d / f"ai{i:02d}.jpg"
        prompt = image_prompt(s)
        print(f"scene {i} image prompt: {prompt}")
        ok = fetch_image(prompt, path, seed=i * 7 + 3)
        bg_images.append(path if ok else None)
        if not ok:
            print(f"scene {i}: AI image failed, using gradient background")

    # 3) render all pictures in one browser session
    jobs, bg_pngs, sub_pngs = [], [], []
    cover_png = d / "cover.png"
    jobs.append((cover_html(hook), cover_png, False))
    bg_pngs.append(cover_png)
    sub_pngs.append([])
    for i in range(1, len(all_scenes)):
        bp = d / f"bg{i:02d}.png"
        jobs.append((bg_html(bg_images[i]), bp, False))
        bg_pngs.append(bp)
        row = []
        for j, (txt, a, b) in enumerate(audio[i][1]):
            sp = d / f"sub{i:02d}_{j:02d}.png"
            jobs.append((sub_html(txt), sp, True))
            row.append((sp, a, b))
        sub_pngs.append(row)
    render_pages(jobs)

    # 4) video per scene, then join with crossfades
    clips, durs = [], []
    n = len(all_scenes)
    for i in range(n):
        clip = d / f"clip{i:02d}.mp4"
        dur = audio[i][2]
        scene_video(i, bg_pngs[i], sub_pngs[i], dur, i == n - 1, clip)
        clips.append(clip)
        durs.append(dur)
    voice = d / "voice.wav"
    concat_wavs([a[0] for a in audio], voice, d)
    music = pick_music(hook)
    print("music:", music.name if music else "none (add .mp3 files to use music)")
    dst.parent.mkdir(parents=True, exist_ok=True)
    assemble(clips, durs, voice, music, dst)
    (dst.parent / "cover.png").write_bytes(cover_png.read_bytes())
    total = sum(durs)
    print(f"Done: {dst} ({n} scenes incl. cover, {total:.1f} s)")
    if total > 90:
        print("WARNING: Instagram API allows Reels up to 90 seconds only.")


def main():
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    hook, scenes = parse(src.read_text(encoding="utf-8"))
    if not scenes:
        sys.exit("Input file has no scenes.")
    with tempfile.TemporaryDirectory() as t:
        asyncio.run(build(hook, scenes, Path(t), dst))


if __name__ == "__main__":
    main()
