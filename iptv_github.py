#!/usr/bin/env python3
# ============================================================
# Script: iptv_github.py
# Descripción: Descarga el M3U maestro de iptv-org, extrae
#              logos y User-Agents, filtra por países
#              hispanohablantes, prueba los streams y genera:
#                - Un M3U por país (ar_canales.m3u, mx_canales.m3u, ...)
#                - Un M3U de Cine (cine_canales.m3u)
#                - Un M3U unificado (es_unificado.m3u) con países + Cine
#                - Reportes TXT por cada salida
#                - README.md
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

INDEX_M3U_URL = "https://iptv-org.github.io/iptv/index.m3u"

CINE_M3U_URLS = [
    "https://raw.githubusercontent.com/romaxa55/world_ip_tv/master/output/index.m3u",
]

PAISES_ES = [
    "AR", "BO", "CL", "CO", "CR", "CU", "DO", "EC", "SV", "GQ", "GT",
    "HN", "MX", "NI", "PA", "PY", "PE", "PR", "ES", "UY", "VE",
]

PAISES_LATAM_TXT = [
    "argentina", "mexico", "méxico", "colombia", "chile", "peru", "perú",
    "venezuela", "ecuador", "bolivia", "paraguay", "uruguay", "brasil",
    "brazil", "costa rica", "panama", "panamá", "guatemala", "honduras",
    "el salvador", "nicaragua", "cuba", "republica dominicana",
    "república dominicana", "puerto rico", "haiti", "haití",
    "alto parana", "ancash", "apurimac", "arequipa", "amazonas",
    "antioquia", "aragua", "alagoas", "aguascalientes",
]

PROBAR       = True
GENERAR_TXT  = True
GENERAR_M3U  = True

UNIFICADO_M3U = WORK_DIR / "es_unificado.m3u"
UNIFICADO_TXT = WORK_DIR / "es_unificado.txt"

CINE_M3U  = WORK_DIR / "cine_canales.m3u"
CINE_TXT  = WORK_DIR / "cine_reporte.txt"
CINE_NOMBRE_CATEGORIA = "🎬 Cine"

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

# ============================================================
# CANALES DE CINE / SERIES
# ============================================================
CANALES_CINE = [
    r"\bAMC\b", r"\bAMC Series\b",
    r"\bTNT\b", r"\bTNT Series\b", r"\bTNT Novelas\b",
    r"\bSpace\b", r"\bWarner\b", r"\bWarner Channel\b",
    r"\bSony\b", r"\bSony Channel\b", r"\bSony Movies\b",
    r"\bCinecanal\b", r"\bCinemax\b",
    r"\bStudio Universal\b", r"\bUniversal TV\b",
    r"\bUniversal Premiere\b", r"\bUniversal Cinema\b",
    r"\bUniversal Crime\b", r"\bUniversal Comedy\b",
    r"\bA&E\b", r"\bAXN\b", r"\bFX\b", r"\bFXM\b",
    r"\bFox\b", r"\bFox Channel\b", r"\bFox Premium\b",
    r"\bGolden\b", r"\bGolden Edge\b", r"\bGolden Premier\b", r"\bGolden Plus\b",
    r"\bHBO\b", r"\bHBO 2\b", r"\bHBO Plus\b", r"\bHBO Family\b",
    r"\bHBO Signature\b", r"\bHBO Mundi\b", r"\bHBO Xtreme\b", r"\bHBO Pop\b",
    r"\bMax\b", r"\bMax Up\b",
    r"\bParamount\b", r"\bParamount Channel\b", r"\bParamount Network\b",
    r"\bSyfy\b", r"\bTCM\b", r"\bTBS\b",
    r"\bI-Sat\b", r"\bEurochannel\b", r"\bEuropa Europa\b",
    r"\bDe Película\b", r"\bDe Película Clásico\b",
    r"\bMultipremier\b", r"\bMulticinema\b", r"\bMultiPremier\b",
    r"\bCine Latino\b", r"\bCinelatino\b",
    r"\bCine en Español\b", r"\bCine de Horror\b",
    r"\bStar Channel\b", r"\bStar Life\b",
    r"\bDHE\b", r"\bMovistar\b",
    r"\bAtrescine\b", r"\bAzteca Cinema\b", r"\bClaro Cinema\b",
    r"\bTlnovelas\b", r"\bPasiones\b", r"\bComedy Central\b",
    r"\bLifetime\b", r"\bInvestiga\b", r"\bInvestigation Discovery\b",
    r"\bDiscovery Theater\b", r"\bDiscovery Home\b",
    r"\bBeverly Hills Movies\b", r"\bMovieworld\b",
    r"\bX-Time\b", r"\bCinema \+", r"\bCine Click\b",
    r"\bCine Premium\b", r"\bClover Channel\b",
    r"\bCine Familiar\b", r"\bFMH Movies\b",
    r"\bNovelisima\b", r"\bCine Adrenalina\b",
    r"\bCine Clásico\b", r"\bCine Premiere\b",
    r"\bCine Terror\b", r"\bCine XOXO\b",
    r"\bCSI en Español\b",
]

