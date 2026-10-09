#!/usr/bin/env python3
# ============================================================
# Script: iptv_github.py
# Descripción: Descarga canales
#              IPTV, los filtra por países hispanohablantes,
#              los prueba y genera listas M3U + README.md.
#              El commit/push lo hace el workflow de Actions.
# Autor: IamJony https://github.com/IamJony
# Licencia: MIT
# ============================================================

import os
import sys
import json
import urllib.request
import urllib.error
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from collections import defaultdict

# ---------- Colores ----------
class C:
    RED    = '\033[0;31m'
    GREEN  = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE   = '\033[0;34m'
    CYAN   = '\033[0;36m'
    WHITE  = '\033[1;37m'
    NC     = '\033[0m'

# ============================================================
# CONFIGURACIÓN (detecta CI vs local)
# ============================================================
EN_CI = os.environ.get("GITHUB_ACTIONS") == "true"

if EN_CI:
    BASE_DIR = Path.cwd()
    WORK_DIR = Path("generated")
else:
    BASE_DIR = Path("/mnt/user/j/Projectos/python/iptv-m3u")
    WORK_DIR = BASE_DIR / "iptv_downloads"

WORK_DIR.mkdir(parents=True, exist_ok=True)

CHANNELS_URL = "https://iptv-org.github.io/api/channels.json"
STREAMS_URL  = "https://iptv-org.github.io/api/streams.json"

# Países hispanohablantes (ISO 3166-1 alpha-2)
PAISES_ES = [
    "AR", "BO", "CL", "CO", "CR", "CU", "DO", "EC", "SV", "GQ", "GT",
    "HN", "MX", "NI", "PA", "PY", "PE", "PR", "ES", "UY", "VE",
]

PROBAR       = True
GENERAR_TXT  = True
GENERAR_M3U  = True

UNIFICADO_M3U = WORK_DIR / "es_unificado.m3u"
UNIFICADO_TXT = WORK_DIR / "es_unificado.txt"

README_TEMPLATE = BASE_DIR / "README.md.template"
README_OUT      = WORK_DIR / "README.md"

REPO_URL_RAW = "https://raw.githubusercontent.com/IamJony/IPTVListaLibre/refs/heads/main"

MAX_WORKERS   = 100
TIMEOUT       = 4
MAX_POR_CANAL = 3

NOMBRES_PAISES = {
    "AR": "🇦🇷 Argentina",       "BO": "🇧🇴 Bolivia",
    "CL": "🇨🇱 Chile",           "CO": "🇨🇴 Colombia",
    "CR": "🇨🇷 Costa Rica",      "CU": "🇨🇺 Cuba",
    "DO": "🇩🇴 Rep. Dominicana", "EC": "🇪🇨 Ecuador",
    "SV": "🇸🇻 El Salvador",     "GQ": "🇬🇶 Guinea Ecuatorial",
    "GT": "🇬🇹 Guatemala",       "HN": "🇭🇳 Honduras",
    "MX": "🇲🇽 México",          "NI": "🇳🇮 Nicaragua",
    "PA": "🇵🇦 Panamá",          "PY": "🇵🇾 Paraguay",
    "PE": "🇵🇪 Perú",            "PR": "🇵🇷 Puerto Rico",
    "ES": "🇪🇸 España",          "UY": "🇺🇾 Uruguay",
    "VE": "🇻🇪 Venezuela",
}

# ============================================================
# UTILIDADES
# ============================================================
def log(msg, color=C.NC):
    print(f"{color}{msg}{C.NC}", flush=True)


def descargar_json(url):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'IPTVManager/2.0'})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        log(f"Error al descargar {url}: {e}", C.RED)
        sys.exit(1)


def obtener_logo(canal):
    """Devuelve la URL del logo del canal inspeccionando posibles estructuras de la API iptv-org."""
    if not isinstance(canal, dict):
        return ""
    
    # Check 1: Campo directo 'logo' si es una string URL
    logo = canal.get("logo")
    if isinstance(logo, str) and logo.strip():
        return logo.strip()
    
    # Check 2: Campo 'images' (Lista de diccionarios o URLs)
    images = canal.get("images")
    if isinstance(images, list) and len(images) > 0:
        primera_img = images[0]
        if isinstance(primera_img, dict) and primera_img.get("url"):
            return primera_img["url"].strip()
        elif isinstance(primera_img, str) and primera_img.strip():
            return primera_img.strip()

    return ""

