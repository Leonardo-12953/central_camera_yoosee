"""Resolve fontes públicas (YouTube ao vivo) para uma URL HLS usável no FFmpeg.

O viewer continua sem dependências extras para câmeras RTSP comuns. Para fontes
públicas do YouTube, `yt-dlp` é usado apenas quando necessário; como as URLs HLS
do YouTube são assinadas e expiram, a resolução é refeita a cada reinício do
FFmpeg (com um cache curto para evitar chamadas repetidas).
"""
from __future__ import annotations

import re
import time
from urllib.parse import parse_qs, urlsplit

_CACHE: dict[str, tuple[float, str]] = {}
_CACHE_SECONDS = 10 * 60


def is_youtube(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return host == "youtu.be" or host.endswith("youtube.com") or host.endswith("youtube-nocookie.com")


def _public_url(url: str) -> str:
    """Remove tokens/assinaturas antes de mostrar a URL na interface."""
    parts = urlsplit(url)
    if "youtube" not in (parts.hostname or ""):
        return url
    query = parse_qs(parts.query)
    video_id = (query.get("v") or [""])[0]
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    return f"https://{parts.netloc}{parts.path}"


def display_url(url: str) -> str:
    """Versão segura para exibição em logs/cartões (sem query assinada)."""
    return _public_url(url)


_SIGNED_URL_RE = re.compile(r"https?://[^\s'\"]*googlevideo\.com/[^\s'\"]*", re.IGNORECASE)


def redact_stream_urls(text: str) -> str:
    """Remove URLs assinadas que o FFmpeg costuma imprimir por segmento."""
    return _SIGNED_URL_RE.sub("<stream-publico>", text)


def is_noisy_source_line(line: str) -> bool:
    """Linhas de abertura de segmento HLS só poluem o log da fonte pública."""
    return "Opening '" in line and "googlevideo.com" in line


def resolve_source(url: str) -> str:
    """Devolve a URL final para o FFmpeg; YouTube é resolvido via yt-dlp."""
    url = (url or "").strip()
    if not url:
        raise RuntimeError("URL da fonte pública vazia")
    if not is_youtube(url):
        return url

    cached = _CACHE.get(url)
    if cached and time.monotonic() - cached[0] < _CACHE_SECONDS:
        return cached[1]

    try:
        import yt_dlp  # import tardio: só câmeras públicas precisam dele
    except ImportError as exc:  # pragma: no cover - dependência opcional
        raise RuntimeError(
            "yt-dlp não está instalado no Python do viewer (pip install yt-dlp)"
        ) from exc

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001 - yt-dlp tem vários erros próprios
        raise RuntimeError(f"não foi possível resolver a transmissão pública: {exc}") from exc

    if not isinstance(info, dict):
        raise RuntimeError("resposta inválida da transmissão pública")

    candidates = []
    for item in info.get("formats") or []:
        if not isinstance(item, dict) or not item.get("url"):
            continue
        if item.get("vcodec") in (None, "none"):
            continue
        protocol = str(item.get("protocol") or "")
        if "m3u8" not in protocol and not str(item.get("url")).split("?", 1)[0].endswith(".m3u8"):
            continue
        height = int(item.get("height") or 0)
        # Para o teste de detecção, 720p é mais que suficiente e gasta menos CPU.
        if height > 720:
            continue
        candidates.append((height, int(item.get("tbr") or 0), str(item["url"])))

    if not candidates:
        raise RuntimeError("a transmissão pública não oferece um vídeo HLS")

    # Prefere a maior resolução até 720p; desempata por bitrate.
    candidates.sort(key=lambda item: (item[0], item[1]))
    resolved = candidates[-1][2]
    _CACHE[url] = (time.monotonic(), resolved)
    return resolved
