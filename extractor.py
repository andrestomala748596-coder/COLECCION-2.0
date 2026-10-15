"""Extractor para AJPLAY-MAX-EXTRACTORES.

Funciones públicas consumidas por scripts/pipeline.py:
  normalizar_url(url)
  clasificar_url(url, pelicula=None)
  es_url_directa(url, pelicula=None)
  extraer_pelicula(pelicula, plataforma)

Las URLs originales y los resultados anteriores se conservan si un proveedor
falla. No se descarga el vídeo; se resuelve la URL reproducible.
"""
from __future__ import annotations

import html
import json
import re
import subprocess
from urllib.parse import urlparse, urlunparse

import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)
TIMEOUT = 30
YT_DLP_TIMEOUT = 75


def normalizar_url(value):
    """Normaliza una URL sin alterar parámetros firmados."""
    if not isinstance(value, str):
        return value
    value = html.unescape(value).strip().replace(r"\/", "/")
    value = value.replace("\\u0026", "&").replace("\\u003D", "=")
    if value.startswith("//"):
        value = "https:" + value
    elif value and not re.match(r"^https?://", value, re.I):
        value = "https://" + value
    return value


def _host(url):
    try:
        return (urlparse(normalizar_url(url)).hostname or "").lower().rstrip(".")
    except Exception:
        return ""


def es_url_directa(url, pelicula=None):
    """True para streams/archivos directos. BUDTV Direct se considera directo."""
    if not isinstance(url, str) or not url.strip():
        return False
    parsed = urlparse(normalizar_url(url))
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    plataforma = ""
    if isinstance(pelicula, dict):
        plataforma = str(pelicula.get("PLATAFORMA", "")).strip().lower()
    if "budtv direct" in plataforma:
        return True
    path = parsed.path.lower()
    return bool(re.search(r"\.(?:mp4|m4v|mkv|mov|webm|m3u8|mpd|ts)(?:$|/)", path))


def clasificar_url(url, pelicula=None):
    """Devuelve una de las claves de base usadas por el pipeline."""
    host = _host(url)
    platform = ""
    if isinstance(pelicula, dict):
        platform = str(pelicula.get("PLATAFORMA", "")).lower()
    if "budtv direct" in platform or es_url_directa(url, pelicula):
        return "directo"
    if host == "ok.ru" or host.endswith(".ok.ru"):
        return "ok_ru"
    if host == "vimeos.net" or host.endswith(".vimeos.net"):
        return "vimeos"
    if host == "tokyvideo.com" or host.endswith(".tokyvideo.com"):
        return "tokyvideo"
    if host == "videa.hu" or host.endswith(".videa.hu"):
        return "videa_hu"
    # VK se resuelve con yt-dlp y comparte la etapa tokyvideo del pipeline.
    if host in {"vk.com", "www.vk.com", "vkvideo.ru", "www.vkvideo.ru"} or host.endswith(".vk.com"):
        return "tokyvideo"
    return "directo"


def _json_object_after(text, marker='"metadata"'):
    """Extrae el primer objeto JSON balanceado después de metadata."""
    match = re.search(marker + r"\s*:\s*\{", text, re.I)
    if not match:
        # Algunas páginas contienen JSON escapado dentro de un string.
        match = re.search(r'\\?"metadata\\?"\s*:\s*\{', text, re.I)
    if not match:
        return None
    start = text.find("{", match.start(), match.end())
    depth, in_string, escape = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_string:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _quality(video):
    def number(v):
        try:
            return float(v or 0)
        except (ValueError, TypeError):
            return 0.0
    width, height, bitrate = number(video.get("width")), number(video.get("height")), number(video.get("bitrate"))
    score = width * height * 1000 + bitrate
    name = str(video.get("name", "")).lower()
    levels = {"2160": 7, "4k": 7, "1440": 6, "1080": 5, "full_hd": 5, "fullhd": 5, "720": 4, "hd": 4, "480": 3, "360": 2, "240": 1}
    for token, level in levels.items():
        if token in name:
            score += level * 100000
    try:
        score += int(video.get("type")) * 1000000
    except (ValueError, TypeError):
        pass
    return score