# ============================================================
# PRUEBA DE STREAMS
# ============================================================
def probar_stream(url):
    try:
        req = urllib.request.Request(url, method='HEAD',
                                     headers={'User-Agent': 'IPTVManager/2.0'})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return 200 <= resp.status < 400
    except urllib.error.HTTPError as e:
        if e.code in (403, 405, 501):
            try:
                req = urllib.request.Request(
                    url, headers={'User-Agent': 'IPTVManager/2.0',
                                  'Range': 'bytes=0-1024'})
                with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                    return 200 <= resp.status < 400
            except Exception:
                return False
        return False
    except Exception:
        return False


def probar_streams_concurrente(streams, etiqueta="streams"):
    resultados = {}
    total = len(streams)
    if total == 0:
        return resultados

    procesados = 0
    ok = 0
    fail = 0

    log(f"Probando {total} {etiqueta} con {MAX_WORKERS} hilos...", C.CYAN)

    def barra():
        ancho = 40
        llenos = int(ancho * procesados / total) if total else 0
        vacios = ancho - llenos
        barra_str = "[" + "#" * llenos + " " * vacios + "]"
        pct = int(100 * procesados / total) if total else 0
        print(f"\r{C.CYAN}{barra_str} {C.WHITE}{pct}%{C.NC} "
              f"{C.BLUE}[{procesados}/{total}]{C.NC} "
              f"{C.GREEN}OK {ok}{C.NC} {C.RED}FAIL {fail}{C.NC}",
              end="", flush=True)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futuros = {executor.submit(probar_stream, s["url"]): s for s in streams}
        for fut in as_completed(futuros):
            s = futuros[fut]
            try:
                res = fut.result()
            except Exception:
                res = False
            resultados[s["url"]] = res
            procesados += 1
            if res:
                ok += 1
            else:
                fail += 1
            barra()

    print()
    return resultados

# ============================================================
# RECOLECCIÓN DE STREAMS
# ============================================================
def recolectar_streams_paises(paises, channels, streams):
    canal_por_id = {ch["id"]: ch for ch in channels}
    ids_por_pais = {p: {ch["id"] for ch in channels
                        if ch.get("country") == p} for p in paises}

    streams_por_pais = defaultdict(list)
    for s in streams:
        ch_id = s.get("channel")
        if not ch_id:
            continue
        for p, ids in ids_por_pais.items():
            if ch_id in ids:
                streams_por_pais[p].append(s)
                break

    vistos_url = set()
    contador_canal = defaultdict(int)
    todos_streams = []

    for p in paises:
        for s in streams_por_pais[p]:
            url = s.get("url", "")
            ch_id = s.get("channel", "")
            if not url or url in vistos_url:
                continue
            if contador_canal[ch_id] >= MAX_POR_CANAL:
                continue
            vistos_url.add(url)
            contador_canal[ch_id] += 1
            todos_streams.append(s)

    return streams_por_pais, todos_streams, canal_por_id