EXCLUIR_CINE = [
    r"\bSports?\b", r"\bFox Sports\b", r"\bESPN\b", r"\bDSports\b",
    r"\bTyC Sports\b", r"\bDirectv Sports\b", r"\bGol\b", r"\bFutbol\b",
    r"\bFútbol\b", r"\bLiga\b", r"\bL1\b", r"\bL1 Max\b",
    r"\bESPN 2\b", r"\bESPN 3\b", r"\bESPN Premium\b",
    r"\bWin Sports\b", r"\bTUDN\b", r"\bTNT Sports\b",
    r"\bFox Deportes\b", r"\bEurosport\b", r"\bDAZN\b",
    r"\bDirecTV Sports\b", r"\bMovistar Deportes\b",
    r"\bFox Sports Premium\b", r"\bFox Sports 2\b", r"\bFox Sports 3\b",
    r"\bBandamax\b", r"\bTelemax\b", r"\bTV MAX\b", r"\bMaxivision\b",
    r"\bPower Max\b", r"\bRadio\b", r"\bFM\b", r"\bMúsica\b",
    r"\bMusic\b", r"\bMTV\b", r"\bTelehit\b",
    r"\bHTV\b", r"\bRitmoson\b", r"\bVH1\b", r"\bMuchMusic\b",
    r"\bCanal 5\b", r"\bCanal 4\b", r"\bCanal 2\b", r"\bCanal 3\b",
    r"\bImagen Universal\b", r"\bTV Max\b", r"\bMaxivision\b",
    r"\beSports\b", r"\bMAX Anime\b", r"\bAnime\b",
    r"\bNoticias\b", r"\bNews\b",
    r"\bTVE\b", r"\bTelevisa\b", r"\bAzteca\b",
    r"\bCanal 22\b", r"\bCanal Once\b", r"\bCanal 14\b",
    r"\bTelemax Argentina\b", r"\bTelemax Dominican\b",
    r"\bTelemax Mexico\b",
    r"\bInfantil\b", r"\bKids\b", r"\bDiscovery Kids\b",
    r"\bCartoon\b", r"\bNickelodeon\b", r"\bDisney\b",
    r"\bBabyFirst\b", r"\bBaby TV\b",
    r"\bDocumentales?\b", r"\bDocumentary\b",
    r"\bNat Geo\b", r"\bNational Geographic\b",
    r"\bHistory\b", r"\bDiscovery Channel\b", r"\bAnimal Planet\b",
    r"\bCocina\b", r"\bFood\b", r"\bEl Gourmet\b",
    r"\bHome & Health\b", r"\bTLC\b", r"\bE!\b",
    r"\bReligioso\b", r"\bCristiano\b", r"\bCatólico\b",
    r"\bEWTN\b", r"\bEnlace\b", r"\bNuevo Tiempo\b",
]

_RE_CINE    = re.compile("|".join(CANALES_CINE), re.IGNORECASE)
_RE_EXCLUIR = re.compile("|".join(EXCLUIR_CINE), re.IGNORECASE)


# ============================================================
# UTILIDADES
# ============================================================
def log(msg, color=C.NC):
    print(f"{color}{msg}{C.NC}", flush=True)


def descargar_texto(url, timeout=90):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'IPTVManager/2.0'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode('utf-8', errors='ignore')
    except Exception as e:
        log(f"Error al descargar {url}: {e}", C.RED)
        return None