def _extract_okru(url):
    headers = {"User-Agent": USER_AGENT, "Referer": "https://ok.ru/", "Accept-Language": "es,en;q=0.8"}
    response = requests.get(url, headers=headers, timeout=TIMEOUT)
    response.raise_for_status()
    text = html.unescape(response.text).replace(r"\/", "/")
    data = _json_object_after(text)
    if not isinstance(data, dict):
        return None
    videos = data.get("videos")
    candidates = []
    if isinstance(videos, list):
        for video in videos:
            if not isinstance(video, dict):
                continue
            direct = normalizar_url(video.get("url", ""))
            if direct.startswith(("http://", "https://")):
                candidates.append((_quality(video), direct))
    if candidates:
        return max(candidates, key=lambda x: x[0])[1]
    for key in ("ondemandHls", "ondemandDash"):
        direct = normalizar_url(data.get(key, ""))
        if direct.startswith(("http://", "https://")):
            return direct
    return None


def _base_n(num, base, alphabet="0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"):
    if num == 0:
        return "0"
    result = ""
    while num:
        result = alphabet[num % base] + result
        num //= base
    return result


def _extract_vimeos(url):
    """Prioriza master.m3u8 sin filtrar por dominio ni extensión de origen."""
    from urllib.parse import urljoin

    headers = {
        "User-Agent": USER_AGENT,
        "Referer": url,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    response = requests.get(url, headers=headers, timeout=TIMEOUT, allow_redirects=True)
    response.raise_for_status()
    page = html.unescape(response.text).replace(r"\/", "/")
    scripts = re.findall(r"<script\b[^>]*>(.*?)</script\s*>", page, re.I | re.S)
    sources = [page, *scripts]

    # Desempaqueta scripts Dean-Edwards cuando los argumentos se reconocen.
    unpacked = []
    packer = re.compile(
        r"eval\(function\(p,a,c,k,e,d\).*?\}\(\s*(['\"])(.*?)\1\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(['\"])(.*?)\5\.split\(['\"]\|['\"]\)",
        re.S,
    )
    for script in scripts:
        if "eval(function(p,a,c,k,e,d)" not in script:
            continue
        match = packer.search(script)
        if not match:
            continue
        try:
            packed, base, count, words_raw = match.group(2), int(match.group(3)), int(match.group(4)), match.group(6)
            words = words_raw.split("|")
            for index in range(count - 1, -1, -1):
                if index < len(words) and words[index]:
                    token = _base_n(index, base)
                    packed = re.sub(r"\b" + re.escape(token) + r"\b", lambda _: words[index], packed)
            unpacked.append(packed)
        except Exception:
            continue
    sources.extend(unpacked)

    # No se exige vimeos.net ni una extensión específica para las candidatas.
    candidates = []
    url_pattern = re.compile(r"(?:https?:)?//[^\s\"'<>\\]+|(?:[\w./?=&%-]*master\.m3u8[\w./?=&%-]*)", re.I)
    for source in sources:
        source = source.replace(r"\u0026", "&").replace(r"\u003D", "=").replace(r"\/", "/")
        for raw in url_pattern.findall(source):
            raw = html.unescape(raw).rstrip("),;]}\\")
            if raw.startswith("//"):
                raw = "https:" + raw
            elif raw.startswith("/") or raw.lower().startswith("master.m3u8"):
                raw = urljoin(response.url, raw)
            if raw.startswith(("http://", "https://")):
                candidates.append(raw)

    candidates = list(dict.fromkeys(candidates))
    candidates.sort(key=lambda item: ("master.m3u8" not in urlparse(item).path.lower(), item.lower().find("master.m3u8") if "master.m3u8" in item.lower() else 999999))
    fallback_hls = None
    for candidate in candidates:
        try:
            check_headers = {
                "User-Agent": USER_AGENT,
                "Referer": response.url,
                "Accept": "application/vnd.apple.mpegurl,application/x-mpegURL,*/*",
            }
            with requests.get(candidate, headers=check_headers, timeout=TIMEOUT, stream=True, allow_redirects=True) as check:
                if not (200 <= check.status_code < 300):
                    continue
                content_type = check.headers.get("Content-Type", "").lower()
                sample = next(check.iter_content(chunk_size=4096), b"")
                if b"#EXTM3U" not in sample[:2048] and "mpegurl" not in content_type:
                    continue
                if "master.m3u8" in urlparse(candidate).path.lower():
                    return candidate
                if fallback_hls is None:
                    fallback_hls = candidate
        except requests.RequestException:
            continue
    return fallback_hls


def _extract_ytdlp(url):
    command = ["yt-dlp", "--no-warnings", "--no-playlist", "--skip-download", "--dump-single-json", url]
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=YT_DLP_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        # Compatibilidad con versiones/formats donde -g sí devuelve una URL.
        try:
            fallback = subprocess.run(["yt-dlp", "--no-warnings", "--no-playlist", "-g", url], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=YT_DLP_TIMEOUT, check=False)
            if fallback.returncode == 0:
                urls = [line.strip() for line in fallback.stdout.splitlines() if line.strip().startswith(("http://", "https://"))]
                return urls[0] if urls else None
        except (OSError, subprocess.TimeoutExpired):
            return None
        return None
    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    # Prefer the highest format that contains a video stream and a resolved URL.
    formats = info.get("formats") if isinstance(info, dict) else None
    candidates = []
    if isinstance(formats, list):
        for fmt in formats:
            if not isinstance(fmt, dict):
                continue
            direct = fmt.get("url")
            if not isinstance(direct, str) or not direct.startswith(("http://", "https://")):
                continue
            if fmt.get("vcodec") == "none":
                continue
            try:
                height = int(fmt.get("height") or 0)
            except (ValueError, TypeError):
                height = 0
            try:
                bitrate = float(fmt.get("tbr") or 0)
            except (ValueError, TypeError):
                bitrate = 0
            candidates.append(((height, bitrate), direct))
    if candidates:
        return max(candidates, key=lambda item: item[0])[1]
    direct = info.get("url") if isinstance(info, dict) else None
    return direct if isinstance(direct, str) and direct.startswith(("http://", "https://")) else None


def _source_urls(movie):
    value = movie.get("URLS")
    if not isinstance(value, list):
        value = movie.get("URLS_OKRU", movie.get("URL", movie.get("url", [])))
    if isinstance(value, str):
        value = [value]
    return [normalizar_url(x) for x in value if isinstance(x, str) and x.strip()] if isinstance(value, list) else []


def _extract_one(url, platform):
    if platform == "ok_ru":
        return _extract_okru(url)
    if platform == "vimeos":
        return _extract_vimeos(url)
    if platform in {"tokyvideo", "videa_hu"}:
        return _extract_ytdlp(url)
    return None


def extraer_pelicula(pelicula, plataforma):
    """Procesa una película y conserva las URLs extraídas previamente si falla."""
    result = dict(pelicula) if isinstance(pelicula, dict) else {}
    urls = _source_urls(result)
    previous = result.get("URLS_DIRECTAS", [])
    if isinstance(previous, str):
        previous = [previous] if previous else []
    if not isinstance(previous, list):
        previous = []
    direct_urls = list(dict.fromkeys(x for x in previous if isinstance(x, str) and x.startswith(("http://", "https://"))))
    cache = result.get("_EXTRACTED_BY_URL", {})
    if not isinstance(cache, dict):
        cache = {}
    failures = []

    for source_url in urls:
        if clasificar_url(source_url, result) != plataforma:
            continue
        if es_url_directa(source_url, result):
            if source_url not in direct_urls:
                direct_urls.append(source_url)
            continue
        cached = cache.get(source_url)
        if isinstance(cached, str) and cached.startswith(("http://", "https://")):
            if cached not in direct_urls:
                direct_urls.append(cached)
            continue
        try:
            extracted = _extract_one(source_url, plataforma)
        except Exception as exc:
            extracted = None
            failures.append(f"{type(exc).__name__}: {exc}")
        if extracted and extracted.startswith(("http://", "https://")):
            cache[source_url] = extracted
            if extracted not in direct_urls:
                direct_urls.append(extracted)
        else:
            failures.append(f"No se pudo extraer: {source_url}")

    result["URLS_DIRECTAS"] = direct_urls
    if direct_urls:
        result["URL_DIRECTA"] = direct_urls[0]
    else:
        # No borrar un resultado válido que ya estuviera en el registro.
        old = result.get("URL_DIRECTA")
        if isinstance(old, str) and old.startswith(("http://", "https://")):
            result["URLS_DIRECTAS"] = [old]
        else:
            result["URL_DIRECTA"] = ""
    result["_EXTRACTED_BY_URL"] = cache
    if failures:
        result["_EXTRACTION_ERRORS"] = failures
    else:
        result.pop("_EXTRACTION_ERRORS", None)
    return result
