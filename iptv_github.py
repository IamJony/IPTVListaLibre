#!/usr/bin/env python3
# ============================================================
# Script: iptv_github.py
# Descripción: Descarga el M3U maestro de iptv-org, extrae
#              logos y User-Agents, filtra por países
#              hispanohablantes, categoriza ÚNICAMENTE por país
#              en español, prueba los streams y genera M3U + README.
#              Además integra canales de CINE desde world_ip_tv.
# Autor: IamJony https://github.com/IamJony
# Licencia: MIT
# ============================================================

import os
import sys
import re
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

# M3U maestra oficial de iptv-org
INDEX_M3U_URL = "https://iptv-org.github.io/iptv/index.m3u"

# M3U secundaria para canales de CINE
CINE_M3U_URL = "https://raw.githubusercontent.com/romaxa55/world_ip_tv/master/output/index.m3u"

# Países hispanohablantes (ISO 3166-1 alpha-2)
PAISES_ES = [
    "AR", "BO", "CL", "CO", "CR", "CU", "DO", "EC", "SV", "GQ", "GT",
    "HN", "MX", "NI", "PA", "PY", "PE", "PR", "ES", "UY", "VE",
]

# Canales de cine a buscar en la M3U secundaria (regex, case-insensitive)
CINE_PATRONES = [
    r"AMC\s+Series",
    r"TNT\s+Latin\s+America",
    r"TNT\s+Series",
    r"FX\s+Latin\s+America",
    r"HBO\s+Latin",
    r"Golden\s+Edge",
    r"\bSpace\b",
]

NOMBRE_CATEGORIA_CINE = "🎬 Cine"

PROBAR       = True
GENERAR_TXT  = True
GENERAR_M3U  = True

UNIFICADO_M3U = WORK_DIR / "es_unificado.m3u"
UNIFICADO_TXT = WORK_DIR / "es_unificado.txt"

README_TEMPLATE = BASE_DIR / "README.md.template"
README_OUT      = WORK_DIR / "README.md"

REPO_URL_RAW = "https://raw.githubusercontent.com/IamJony/IPTVListaLibre/refs/heads/main"

MAX_WORKERS   = 100
TIMEOUT       = 5
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

# TLDs de países hispanohablantes para inferencia por URL
TLDS_ES = (
    '.ar', '.bo', '.cl', '.co', '.cr', '.cu', '.do',
    '.ec', '.sv', '.gt', '.hn', '.mx', '.ni', '.pa',
    '.py', '.pe', '.pr', '.es', '.uy', '.ve',
)

# ============================================================
# UTILIDADES
# ============================================================
def log(msg, color=C.NC):
    print(f"{color}{msg}{C.NC}", flush=True)


def descargar_texto(url):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'IPTVManager/2.0'})
        with urllib.request.urlopen(req, timeout=90) as resp:
            return resp.read().decode('utf-8', errors='ignore')
    except Exception as e:
        log(f"Error al descargar {url}: {e}", C.RED)
        return None


def _sanitizar_ua(ua: str) -> str:
    """Reemplaza comillas dobles por simples para no romper el M3U."""
    if not ua:
        return ""
    return ua.replace('"', "'").replace('\n', ' ').replace('\r', ' ').strip()

# ============================================================
# PARSER M3U (ROBUSTO ANTE COMILLAS ANIDADAS)
# ============================================================
def _parsear_attrs(header: str) -> dict:
    """
    Parser robusto de atributos M3U tipo clave="valor".
    Soporta valores con comillas internas mal escapadas.
    """
    attrs = {}
    i = 0
    n = len(header)
    while i < n:
        while i < n and header[i].isspace():
            i += 1
        if i >= n:
            break

        key_start = i
        while i < n and (header[i].isalnum() or header[i] in "_-"):
            i += 1
        if i == key_start:
            i += 1
            continue
        key = header[key_start:i]

        while i < n and header[i].isspace():
            i += 1

        if i >= n or header[i] != '=':
            continue
        i += 1

        while i < n and header[i].isspace():
            i += 1

        if i >= n or header[i] != '"':
            val_start = i
            while i < n and not header[i].isspace():
                i += 1
            attrs[key] = header[val_start:i]
            continue

        i += 1
        val_start = i
        while i < n:
            if header[i] == '"':
                j = i + 1
                while j < n and header[j].isspace():
                    j += 1
                if j >= n:
                    break
                k = j
                while k < n and (header[k].isalnum() or header[k] in "_-"):
                    k += 1
                if k < n and header[k] == '=':
                    break
            i += 1
        attrs[key] = header[val_start:i]
        if i < n and header[i] == '"':
            i += 1
    return attrs


