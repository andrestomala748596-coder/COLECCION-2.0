import json
import os
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from extractor import clasificar_url, es_url_directa, extraer_pelicula, normalizar_url

SOURCE = ROOT / "urls.txt"
BASE = ROOT / "base"
MOVIES = ROOT / "peliculas"
CATEGORY_LIST = ROOT / "category_list.json"

PLATFORM_ORDER = ["ok_ru", "vimeos", "tokyvideo", "videa_hu", "directo"]
BASE_FILES = {
    "ok_ru": BASE / "ok_ru.json",
    "vimeos": BASE / "vimeos.json",
    "tokyvideo": BASE / "tokyvideo.json",
    "videa_hu": BASE / "videa_hu.json",
    "directo": BASE / "directo.json",
}
EXTRA_YTDLP_HOSTS = {"vk.com", "vkvideo.ru"}


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def read_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print(f"AVISO: {path} no se pudo leer; se conserva el contenido en memoria solo si estaba disponible.")
        return default


def normalizar_categoria(value):
    value = str(value or "GENERAL").strip().upper()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = re.sub(r"[^A-Z0-9 _-]", "", value)
    value = re.sub(r"[\s-]+", "_", value).strip("_")
    return value or "GENERAL"


def obtener_id(movie):
    return movie.get("ID_VIDEO") or movie.get("ID_OKRU") or movie.get("TMDB_ID") or movie.get("ID")


def urls_de(movie):
    urls = movie.get("URLS")
    if not isinstance(urls, list):
        urls = movie.get("URLS_OKRU", [])
    if isinstance(urls, str):
        return [urls]
    return urls if isinstance(urls, list) else []


def merge_movies(existing, new_movies):
    data = [dict(x) for x in existing if isinstance(x, dict)] if isinstance(existing, list) else []
    positions = {}
    for i, movie in enumerate(data):
        mid = obtener_id(movie)
        if mid is not None:
            positions[str(mid)] = i

    for movie in new_movies:
        mid = obtener_id(movie)
        key = str(mid) if mid is not None else None
        if key is not None and key in positions:
            old = data[positions[key]]
            merged = dict(old)
            merged.update(movie)
            # Nunca perder una URL directa/extracción válida anterior cuando
            # la ejecución actual no produjo un resultado nuevo.
            new_urls = movie.get("URLS_DIRECTAS")
            old_urls = old.get("URLS_DIRECTAS")
            if not new_urls and old_urls:
                merged["URLS_DIRECTAS"] = old_urls
                merged["URL_DIRECTA"] = old.get("URL_DIRECTA", old_urls[0] if old_urls else "")
            old_cache = old.get("_EXTRACTED_BY_URL", {})
            new_cache = movie.get("_EXTRACTED_BY_URL", {})
            if isinstance(old_cache, dict) and isinstance(new_cache, dict):
                merged["_EXTRACTED_BY_URL"] = {**old_cache, **new_cache}
            data[positions[key]] = merged
        else:
            if key is not None:
                positions[key] = len(data)
            data.append(movie)
    return data


def main():
    BASE.mkdir(parents=True, exist_ok=True)
    MOVIES.mkdir(parents=True, exist_ok=True)

    if not SOURCE.exists():
        raise SystemExit("ERROR: falta urls.txt")

    raw = SOURCE.read_text(encoding="utf-8").strip()
    if not raw:
        raise SystemExit("ERROR: urls.txt está vacío; no se modificará el catálogo.")
    try:
        movies = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"ERROR: urls.txt no contiene JSON válido: {exc}")
    if not isinstance(movies, list) or not movies:
        raise SystemExit("ERROR: urls.txt debe ser una lista JSON no vacía; no se modificará el catálogo.")

    # Lee los datos anteriores y los incorpora al clasificador para que las
    # actualizaciones no eliminen películas no incluidas en el archivo entrante.
    grouped = {name: [] for name in BASE_FILES}
    for movie in movies:
        if not isinstance(movie, dict):
            continue
        current = dict(movie)
        urls = urls_de(current)
        if not urls:
            # Un registro sin URL se conserva en la categoría y no se descarta.
            grouped["directo"].append(current)
            continue
        assigned = set()
        for url in urls:
            group = clasificar_url(url, current)
            if group in grouped:
                assigned.add(group)
        if not assigned:
            # No tirar registros de plataformas no reconocidas; quedan en directo
            # como registros conservados, pero no se intenta extraerlos.
            assigned.add("directo")
        for group in assigned:
            grouped[group].append(current)

    # La base se actualiza por plataforma. Las películas anteriores permanecen.
    for group, path in BASE_FILES.items():
        previous = read_json(path, [])
        atomic_json(path, merge_movies(previous, grouped[group]))

    # Procesar una plataforma a la vez. Después de cada plataforma, fusionar
    # lo que ya exista y publicar categorías, sin borrar el directorio.
    for platform in PLATFORM_ORDER:
        print(f"\n===== PLATAFORMA: {platform} =====", flush=True)
        source_records = read_json(BASE_FILES[platform], [])
        processed = []
        for index, movie in enumerate(source_records):
            if not isinstance(movie, dict):
                continue
            print(f"[{platform}] {index + 1}/{len(source_records)}: {movie.get('TITULO', 'Sin título')}", flush=True)
            if platform == "directo":
                result = dict(movie)
                urls = urls_de(result)
                directas = result.get("URLS_DIRECTAS", [])
                if not isinstance(directas, list):
                    directas = [directas] if directas else []
                for url in urls:
                    if es_url_directa(url, result) and url not in directas:
                        directas.append(url)
                result["URLS_DIRECTAS"] = directas
                result["URL_DIRECTA"] = directas[0] if directas else result.get("URL_DIRECTA", "")
            else:
                result = extraer_pelicula(movie, platform)
            processed.append(result)

        # Guardar cada base al terminar su plataforma.
        old_base = read_json(BASE_FILES[platform], [])
        atomic_json(BASE_FILES[platform], merge_movies(old_base, processed))

        # Fusionar los resultados de esta plataforma con el catálogo que ya existe.
        by_category = {}
        for movie in processed:
            category = normalizar_categoria(movie.get("CATEGORIA", "GENERAL"))
            movie["CATEGORIA"] = category
            by_category.setdefault(category, []).append(movie)

        for category, entries in by_category.items():
            path = MOVIES / f"{category}.json"
            previous = read_json(path, [])
            atomic_json(path, merge_movies(previous, entries))
            print(f"Actualizada {path.relative_to(ROOT)}: {len(read_json(path, []))} registros", flush=True)

        # category_list se actualiza incrementalmente sin eliminar categorías existentes.
        existing_categories = {
            p.stem for p in MOVIES.glob("*.json") if p.is_file()
        }
        category_list = sorted(existing_categories)
        atomic_json(CATEGORY_LIST, category_list)

    print("\nProceso terminado. No se borró la carpeta peliculas/.", flush=True)


if __name__ == "__main__":
    main()

