"""
YouTube Video Downloader — backend API (FastAPI + yt-dlp).

Run:
    uvicorn app:app --host 0.0.0.0 --port 8000

Endpoints:
    POST /api/info      {"url": "https://youtube.com/watch?v=..."}
                        -> {title, thumbnail, duration, uploader, formats:[{id,label,ext,approx_size}]}
    GET  /api/download?url=...&format_id=best|720p|480p|360p|audio
                        -> file download (mp4 / mp3)

Only youtube.com / youtu.be URLs are accepted.
"""
import os
import re
import shutil
import tempfile
import time
import uuid
from collections import defaultdict

import yt_dlp
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

app = FastAPI(title="YT Downloader API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

YT_RE = re.compile(
    r"^https?://(www\.|m\.|music\.)?(youtube\.com/(watch|shorts|embed|live)|youtu\.be/)",
    re.IGNORECASE,
)

# simple in-memory rate limit: 30 requests / minute / IP
_hits = defaultdict(list)
RATE_LIMIT = 30
WINDOW = 60


def _rate_limit(ip: str):
    now = time.time()
    _hits[ip] = [t for t in _hits[ip] if now - t < WINDOW]
    if len(_hits[ip]) >= RATE_LIMIT:
        raise HTTPException(429, "Bohat zyada requests — 1 minute ruk kar try karein.")
    _hits[ip].append(now)


def _check_url(url: str) -> str:
    url = (url or "").strip()
    if not YT_RE.match(url):
        raise HTTPException(400, "Sirf YouTube video link paste karein.")
    return url


def _base_opts():
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 20,
        # YouTube bot-check se bachne ke liye android player client
        "extractor_args": {"youtube": {"player_client": ["android"]}},
    }


FORMATS = {
    # id: (label, yt-dlp format string, ext, merge_to)
    "best": ("Best Quality (MP4)", "bestvideo+bestaudio/best", "mp4", "mp4"),
    "720p": ("720p (MP4)", "bestvideo[height<=720]+bestaudio/best[height<=720]/best", "mp4", "mp4"),
    "480p": ("480p (MP4)", "bestvideo[height<=480]+bestaudio/best[height<=480]/best", "mp4", "mp4"),
    "360p": ("360p (MP4)", "bestvideo[height<=360]+bestaudio/best[height<=360]/best", "mp4", "mp4"),
    "audio": ("Audio Only (MP3)", "bestaudio/best", "mp3", None),
}


@app.post("/api/info")
def video_info(payload: dict):
    url = _check_url(payload.get("url"))
    opts = {**_base_opts(), "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(400, f"Video ki info nahi mil saki: {str(e)[:200]}")
    except Exception:
        raise HTTPException(400, "Video ki info nahi mil saki. Link check karein.")
    if not info:
        raise HTTPException(400, "Video nahi mili.")
    return {
        "title": info.get("title"),
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader") or info.get("channel"),
        "formats": [
            {"id": fid, "label": label, "ext": ext}
            for fid, (label, _fmt, ext, _m) in FORMATS.items()
        ],
    }


@app.get("/api/download")
def download_video(url: str = Query(...), format_id: str = Query("best")):
    url = _check_url(url)
    if format_id not in FORMATS:
        raise HTTPException(400, "Ghalat format select hua.")
    label, fmt, ext, merge_to = FORMATS[format_id]

    tmpdir = tempfile.mkdtemp(prefix="ytdl_")
    outtmpl = os.path.join(tmpdir, "%(title).80s.%(ext)s")
    opts = {
        **_base_opts(),
        "format": fmt,
        "outtmpl": outtmpl,
        "noplaylist": True,
        "merge_output_format": merge_to or "mp4",
    }
    if format_id == "audio":
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            fname = ydl.prepare_filename(info)
            if format_id == "audio":
                base, _ = os.path.splitext(fname)
                fname = base + ".mp3"
    except yt_dlp.utils.DownloadError as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise HTTPException(400, f"Download fail ho gaya: {str(e)[:200]}")
    except Exception:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise HTTPException(500, "Download mein masla hua. Dobara try karein.")

    if not os.path.exists(fname):
        # yt-dlp merged file may have different ext; find the real file
        files = [os.path.join(tmpdir, f) for f in os.listdir(tmpdir)]
        files = [f for f in files if os.path.isfile(f)]
        if not files:
            shutil.rmtree(tmpdir, ignore_errors=True)
            raise HTTPException(500, "File tayyar nahi ho saki.")
        fname = max(files, key=os.path.getsize)

    safe_title = re.sub(r"[^\w\- ]", "", info.get("title") or "video")[:60].strip() or "video"
    download_name = f"{safe_title}.{ext}"
    media_type = "audio/mpeg" if ext == "mp3" else "video/mp4"

    # cleanup temp dir after response is sent
    import threading

    def _cleanup():
        time.sleep(300)
        shutil.rmtree(tmpdir, ignore_errors=True)

    threading.Thread(target=_cleanup, daemon=True).start()

    return FileResponse(
        fname, media_type=media_type, filename=download_name,
    )


@app.get("/api/health")
def health():
    return {"ok": True}