def parsear_m3u(contenido_m3u):
    canales = []
    lineas = contenido_m3u.splitlines()

    i = 0
    total_lineas = len(lineas)

    while i < total_lineas:
        linea = lineas[i].strip()

        if linea.startswith('#EXTINF:'):
            extinf_data = linea[8:]

            idx_ultima_comilla = extinf_data.rfind('"')
            if idx_ultima_comilla != -1:
                idx_coma = extinf_data.find(',', idx_ultima_comilla)
                if idx_coma == -1:
                    idx_coma = extinf_data.rfind(',')
            else:
                idx_coma = extinf_data.find(',')

            if idx_coma == -1:
                header_attrs = extinf_data
                nombre = "Canal Sin Nombre"
            else:
                header_attrs = extinf_data[:idx_coma]
                nombre = extinf_data[idx_coma + 1:].strip()

            nombre = re.sub(
                r'"?\s*group-title\s*=\s*"[^"]*"\s*,?\s*$',
                '', nombre, flags=re.IGNORECASE,
            ).strip()
            nombre = re.sub(
                r'^(?:like|como)\s+Gecko\)[^,]*,\s*',
                '', nombre, flags=re.IGNORECASE,
            ).strip()
            nombre = re.sub(
                r'^"[^"]*"\s*,\s*', '', nombre, flags=re.IGNORECASE,
            ).strip()

            attrs = _parsear_attrs(header_attrs)

            tvg_id      = (attrs.get('tvg-id', '') or '').strip('"')
            tvg_logo    = (attrs.get('tvg-logo', '') or '').strip('"')
            user_agent  = (attrs.get('http-user-agent', '') or '').strip('"')
            tvg_country = (attrs.get('tvg-country', '') or '').strip('"')
            group_title = (attrs.get('group-title', '') or '').strip('"')

            _ = group_title

            vlc_opt = ""
            url = ""

            i += 1
            while i < total_lineas:
                sub_linea = lineas[i].strip()
                if not sub_linea or sub_linea.startswith('#EXTM3U'):
                    i += 1
                    continue
                elif sub_linea.startswith('#EXTVLCOPT:http-user-agent='):
                    vlc_opt = sub_linea
                    if not user_agent:
                        user_agent = sub_linea.split('=', 1)[1].strip()
                    i += 1
                elif sub_linea.startswith('#'):
                    i += 1
                else:
                    url = sub_linea
                    break

            country = (tvg_country or "").strip().upper()

            if not country and '.' in tvg_id:
                try:
                    codigo = tvg_id.split('.')[1].split('@')[0].upper()
                    if len(codigo) == 2 and codigo.isalpha():
                        country = codigo
                except Exception:
                    pass

            if not country and url:
                m = re.search(r'https?://([^/]+)', url)
                if m:
                    host = m.group(1).lower()
                    for tld in TLDS_ES:
                        if host.endswith(tld) or f"{tld}/" in host or f"{tld}:" in host:
                            country = tld[1:].upper()
                            break

            if url:
                canales.append({
                    'tvg_id': tvg_id,
                    'tvg_logo': tvg_logo,
                    'user_agent': user_agent,
                    'vlc_opt': vlc_opt,
                    'nombre': nombre,
                    'url': url,
                    'country': country,
                })
        else:
            i += 1

    return canales

# ============================================================
# FILTRADO DE CANALES DE CINE (FUENTE SECUNDARIA)
# ============================================================
def extraer_canales_cine(contenido_m3u):
    """
    Parsea la M3U de world_ip_tv y devuelve únicamente los canales
    cuyo nombre coincida con alguno de los patrones de CINE_PATRONES.
    Todos quedarán con categoría 'Cine' (sin importar su país original).
    """
    canales = parsear_m3u(contenido_m3u)

    regex = re.compile("|".join(CINE_PATRONES), re.IGNORECASE)

    cine = []
    vistos = set()

    for c in canales:
        nombre = c.get("nombre", "")
        url = c.get("url", "")
        if not url or url in vistos:
            continue
        if regex.search(nombre):
            vistos.add(url)
            # Forzamos la categoría Cine
            c["country"] = "CINE"
            cine.append(c)

    return cine