# ============================================================
# GENERACIÓN DE LISTADOS POR PAÍS
# ============================================================
def _escribir_archivos(pais, streams_pais, canal_por_id, resultados,
                       txt_file, m3u_file, titulo, group_title):
    total_ok = 0
    total_fail = 0

    ftxt = open(txt_file, "w", encoding="utf-8") if GENERAR_TXT else None
    fm3u = open(m3u_file, "w", encoding="utf-8") if GENERAR_M3U else None

    try:
        if ftxt:
            ftxt.write("=" * 45 + "\n")
            ftxt.write(f"  {titulo}\n")
            ftxt.write("  Generado por IamJony (https://github.com/IamJony)\n")
            ftxt.write(f"  Fecha: {datetime.now()}\n")
            ftxt.write("=" * 45 + "\n\n")

        if fm3u:
            fm3u.write("#EXTM3U\n")
            fm3u.write(f"# {titulo}\n")
            fm3u.write("# Generada por IamJony (https://github.com/IamJony)\n")
            fm3u.write(f"# Fecha: {datetime.now()}\n\n")

        for s in streams_pais:
            url = s.get("url", "")
            if not url:
                continue
            ch_id = s.get("channel", "")
            canal = canal_por_id.get(ch_id, {})
            nombre = canal.get("name") or ch_id
            logo = obtener_logo(canal)
            quality = s.get("quality") or "No especificada"
            audio = s.get("audio_lang") or "No especificado"

            if PROBAR:
                estado = "FUNCIONA" if resultados.get(url) else "FALLA"
            else:
                estado = "SIN PROBAR"

            if estado == "FUNCIONA":
                total_ok += 1
            elif estado == "FALLA":
                total_fail += 1
            else:
                total_ok += 1

            if ftxt:
                ftxt.write(f"Nombre: {nombre}\n")
                ftxt.write(f"Calidad: {quality}\n")
                ftxt.write(f"Idioma: {audio}\n")
                ftxt.write(f"Logo: {logo or 'No disponible'}\n")
                ftxt.write(f"URL: {url}\n")
                ftxt.write(f"Estado: {estado}\n")
                ftxt.write("---\n")

            if fm3u and estado != "FALLA":
                attrs = f'tvg-id="{ch_id}" tvg-name="{nombre}"'
                if logo:
                    attrs += f' tvg-logo="{logo}"'
                if pais:
                    attrs += f' tvg-country="{pais}"'
                    
                fm3u.write(f'#EXTINF:-1 {attrs} group-title="{group_title}",{nombre}\n')
                fm3u.write(f"{url}\n\n")

        if ftxt:
            ftxt.write("\n" + "=" * 45 + "\n")
            ftxt.write("  RESUMEN\n")
            ftxt.write(f"  Total de streams: {len(streams_pais)}\n")
            ftxt.write(f"  Funcionan: {total_ok}\n")
            if PROBAR:
                ftxt.write(f"  Fallan: {total_fail}\n")
            ftxt.write("=" * 45 + "\n")
    finally:
        if ftxt:
            ftxt.close()
        if fm3u:
            fm3u.close()

    log(f"M3U: {m3u_file} — {total_ok}/{len(streams_pais)} canales", C.CYAN)
    return m3u_file


def generar_listados(pais, channels, streams, resultados=None):
    txt_file = WORK_DIR / f"{pais.lower()}_reporte.txt"
    m3u_file = WORK_DIR / f"{pais.lower()}_canales.m3u"

    ids_pais = {ch["id"] for ch in channels if ch.get("country") == pais}
    if not ids_pais:
        return None
    streams_pais = [s for s in streams if s.get("channel") in ids_pais]
    if not streams_pais:
        return None

    canal_por_id = {ch["id"]: ch for ch in channels}

    if resultados is None and PROBAR:
        resultados = probar_streams_concurrente(streams_pais, etiqueta=f"streams de {pais}")
    elif resultados is None:
        resultados = {}

    return _escribir_archivos(
        pais=pais, streams_pais=streams_pais, canal_por_id=canal_por_id,
        resultados=resultados, txt_file=txt_file, m3u_file=m3u_file,
        titulo=f"REPORTE DE CANALES IPTV - {pais}", group_title=pais,
    )