def es_canal_de_cine(nombre: str) -> bool:
    if not nombre:
        return False
    if _RE_EXCLUIR.search(nombre):
        return False
    return bool(_RE_CINE.search(nombre))


def es_pais_latam(group_title: str) -> bool:
    if not group_title:
        return False
    g = group_title.lower()
    for pais in PAISES_LATAM_TXT:
        if pais in g:
            return True
    return False


# ============================================================
# PARSER M3U
# ============================================================
def parsear_m3u(contenido_m3u, parsear_group_title=False):
    canales = []
    lineas = contenido_m3u.splitlines()
    i = 0
    total_lineas = len(lineas)
    regex_attr = re.compile(r'([a-zA-Z0-9_-]+)="([^"]*)"')

    while i < total_lineas:
        linea = lineas[i].strip()
        if linea.startswith('#EXTINF:'):
            extinf_data = linea[8:]
            partes_coma = extinf_data.split(',', 1)
            header_attrs = partes_coma[0] if len(partes_coma) > 0 else ""
            nombre = partes_coma[1].strip() if len(partes_coma) > 1 else "Canal Sin Nombre"
            attrs = dict(regex_attr.findall(header_attrs))

            tvg_id = attrs.get('tvg-id', '')
            tvg_logo = attrs.get('tvg-logo', '')
            user_agent = attrs.get('http-user-agent', '')
            tvg_country = attrs.get('tvg-country', '')
            group_title = attrs.get('group-title', '')

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

            country = tvg_country
            if not country and '.' in tvg_id:
                try:
                    codigo = tvg_id.split('.')[1].split('@')[0].upper()
                    if len(codigo) == 2:
                        country = codigo
                except Exception:
                    pass

            if not country and parsear_group_title and group_title:
                country = group_title

            if url:
                canales.append({
                    'tvg_id': tvg_id, 'tvg_logo': tvg_logo,
                    'user_agent': user_agent, 'vlc_opt': vlc_opt,
                    'nombre': nombre, 'url': url, 'country': country,
                    'group_title': group_title,
                })
        else:
            i += 1
    return canales


# ============================================================
# PRUEBA DE STREAMS
# ============================================================
def probar_stream(canal_item):
    url = canal_item["url"]
    ua = canal_item.get("user_agent") or 'IPTVManager/2.0'
    headers = {'User-Agent': ua}
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
            if res: ok += 1
            else:   fail += 1
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
    set_paises = set(paises)
    for c in canales:
        pais = c.get("country", "").upper()
        url = c.get("url", "")
        ch_id = c.get("tvg_id") or c.get("nombre")
        if pais in set_paises:
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
# ESCRIBIR UN M3U GENÉRICO
# ============================================================
def _escribir_m3u(canales, m3u_file, titulo, categoria_override=None,
                  txt_file=None, resultados=None):
    """
    Escribe un archivo M3U con la lista de canales dada.
    Si categoria_override se especifica, se usa como group-title fijo.
    Opcionalmente escribe un .txt con el reporte.
    Devuelve (total_ok, total_fail).
    """
    resultados = resultados or {}
    total_ok = 0
    total_fail = 0

    fm3u = open(m3u_file, "w", encoding="utf-8") if GENERAR_M3U else None
    ftxt = open(txt_file, "w", encoding="utf-8") if (GENERAR_TXT and txt_file) else None

    try:
        if fm3u:
            fm3u.write("#EXTM3U\n")
            fm3u.write(f"# {titulo}\n")
            fm3u.write("# Generada por IamJony (https://github.com/IamJony)\n")
            fm3u.write(f"# Fecha: {datetime.now()}\n\n")

        if ftxt:
            ftxt.write("=" * 50 + "\n")
            ftxt.write(f"  {titulo}\n")
            ftxt.write("  Generado por IamJony (https://github.com/IamJony)\n")
            ftxt.write(f"  Fecha: {datetime.now()}\n")
            ftxt.write("=" * 50 + "\n\n")

        for c in canales:
            url = c["url"]
            nombre = c["nombre"]
            logo = c["tvg_logo"]
            ch_id = c["tvg_id"]
            ua = c["user_agent"]
            vlc_opt = c["vlc_opt"]

            if categoria_override:
                categoria = categoria_override
            else:
                categoria = c.get("group_title") or c.get("country") or "Sin Categoría"

            if PROBAR:
                estado = "FUNCIONA" if resultados.get(url) else "FALLA"
            else:
                estado = "SIN PROBAR"

            if estado == "FUNCIONA": total_ok += 1
            elif estado == "FALLA":  total_fail += 1
            else:                    total_ok += 1

            if ftxt:
                ftxt.write(f"Nombre: {nombre}\n")
                ftxt.write(f"Categoría: {categoria}\n")
                ftxt.write(f"User-Agent: {ua or 'Predeterminado'}\n")
                ftxt.write(f"Logo: {logo or 'No disponible'}\n")
                ftxt.write(f"URL: {url}\n")
                ftxt.write(f"Estado: {estado}\n")
                ftxt.write("---\n")

            if fm3u and estado != "FALLA":
                attrs = []
                if ch_id: attrs.append(f'tvg-id="{ch_id}"')
                if logo:  attrs.append(f'tvg-logo="{logo}"')
                if ua:    attrs.append(f'http-user-agent="{ua}"')
                attrs.append(f'group-title="{categoria}"')
                fm3u.write(f'#EXTINF:-1 {" ".join(attrs)},{nombre}\n')
                if vlc_opt:
                    fm3u.write(f"{vlc_opt}\n")
                elif ua:
                    fm3u.write(f"#EXTVLCOPT:http-user-agent={ua}\n")
                fm3u.write(f"{url}\n\n")

        if ftxt:
            ftxt.write("\n" + "=" * 50 + "\n")
            ftxt.write("  RESUMEN\n")
            ftxt.write(f"  Total de streams: {len(canales)}\n")
            ftxt.write(f"  Funcionan: {total_ok}\n")
            if PROBAR:
                ftxt.write(f"  Fallan: {total_fail}\n")
            ftxt.write("=" * 50 + "\n")
    finally:
        if fm3u: fm3u.close()
        if ftxt: ftxt.close()

    return total_ok, total_fail


