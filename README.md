# AJPLAY-MAX-EXTRACTORES

Proyecto Python para GitHub Actions que clasifica enlaces, extrae una plataforma por vez y mantiene el catálogo `peliculas/` sin borrarlo durante las actualizaciones.

## Estructura

```text
.
├── urls.txt
├── extractor.py
├── requirements.txt
├── base/
│   ├── ok_ru.json
│   ├── vimeos.json
│   ├── tokyvideo.json
│   ├── videa_hu.json
│   └── directo.json
├── peliculas/
│   └── .gitkeep
├── category_list.json
├── scripts/
│   └── pipeline.py
└── .github/workflows/extractor.yml
```

## Qué debes crear en GitHub

1. Crea un repositorio nuevo, por ejemplo `AJPLAY-MAX-EXTRACTORES`.
2. Sube **todos los archivos de este proyecto**, respetando las carpetas.
3. Revisa `urls.txt`: debe contener un array JSON (`[...]`) con objetos de películas. No pegues una lista M3U EXTINF directamente; conviértela a los objetos JSON que utiliza tu catálogo.
4. En GitHub, abre **Actions** y habilita los workflows si aparece el botón.
5. En **Settings → Actions → General → Workflow permissions**, habilita `Read and write permissions`, para que Actions pueda guardar los JSON generados en el repositorio.
6. Haz un commit que cambie `urls.txt`. El workflow se ejecutará automáticamente. También puedes ejecutarlo desde **Actions → AJPLAY Catalog Extractor → Run workflow**.

## Activadores

- Cuando se suba o cambie `urls.txt` en la rama predeterminada.
- Cada 6 horas (`0 */6 * * *`, UTC).
- Ejecución manual desde la pestaña Actions.

GitHub programa el cron en UTC y puede retrasarlo si hay mucha carga.

## Orden de procesamiento

1. Clasifica todas las películas en `base/` por plataforma.
2. Procesa OK.ru.
3. Procesa Vimeos.
4. Procesa Tokyvideo.
5. Procesa Videa.hu.
6. Conserva las URL directas sin pasarlas por extractor.
7. Después de cada plataforma, fusiona sus resultados en `peliculas/<CATEGORIA>.json`.
8. Actualiza `category_list.json`.

## Protección del catálogo

- No se elimina ni se vacía `peliculas/`.
- Los JSON nuevos se escriben en un archivo temporal y después se reemplazan.
- Una extracción fallida conserva los enlaces directos/extraídos válidos anteriores de esa película.
- Un solo workflow puede correr a la vez para evitar commits concurrentes.
- El workflow solo se dispara por cambios en `urls.txt`, el workflow o el código del pipeline, por lo que el commit automático de resultados no crea un bucle infinito.

## Límites importantes

- La lógica de OK.ru y Vimeos está portada del extractor compartido.
- VK, Videa.hu y Tokyvideo usan `yt-dlp`; las páginas de esas plataformas pueden cambiar y una extracción puede fallar.
- Los enlaces directos se identifican por el campo `PLATAFORMA` (`BUDTV Direct`, `DIRECTO`, etc.) o por URL de archivo multimedia. Se conservan tal como vienen.
- No se garantiza que las URL extraídas sean permanentes; depende de cada plataforma.