# ============================================================
# GENERACIÓN DE LISTA UNIFICADA
# ============================================================
def generar_unificado(paises, streams_por_pais, todos_streams, canal_por_id,
                      resultados=None):
    log(f"\nGENERANDO LISTA UNIFICADA EN ESPAÑOL", C.CYAN)
    log(f"Total de streams únicos: {len(todos_streams)}", C.CYAN)

    if resultados is None:
        resultados = {}
        if PROBAR and todos_streams:
            resultados = probar_streams_concurrente(todos_streams,
                                                    etiqueta="streams (unificado)")
    else:
        log("Reutilizando resultados de la prueba anterior ✅", C.GREEN)

    total_ok = 0
    total_fail = 0
    canales_unicos = set()
    paises_stats = defaultdict(lambda: {"ok": 0, "fail": 0, "total": 0})

    with open(UNIFICADO_TXT, "w", encoding="utf-8") as ftxt, \
         open(UNIFICADO_M3U, "w", encoding="utf-8") as fm3u:

        ftxt.write("=" * 60 + "\n")
        ftxt.write("  REPORTE UNIFICADO DE CANALES IPTV EN ESPAÑOL\n")
        ftxt.write(f"  Países: {', '.join(paises)}\n")
        ftxt.write("  Generado por IamJony (https://github.com/IamJony)\n")
        ftxt.write(f"  Fecha: {datetime.now()}\n")
        ftxt.write("=" * 60 + "\n\n")

        fm3u.write("#EXTM3U\n")
        fm3u.write("# Lista unificada de canales IPTV en español\n")
        fm3u.write(f"# Países: {', '.join(paises)}\n")
        fm3u.write("# Generada por IamJony (https://github.com/IamJony)\n")
        fm3u.write(f"# Fecha: {datetime.now()}\n\n")

        for s in todos_streams:
            url = s.get("url", "")
            if not url:
                continue
            ch_id = s.get("channel", "")
            canal = canal_por_id.get(ch_id, {})
            nombre = canal.get("name") or ch_id
            country = canal.get("country", "??")
            logo = obtener_logo(canal)
            quality = s.get("quality") or "No especificada"

            if PROBAR:
                estado = "FUNCIONA" if resultados.get(url) else "FALLA"
            else:
                estado = "SIN PROBAR"

            paises_stats[country]["total"] += 1
            if estado == "FUNCIONA":
                total_ok += 1
                paises_stats[country]["ok"] += 1
                canales_unicos.add(ch_id)
            elif estado == "FALLA":
                total_fail += 1
                paises_stats[country]["fail"] += 1
            else:
                total_ok += 1
                canales_unicos.add(ch_id)

            ftxt.write(f"Nombre: {nombre}\n")
            ftxt.write(f"País: {country}\n")
            ftxt.write(f"Calidad: {quality}\n")
            ftxt.write(f"Logo: {logo or 'No disponible'}\n")
            ftxt.write(f"URL: {url}\n")
            ftxt.write(f"Estado: {estado}\n")
            ftxt.write("---\n")

            if estado != "FALLA":
                attrs = f'tvg-id="{ch_id}" tvg-name="{nombre}"'
                if logo:
                    attrs += f' tvg-logo="{logo}"'
                if country:
                    attrs += f' tvg-country="{country}"'

                fm3u.write(
                    f'#EXTINF:-1 {attrs} '
                    f'group-title="{country}",{nombre}\n'
                )
                fm3u.write(f"{url}\n\n")

        ftxt.write("\n" + "=" * 60 + "\n")
        ftxt.write("  RESUMEN GLOBAL\n")
        ftxt.write(f"  Total de streams probados: {len(todos_streams)}\n")
        ftxt.write(f"  Funcionan: {total_ok}\n")
        ftxt.write(f"  Fallan:    {total_fail}\n")
        ftxt.write(f"  Canales únicos: {len(canales_unicos)}\n")
        ftxt.write("\n  DESGLOSE POR PAÍS\n")
        for p in paises:
            st = paises_stats.get(p)
            if not st:
                continue
            ftxt.write(f"    {p}: {st['ok']} OK / {st['fail']} FAIL "
                       f"(total {st['total']})\n")
        ftxt.write("=" * 60 + "\n")

    log(f"M3U unificado: {UNIFICADO_M3U} — {total_ok}/{len(todos_streams)} canales", C.CYAN)
    return UNIFICADO_M3U, paises_stats

# ============================================================
# GENERACIÓN DEL README
# ============================================================
def _contar_canales_m3u(m3u_path: Path) -> int:
    if not m3u_path.exists():
        return 0
    with open(m3u_path, "r", encoding="utf-8", errors="ignore") as f:
        return sum(1 for line in f if line.startswith("#EXTINF"))