# ============================================================
# PRUEBA DE STREAMS
# ============================================================
def probar_stream(canal_item):
    url = canal_item["url"]
    ua = canal_item.get("user_agent") or 'IPTVManager/2.0'
    headers = {'User-Agent': _sanitizar_ua(ua) or 'IPTVManager/2.0'}

    try:
        req = urllib.request.Request(url, method='HEAD', headers=headers)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return 200 <= resp.status < 400
    except urllib.error.HTTPError as e:
        if e.code in (403, 405, 501):
            try:
                headers['Range'] = 'bytes=0-1024'
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                    return 200 <= resp.status < 400
            except Exception:
                return False
        return False
    except Exception:
        return False


def probar_streams_concurrente(canales, etiqueta="streams"):
    resultados = {}
    total = len(canales)
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
        futuros = {executor.submit(probar_stream, c): c["url"] for c in canales}
        for fut in as_completed(futuros):
            url = futuros[fut]
            try:
                res = fut.result()
            except Exception:
                res = False
            resultados[url] = res
            procesados += 1
            if res:
                ok += 1
            else:
                fail += 1
            barra()

    print()
    return resultados

# ============================================================
# FILTRADO DE CANALES POR PAÍS
# ============================================================
def filtrar_por_paises(canales, paises):
    canales_por_pais = defaultdict(list)
    vistos_url = set()
    contador_canal = defaultdict(int)
    todos_filtrados = []

    set_paises = set(p.upper() for p in paises)

    for c in canales:
        pais = (c.get("country") or "").strip().upper()
        url = (c.get("url") or "").strip()
        ch_id = c.get("tvg_id") or c.get("nombre")

        if not pais or pais not in set_paises:
            continue
        if not url or url in vistos_url:
            continue
        if contador_canal[ch_id] >= MAX_POR_CANAL:
            continue

        vistos_url.add(url)
        contador_canal[ch_id] += 1

        canales_por_pais[pais].append(c)
        todos_filtrados.append(c)

    return canales_por_pais, todos_filtrados

# ============================================================
# ESCRITURA DE UN CANAL EN M3U
# ============================================================
def _escribir_canal_m3u(fm3u, c, categoria):
    """Escribe un canal en el archivo M3U con la categoría indicada."""
    url = c["url"]
    nombre = c["nombre"]
    logo = c["tvg_logo"]
    ch_id = c["tvg_id"]
    ua = c["user_agent"]
    vlc_opt = c["vlc_opt"]

    ua_limpio = _sanitizar_ua(ua)
    attrs = []
    if ch_id:
        attrs.append(f'tvg-id="{ch_id}"')
    if logo:
        attrs.append(f'tvg-logo="{logo}"')
    if ua_limpio:
        attrs.append(f'http-user-agent="{ua_limpio}"')

    attrs.append(f'group-title="{categoria}"')

    linea_extinf = f'#EXTINF:-1 {" ".join(attrs)},{nombre}'
    fm3u.write(f"{linea_extinf}\n")

    if vlc_opt:
        fm3u.write(f"#EXTVLCOPT:http-user-agent={ua_limpio or 'IPTVManager/2.0'}\n")
    elif ua_limpio:
        fm3u.write(f"#EXTVLCOPT:http-user-agent={ua_limpio}\n")

    fm3u.write(f"{url}\n\n")