# ============================================================
# GENERAR M3U POR PAÍS
# ============================================================
def generar_m3u_por_pais(pais, canales_pais, resultados):
    if not canales_pais:
        return None
    m3u_file = WORK_DIR / f"{pais.lower()}_canales.m3u"
    txt_file = WORK_DIR / f"{pais.lower()}_reporte.txt"
    nombre_pais = NOMBRES_PAISES.get(pais, pais)

    ok, fail = _escribir_m3u(
        canales=canales_pais,
        m3u_file=m3u_file,
        txt_file=txt_file,
        titulo=f"CANALES IPTV - {nombre_pais}",
        categoria_override=nombre_pais,
        resultados=resultados,
    )
    log(f"  {pais}: {ok} OK / {fail} FAIL → {m3u_file.name}", C.CYAN)
    return m3u_file


# ============================================================
# DESCARGAR Y PARSEAR M3Us EXTERNAS DE CINE
# ============================================================
def descargar_y_parsear_externos(urls):
    todos = []
    for url in urls:
        log(f"  Descargando M3U externa: {url}", C.CYAN)
        contenido = descargar_texto(url, timeout=60)
        if not contenido:
            log(f"  No se pudo descargar: {url}", C.YELLOW)
            continue
        canales = parsear_m3u(contenido, parsear_group_title=True)
        log(f"  Canales parseados: {len(canales)}", C.GREEN)
        todos.extend(canales)
    return todos


# ============================================================
# OBTENER CANALES DE CINE (iptv-org + externas)
# ============================================================
def obtener_canales_cine(todos_canales_iptvorg, resultados):
    log(f"\n[+] Obteniendo canales de CINE...", C.BLUE)

    cine_iptvorg = [c for c in todos_canales_iptvorg if es_canal_de_cine(c["nombre"])]
    log(f"  Canales de cine desde iptv-org: {len(cine_iptvorg)}", C.CYAN)

    log(f"  Descargando M3Us externas para Cine...", C.CYAN)
    canales_externos_raw = descargar_y_parsear_externos(CINE_M3U_URLS)

    cine_externos = []
    for c in canales_externos_raw:
        if not es_canal_de_cine(c["nombre"]):
            continue
        if not es_pais_latam(c.get("group_title", "")):
            continue
        cine_externos.append(c)

    log(f"  Canales de cine desde externas (LATAM): {len(cine_externos)}", C.CYAN)

    combinados = []
    vistos_url = set()
    for c in cine_iptvorg + cine_externos:
        url = c["url"]
        if url in vistos_url:
            continue
        vistos_url.add(url)
        combinados.append(c)

    log(f"  Total canales de cine (únicos): {len(combinados)}", C.GREEN)

    if PROBAR:
        pendientes = [c for c in cine_externos if c["url"] not in resultados]
        if pendientes:
            log(f"  Probando {len(pendientes)} streams externos de cine...", C.CYAN)
            resultados.update(probar_streams_concurrente(pendientes, etiqueta="cine externos"))

    return combinados