def generar_readme(paises, paises_stats, total_ok, total_probados, total_fail):
    filas = []
    for p in paises:
        st = paises_stats.get(p)
        if not st:
            continue
        ok_pais = st.get("ok", 0)
        if ok_pais == 0:
            continue
        nombre = NOMBRES_PAISES.get(p, p)
        archivo = f"{p.lower()}_canales.m3u"
        filas.append(
            f"| {nombre} | {ok_pais} | [📥 Descargar]({REPO_URL_RAW}/{archivo}) |"
        )

    tabla_paises = "\n".join(filas) if filas else "| _Sin datos_ | 0 | — |"

    if not README_TEMPLATE.exists():
        log(f"No se encontró la plantilla: {README_TEMPLATE}", C.RED)
        return None

    tpl = README_TEMPLATE.read_text(encoding="utf-8")
    contenido = (
        tpl.replace("{{FECHA}}", datetime.now().strftime("%Y-%m-%d %H:%M UTC"))
           .replace("{{TOTAL}}", str(total_ok))
           .replace("{{PAISES}}", str(len(filas)))
           .replace("{{PROBADOS}}", str(total_probados))
           .replace("{{OK}}", str(total_ok))
           .replace("{{FAIL}}", str(total_fail))
           .replace("{{TABLA_PAISES}}", tabla_paises)
    )
    README_OUT.write_text(contenido, encoding="utf-8")
    log(f"README generado: {README_OUT}", C.WHITE)
    return README_OUT

# ============================================================
# MAIN
# ============================================================
def main():
    inicio = datetime.now()
    log("=" * 60, C.CYAN)
    log("IPTV Manager - Modo GitHub Actions", C.WHITE)
    log(f"Inicio: {inicio:%Y-%m-%d %H:%M:%S}", C.CYAN)
    log("=" * 60, C.CYAN)

    log("\n[1/5] Descargando datos de la API...", C.BLUE)
    channels = descargar_json(CHANNELS_URL)
    streams  = descargar_json(STREAMS_URL)
    log(f"Datos: {len(channels)} canales, {len(streams)} streams", C.GREEN)

    log(f"\n[2/5] Filtrando países en español ({len(PAISES_ES)})...", C.BLUE)
    streams_por_pais, todos_streams, canal_por_id = recolectar_streams_paises(
        PAISES_ES, channels, streams
    )
    log(f"Streams únicos: {len(todos_streams)}", C.GREEN)

    log(f"\n[3/5] Probando streams (una sola vez)...", C.BLUE)
    resultados_globales = {}
    if PROBAR and todos_streams:
        resultados_globales = probar_streams_concurrente(
            todos_streams, etiqueta="streams (todos los países)"
        )

    log(f"\n[4/5] Generando listados por país...", C.BLUE)
    for pais in PAISES_ES:
        streams_pais = streams_por_pais.get(pais, [])
        if not streams_pais:
            continue
        generar_listados(pais, channels, streams, resultados=resultados_globales)

    log(f"\n[5/5] Generando unificado + README...", C.BLUE)
    unificado_m3u, paises_stats = generar_unificado(
        PAISES_ES, streams_por_pais, todos_streams, canal_por_id,
        resultados=resultados_globales,
    )

    total_ok_uni = _contar_canales_m3u(unificado_m3u)
    total_probados = len(todos_streams)
    total_fail_uni = total_probados - total_ok_uni

    generar_readme(
        paises=PAISES_ES,
        paises_stats=paises_stats,
        total_ok=total_ok_uni,
        total_probados=total_probados,
        total_fail=total_fail_uni,
    )

    fin = datetime.now()
    log("\n" + "=" * 60, C.CYAN)
    log(f"PROCESO COMPLETADO en {(fin - inicio).total_seconds():.1f}s", C.GREEN)
    log(f"Archivos en: {WORK_DIR}", C.WHITE)
    log("=" * 60, C.CYAN)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("\nProceso interrumpido.", C.RED)
        sys.exit(1)
    except Exception as e:
        log(f"\nError inesperado: {e}", C.RED)
        sys.exit(1)