# ============================================================
# ESCRIBIR ARCHIVOS POR PAÍS (M3U Y TXT)
# ============================================================
def _escribir_archivos(pais, canales_pais, resultados, txt_file, m3u_file, titulo):
    total_ok = 0
    total_fail = 0

    categoria_espanol = NOMBRES_PAISES.get(pais, pais)

    ftxt = open(txt_file, "w", encoding="utf-8") if GENERAR_TXT else None
    fm3u = open(m3u_file, "w", encoding="utf-8") if GENERAR_M3U else None

    try:
        if ftxt:
            ftxt.write("=" * 50 + "\n")
            ftxt.write(f"  {titulo}\n")
            ftxt.write("  Generado por IamJony (https://github.com/IamJony)\n")
            ftxt.write(f"  Fecha: {datetime.now()}\n")
            ftxt.write("=" * 50 + "\n\n")

        if fm3u:
            fm3u.write("#EXTM3U\n")
            fm3u.write(f"# {titulo}\n")
            fm3u.write("# Generada por IamJony (https://github.com/IamJony)\n")
            fm3u.write(f"# Fecha: {datetime.now()}\n\n")

        for c in canales_pais:
            url = c["url"]
            nombre = c["nombre"]
            logo = c["tvg_logo"]
            ua = c["user_agent"]

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
                ftxt.write(f"Categoría: {categoria_espanol}\n")
                ftxt.write(f"User-Agent: {ua or 'Predeterminado'}\n")
                ftxt.write(f"Logo: {logo or 'No disponible'}\n")
                ftxt.write(f"URL: {url}\n")
                ftxt.write(f"Estado: {estado}\n")
                ftxt.write("---\n")

            if fm3u and estado != "FALLA":
                _escribir_canal_m3u(fm3u, c, categoria_espanol)

        if ftxt:
            ftxt.write("\n" + "=" * 50 + "\n")
            ftxt.write("  RESUMEN\n")
            ftxt.write(f"  Total de streams: {len(canales_pais)}\n")
            ftxt.write(f"  Funcionan: {total_ok}\n")
            if PROBAR:
                ftxt.write(f"  Fallan: {total_fail}\n")
            ftxt.write("=" * 50 + "\n")
    finally:
        if ftxt:
            ftxt.close()
        if fm3u:
            fm3u.close()

    log(f"M3U: {m3u_file} — {total_ok}/{len(canales_pais)} canales", C.CYAN)
    return m3u_file


def generar_listados(pais, canales_pais, resultados=None):
    txt_file = WORK_DIR / f"{pais.lower()}_reporte.txt"
    m3u_file = WORK_DIR / f"{pais.lower()}_canales.m3u"

    if not canales_pais:
        return None

    return _escribir_archivos(
        pais=pais, canales_pais=canales_pais,
        resultados=resultados or {}, txt_file=txt_file, m3u_file=m3u_file,
        titulo=f"REPORTE DE CANALES IPTV - {pais}",
    )


# ============================================================
# GENERAR M3U SOLO DE CINE
# ============================================================
def generar_cine(canales_cine, resultados=None):
    """Genera un M3U y TXT exclusivo con los canales de cine."""
    if not canales_cine:
        log("No se encontraron canales de cine.", C.YELLOW)
        return None

    cine_m3u = WORK_DIR / "cine.m3u"
    cine_txt = WORK_DIR / "cine_reporte.txt"

    resultados = resultados or {}

    total_ok = 0
    total_fail = 0

    with open(cine_txt, "w", encoding="utf-8") as ftxt, \
         open(cine_m3u, "w", encoding="utf-8") as fm3u:

        ftxt.write("=" * 50 + "\n")
        ftxt.write("  REPORTE DE CANALES DE CINE\n")
        ftxt.write("  Fuente: world_ip_tv\n")
        ftxt.write("  Generado por IamJony (https://github.com/IamJony)\n")
        ftxt.write(f"  Fecha: {datetime.now()}\n")
        ftxt.write("=" * 50 + "\n\n")

        fm3u.write("#EXTM3U\n")
        fm3u.write("# Canales de Cine\n")
        fm3u.write("# Generada por IamJony (https://github.com/IamJony)\n")
        fm3u.write(f"# Fecha: {datetime.now()}\n\n")

        for c in canales_cine:
            url = c["url"]
            nombre = c["nombre"]
            logo = c["tvg_logo"]
            ua = c["user_agent"]

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

            ftxt.write(f"Nombre: {nombre}\n")
            ftxt.write(f"Categoría: {NOMBRE_CATEGORIA_CINE}\n")
            ftxt.write(f"User-Agent: {ua or 'Predeterminado'}\n")
            ftxt.write(f"Logo: {logo or 'No disponible'}\n")
            ftxt.write(f"URL: {url}\n")
            ftxt.write(f"Estado: {estado}\n")
            ftxt.write("---\n")

            if estado != "FALLA":
                _escribir_canal_m3u(fm3u, c, NOMBRE_CATEGORIA_CINE)

        ftxt.write("\n" + "=" * 50 + "\n")
        ftxt.write("  RESUMEN\n")
        ftxt.write(f"  Total de streams: {len(canales_cine)}\n")
        ftxt.write(f"  Funcionan: {total_ok}\n")
        if PROBAR:
            ftxt.write(f"  Fallan: {total_fail}\n")
        ftxt.write("=" * 50 + "\n")

    log(f"M3U Cine: {cine_m3u} — {total_ok}/{len(canales_cine)} canales", C.CYAN)
    return cine_m3u