# ============================================================
# GENERAR M3U DE CINE (separado)
# ============================================================
def generar_m3u_cine(canales_cine, resultados):
    if not canales_cine:
        log("No se encontraron canales de cine.", C.YELLOW)
        return None

    ok, fail = _escribir_m3u(
        canales=canales_cine,
        m3u_file=CINE_M3U,
        txt_file=CINE_TXT,
        titulo="CANALES DE CINE (Películas y Series en Español)",
        categoria_override=CINE_NOMBRE_CATEGORIA,
        resultados=resultados,
    )
    log(f"  Cine: {ok} OK / {fail} FAIL → {CINE_M3U.name}", C.CYAN)
    return CINE_M3U


# ============================================================
# GENERAR M3U UNIFICADO (países + cine)
# ============================================================
def generar_m3u_unificado(todos_canales, canales_cine, resultados):
    log(f"\nGENERANDO LISTA UNIFICADA (países + cine)...", C.CYAN)

    combinados = todos_canales + canales_cine
    log(f"  Total canales: {len(combinados)}", C.CYAN)

    ok, fail = _escribir_m3u(
        canales=combinados,
        m3u_file=UNIFICADO_M3U,
        txt_file=UNIFICADO_TXT,
        titulo="LISTA UNIFICADA - PAÍSES + CINE",
        resultados=resultados,
        # Sin categoria_override: cada canal usa su group-title
        # (nombre del país para canales de país, "🎬 Cine" para cine)
    )
    log(f"  Unificado: {ok} OK / {fail} FAIL → {UNIFICADO_M3U.name}", C.CYAN)
    return UNIFICADO_M3U


# ============================================================
# GENERACIÓN DEL README
# ============================================================
def _contar_canales_m3u(m3u_path: Path) -> int:
    if not m3u_path.exists():
        return 0
    with open(m3u_path, "r", encoding="utf-8", errors="ignore") as f:
        return sum(1 for line in f if line.startswith("#EXTINF"))


