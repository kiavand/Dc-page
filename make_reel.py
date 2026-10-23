"""
Text -> vertical Persian Reel (MP4, 1080x1920, H.264/AAC).

Input file format: one scene per line.
    screen text | spoken text      (spoken text is optional)
If a line has no "|", the same text is shown and spoken.

Usage: python make_reel.py input.txt out/reel.mp4
"""
import asyncio
import html
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import edge_tts
from playwright.sync_api import sync_playwright

VOICE = "fa-IR-DilaraNeural"   # female Persian voice; male: fa-IR-FaridNeural
RATE = "-5%"                   # slightly slower speech
W, H = 1080, 1920
PAD = 0.45                     # seconds of silence after each scene

PAGE = """<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Vazirmatn:wght@500;800&display=swap" rel="stylesheet">
<style>
html,body{margin:0;width:1080px;height:1920px}
body{background:linear-gradient(160deg,#0f172a,#1e3a5f);display:flex;
align-items:center;justify-content:center;
font-family:'Vazirmatn','Tahoma','DejaVu Sans',sans-serif;color:#fff}
.box{padding:0 90px;text-align:center;line-height:1.7;
font-size:__SIZE__px;font-weight:800}
.n{display:block;font-size:170px;color:#fbbf24;margin-bottom:20px}
</style></head><body><div class="box">__BODY__</div></body></html>"""


def parse(text):
    scenes = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" in line:
            screen, speak = [x.strip() for x in line.split("|", 1)]
        else:
            screen = speak = line
        scenes.append({"screen": screen, "speak": speak or screen})
    return scenes


def build_html(screen):
    badge = ""
    m = re.match(r"^([0-9\u06F0-\u06F9]+)\s*[\.\)\:\-\u2013]\s*(.+)$", screen)
    if m:
        badge = f'<span class="n">{html.escape(m.group(1))}</span>'
        screen = m.group(2)
    n = len(screen)
    size = 100 if n < 40 else 84 if n < 80 else 70
    body = badge + html.escape(screen)
    return PAGE.replace("__SIZE__", str(size)).replace("__BODY__", body)


async def synth(scenes, d):
    for i, s in enumerate(scenes):
        out = d / f"s{i:02d}.mp3"
        await edge_tts.Communicate(s["speak"], VOICE, rate=RATE).save(str(out))
        s["audio"] = out


def render_pngs(scenes, d):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H})
        for i, s in enumerate(scenes):
            page.set_content(build_html(s["screen"]), wait_until="networkidle")
            page.evaluate("document.fonts.ready.then(() => true)")
            png = d / f"s{i:02d}.png"
            page.screenshot(path=str(png))
            s["png"] = png
        browser.close()


def duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def make_clips(scenes, d):
    total = 0.0
    for i, s in enumerate(scenes):
        clip = d / f"c{i:02d}.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-loop", "1", "-framerate", "30", "-i", str(s["png"]),
             "-i", str(s["audio"]), "-af", f"apad=pad_dur={PAD}",
             "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
             "-r", "30", "-c:a", "aac", "-ar", "44100", "-ac", "2",
             "-shortest", str(clip)],
            check=True, capture_output=True)
        s["clip"] = clip
        total += duration(clip)
    return total


def concat(scenes, d, out):
    lst = d / "list.txt"
    lst.write_text("".join(f"file '{s['clip']}'\n" for s in scenes), encoding="utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c", "copy", "-movflags", "+faststart", str(out)],
        check=True, capture_output=True)


def main():
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    scenes = parse(src.read_text(encoding="utf-8"))
    if not scenes:
        sys.exit("Input file is empty.")
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        asyncio.run(synth(scenes, d))
        render_pngs(scenes, d)
        total = make_clips(scenes, d)
        concat(scenes, d, dst)
    print(f"Done: {dst}  ({len(scenes)} scenes, {total:.1f} s)")
    if total > 90:
        print("WARNING: Instagram API allows Reels up to 90 seconds only.")


if __name__ == "__main__":
    main()