# ============================================================
# GENERACIÓN DE LISTA UNIFICADA
# ============================================================
def generar_unificado(paises, todos_canales, canales_cine=None, resultados=None):
    log(f"\nGENERANDO LISTA UNIFICADA EN ESPAÑOL", C.CYAN)
    log(f"Total de streams únicos: {len(todos_canales)}", C.CYAN)

    resultados = resultados or {}
    canales_cine = canales_cine or []

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

        # --- Canales por país ---
        for c in todos_canales:
            url = c["url"]
            nombre = c["nombre"]
            country = (c["country"] or "").upper()
            logo = c["tvg_logo"]
            ua = c["user_agent"]

            if country not in NOMBRES_PAISES:
                continue

            categoria_espanol = NOMBRES_PAISES[country]

            if PROBAR:
                estado = "FUNCIONA" if resultados.get(url) else "FALLA"
            else:
                estado = "SIN PROBAR"

            paises_stats[country]["total"] += 1
            if estado == "FUNCIONA":
                total_ok += 1
                paises_stats[country]["ok"] += 1
                canales_unicos.add(c.get("tvg_id") or nombre)
            elif estado == "FALLA":
                total_fail += 1
                paises_stats[country]["fail"] += 1
            else:
                total_ok += 1
                canales_unicos.add(c.get("tvg_id") or nombre)

            ftxt.write(f"Nombre: {nombre}\n")
            ftxt.write(f"País: {categoria_espanol}\n")
            ftxt.write(f"User-Agent: {ua or 'Predeterminado'}\n")
            ftxt.write(f"Logo: {logo or 'No disponible'}\n")
            ftxt.write(f"URL: {url}\n")
            ftxt.write(f"Estado: {estado}\n")
            ftxt.write("---\n")

            if estado != "FALLA":
                _escribir_canal_m3u(fm3u, c, categoria_espanol)

        # --- Canales de CINE (integrados en el unificado) ---
        cine_ok = 0
        cine_fail = 0
        for c in canales_cine:
            url = c["url"]
            nombre = c["nombre"]
            logo = c["tvg_logo"]
            ua = c["user_agent"]

            if PROBAR:
                estado = "FUNCIONA" if resultados.get(url) else "FALLA"
            else:
                estado = "SIN PROBAR"

            if estado == "FUNCIONA":
                cine_ok += 1
                total_ok += 1
                canales_unicos.add(c.get("tvg_id") or nombre)
            elif estado == "FALLA":
                cine_fail += 1
                total_fail += 1
            else:
                cine_ok += 1
                total_ok += 1
                canales_unicos.add(c.get("tvg_id") or nombre)

            ftxt.write(f"Nombre: {nombre}\n")
            ftxt.write(f"Categoría: {NOMBRE_CATEGORIA_CINE}\n")
            ftxt.write(f"User-Agent: {ua or 'Predeterminado'}\n")
            ftxt.write(f"Logo: {logo or 'No disponible'}\n")
            ftxt.write(f"URL: {url}\n")
            ftxt.write(f"Estado: {estado}\n")
            ftxt.write("---\n")

            if estado != "FALLA":
                _escribir_canal_m3u(fm3u, c, NOMBRE_CATEGORIA_CINE)

        ftxt.write("\n" + "=" * 60 + "\n")
        ftxt.write("  RESUMEN GLOBAL\n")
        ftxt.write(f"  Total de streams probados: {len(todos_canales) + len(canales_cine)}\n")
        ftxt.write(f"  Funcionan: {total_ok}\n")
        ftxt.write(f"  Fallan:    {total_fail}\n")
        ftxt.write(f"  Canales únicos: {len(canales_unicos)}\n")
        ftxt.write("\n  DESGLOSE POR PAÍS\n")
        for p in paises:
            st = paises_stats.get(p)
            if not st:
                continue
            ftxt.write(f"    {NOMBRES_PAISES.get(p, p)}: {st['ok']} OK / {st['fail']} FAIL "
                       f"(total {st['total']})\n")
        ftxt.write(f"\n  CINE: {cine_ok} OK / {cine_fail} FAIL "
                   f"(total {len(canales_cine)})\n")
        ftxt.write("=" * 60 + "\n")

    log(f"M3U unificado: {UNIFICADO_M3U} — {total_ok} canales", C.CYAN)
    return UNIFICADO_M3U, paises_stats, cine_ok