def generar_readme(paises, resultados_por_pais, cine_stats, total_ok_uni,
                   total_probados, total_fail_uni):
    filas = []
    for p in paises:
        st = resultados_por_pais.get(p)
        if not st:
            continue
        ok_pais, fail_pais = st
        if ok_pais == 0:
            continue
        nombre = NOMBRES_PAISES.get(p, p)
        archivo = f"{p.lower()}_canales.m3u"
        filas.append(
            f"| {nombre} | {ok_pais} | [📥 Descargar]({REPO_URL_RAW}/{archivo}) |"
        )

    if cine_stats and cine_stats[0] > 0:
        filas.append(
            f"| 🎬 Cine (Películas y Series) | {cine_stats[0]} | "
            f"[📥 Descargar]({REPO_URL_RAW}/cine_canales.m3u) |"
        )

    filas.append(
        f"| 📦 **LISTA UNIFICADA** | {total_ok_uni} | "
        f"[📥 Descargar]({REPO_URL_RAW}/es_unificado.m3u) |"
    )

    tabla_paises = "\n".join(filas) if filas else "| _Sin datos_ | 0 | — |"

    if not README_TEMPLATE.exists():
        log(f"No se encontró la plantilla: {README_TEMPLATE}", C.RED)
        return None

    tpl = README_TEMPLATE.read_text(encoding="utf-8")
    contenido = (
        tpl.replace("{{FECHA}}", datetime.now().strftime("%Y-%m-%d %H:%M UTC"))
           .replace("{{TOTAL}}", str(total_ok_uni))
           .replace("{{PAISES}}", str(len(paises)))
           .replace("{{PROBADOS}}", str(total_probados))
           .replace("{{OK}}", str(total_ok_uni))
           .replace("{{FAIL}}", str(total_fail_uni))
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
    log("IPTV Manager - Países + Cine + Unificado", C.WHITE)
    log(f"Inicio: {inicio:%Y-%m-%d %H:%M:%S}", C.CYAN)
    log("=" * 60, C.CYAN)

    # [1] Descargar iptv-org
    log("\n[1/6] Descargando M3U maestro de iptv-org...", C.BLUE)
    m3u_raw = descargar_texto(INDEX_M3U_URL)
    if not m3u_raw:
        log("No se pudo descargar el M3U de iptv-org.", C.RED)
        sys.exit(1)

    # [2] Parsear
    log("[2/6] Parseando canales...", C.BLUE)
    todos_canales_raw = parsear_m3u(m3u_raw)
    log(f"Canales totales parseados: {len(todos_canales_raw)}", C.GREEN)

    # [3] Filtrar países
    log(f"\n[3/6] Filtrando países en español ({len(PAISES_ES)})...", C.BLUE)
    canales_por_pais, todos_canales = filtrar_por_paises(todos_canales_raw, PAISES_ES)
    log(f"Streams filtrados únicos: {len(todos_canales)}", C.GREEN)

    # [4] Probar streams iptv-org
    log(f"\n[4/6] Probando streams de iptv-org...", C.BLUE)
    resultados_globales = {}
    if PROBAR and todos_canales:
        resultados_globales = probar_streams_concurrente(
            todos_canales, etiqueta="streams en español"
        )

    # [5] Generar M3U por país + M3U de Cine
    log(f"\n[5/6] Generando M3U por país y M3U de Cine...", C.BLUE)
    resultados_por_pais = {}
    for pais in PAISES_ES:
        canales_pais = canales_por_pais.get(pais, [])
        if canales_pais:
            ok, fail = _escribir_m3u(
                canales=canales_pais,
                m3u_file=WORK_DIR / f"{pais.lower()}_canales.m3u",
                txt_file=WORK_DIR / f"{pais.lower()}_reporte.txt",
                titulo=f"CANALES IPTV - {NOMBRES_PAISES.get(pais, pais)}",
                categoria_override=NOMBRES_PAISES.get(pais, pais),
                resultados=resultados_globales,
            )
            resultados_por_pais[pais] = (ok, fail)
            log(f"  {pais}: {ok} OK / {fail} FAIL", C.CYAN)

    # Canales de cine (iptv-org + externas)
    canales_cine = obtener_canales_cine(todos_canales, resultados_globales)

    # M3U de Cine separado
    cine_stats = (0, 0)
    if canales_cine:
        ok_cine, fail_cine = _escribir_m3u(
            canales=canales_cine,
            m3u_file=CINE_M3U,
            txt_file=CINE_TXT,
            titulo="CANALES DE CINE (Películas y Series en Español)",
            categoria_override=CINE_NOMBRE_CATEGORIA,
            resultados=resultados_globales,
        )
        cine_stats = (ok_cine, fail_cine)
        log(f"  Cine: {ok_cine} OK / {fail_cine} FAIL", C.CYAN)

    # [6] Generar M3U unificado (países + cine)
    log(f"\n[6/6] Generando M3U unificado y README...", C.BLUE)
    combinados_unificado = todos_canales + canales_cine
    total_ok_uni, total_fail_uni = _escribir_m3u(
        canales=combinados_unificado,
        m3u_file=UNIFICADO_M3U,
        txt_file=UNIFICADO_TXT,
        titulo="LISTA UNIFICADA - PAÍSES + CINE",
        resultados=resultados_globales,
        # Sin categoria_override → cada canal mantiene su group-title
    )
    log(f"  Unificado: {total_ok_uni} OK / {total_fail_uni} FAIL", C.CYAN)

    # README
    generar_readme(
        paises=PAISES_ES,
        resultados_por_pais=resultados_por_pais,
        cine_stats=cine_stats,
        total_ok_uni=total_ok_uni,
        total_probados=len(combinados_unificado),
        total_fail_uni=total_fail_uni,
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