# ============================================================
# GENERACIÓN DEL README
# ============================================================
def _contar_canales_m3u(m3u_path: Path) -> int:
    if not m3u_path.exists():
        return 0
    with open(m3u_path, "r", encoding="utf-8", errors="ignore") as f:
        return sum(1 for line in f if line.startswith("#EXTINF"))


def generar_readme(paises, paises_stats, total_ok, total_probados, total_fail, cine_ok=0):
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

    if cine_ok > 0:
        filas.append(
            f"| {NOMBRE_CATEGORIA_CINE} | {cine_ok} | [📥 Descargar]({REPO_URL_RAW}/cine.m3u) |"
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
    log("IPTV Manager - Categorías por País + Cine", C.WHITE)
    log(f"Inicio: {inicio:%Y-%m-%d %H:%M:%S}", C.CYAN)
    log("=" * 60, C.CYAN)

    # [1/6] Descargar M3U principal
    log("\n[1/6] Descargando archivo M3U maestro de iptv-org...", C.BLUE)
    m3u_raw = descargar_texto(INDEX_M3U_URL)
    if not m3u_raw:
        log("No se pudo descargar la lista principal. Abortando.", C.RED)
        return 1

    # [2/6] Parsear
    log("[2/6] Parseando canales y atributos...", C.BLUE)
    todos_canales_raw = parsear_m3u(m3u_raw)
    log(f"Canales totales parseados: {len(todos_canales_raw)}", C.GREEN)

    # [3/6] Filtrar países
    log(f"\n[3/6] Filtrando países en español ({len(PAISES_ES)})...", C.BLUE)
    canales_por_pais, todos_canales = filtrar_por_paises(todos_canales_raw, PAISES_ES)
    log(f"Streams filtrados únicos: {len(todos_canales)}", C.GREEN)

    # [4/6] Descargar y extraer canales de CINE
    log(f"\n[4/6] Descargando lista de CINE (world_ip_tv)...", C.BLUE)
    cine_raw = descargar_texto(CINE_M3U_URL)
    canales_cine = []
    if cine_raw:
        canales_cine = extraer_canales_cine(cine_raw)
        log(f"Canales de CINE encontrados: {len(canales_cine)}", C.GREEN)
    else:
        log("No se pudo descargar la lista de CINE. Se omite esa sección.", C.YELLOW)

    # [5/6] Probar streams (países + cine en una sola pasada)
    log(f"\n[5/6] Probando streams con sus User-Agents...", C.BLUE)
    resultados_globales = {}
    a_probar = todos_canales + canales_cine
    if PROBAR and a_probar:
        resultados_globales = probar_streams_concurrente(
            a_probar, etiqueta="streams en español + cine"
        )

    # [6/6] Generar archivos
    log(f"\n[6/6] Generando archivos M3U por país, Cine y unificado...", C.BLUE)

    for pais in PAISES_ES:
        canales_pais = canales_por_pais.get(pais, [])
        if canales_pais:
            generar_listados(pais, canales_pais, resultados=resultados_globales)

    # M3U exclusivo de Cine
    generar_cine(canales_cine, resultados=resultados_globales)

    # Unificado (países + cine)
    unificado_m3u, paises_stats, cine_ok = generar_unificado(
        PAISES_ES, todos_canales,
        canales_cine=canales_cine,
        resultados=resultados_globales,
    )

    total_ok_uni = _contar_canales_m3u(unificado_m3u)
    total_probados = len(todos_canales) + len(canales_cine)
    total_fail_uni = total_probados - total_ok_uni

    generar_readme(
        paises=PAISES_ES,
        paises_stats=paises_stats,
        total_ok=total_ok_uni,
        total_probados=total_probados,
        total_fail=total_fail_uni,
        cine_ok=cine_ok,
    )

    fin = datetime.now()
    log("\n" + "=" * 60, C.CYAN)
    log(f"PROCESO COMPLETADO en {(fin - inicio).total_seconds():.1f}s", C.GREEN)
    log(f"Archivos guardados en: {WORK_DIR}", C.WHITE)
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
