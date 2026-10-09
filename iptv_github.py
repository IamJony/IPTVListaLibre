#!/usr/bin/env python3
# ============================================================
# Script: iptv_github.py
# Descripción: Descarga el M3U maestro de iptv-org, extrae canales
#              hispanohablantes, categoriza ÚNICAMENTE por país
#              en español, prueba los streams y genera M3U + README.
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
        sys.exit(1)

# ============================================================
# PARSER M3U
# ============================================================
def parsear_m3u(contenido_m3u):
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
            
            # Inferir país
            country = tvg_country
            if not country and '.' in tvg_id:
                try:
                    codigo = tvg_id.split('.')[1].split('@')[0].upper()
                    if len(codigo) == 2:
                        country = codigo
                except Exception:
                    pass

            if url:
                canales.append({
                    'tvg_id': tvg_id,
                    'tvg_logo': tvg_logo,
                    'user_agent': user_agent,
                    'vlc_opt': vlc_opt,
                    'nombre': nombre,
                    'url': url,
                    'country': country
                })
        else:
            i += 1
            
    return canales

# ============================================================
# PRUEBA DE STREAMS (USANDO SU USER-AGENT)
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
# ESCRIBIR ARCHIVOS M3U Y TXT
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
            ch_id = c["tvg_id"]
            ua = c["user_agent"]
            vlc_opt = c["vlc_opt"]

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
                attrs = []
                if ch_id:
                    attrs.append(f'tvg-id="{ch_id}"')
                if logo:
                    attrs.append(f'tvg-logo="{logo}"')
                if ua:
                    attrs.append(f'http-user-agent="{ua}"')
                
                # Asignar estrictamente el Nombre del País en Español como categoría
                attrs.append(f'group-title="{categoria_espanol}"')
                
                linea_extinf = f'#EXTINF:-1 {" ".join(attrs)},{nombre}'
                fm3u.write(f"{linea_extinf}\n")
                
                if vlc_opt:
                    fm3u.write(f"{vlc_opt}\n")
                elif ua:
                    fm3u.write(f"#EXTVLCOPT:http-user-agent={ua}\n")
                    
                fm3u.write(f"{url}\n\n")

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
# GENERACIÓN DE LISTA UNIFICADA
# ============================================================
def generar_unificado(paises, todos_canales, resultados=None):
    log(f"\nGENERANDO LISTA UNIFICADA EN ESPAÑOL", C.CYAN)
    log(f"Total de streams únicos: {len(todos_canales)}", C.CYAN)

    resultados = resultados or {}

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

        for c in todos_canales:
            url = c["url"]
            nombre = c["nombre"]
            country = c["country"]
            logo = c["tvg_logo"]
            ch_id = c["tvg_id"]
            ua = c["user_agent"]
            vlc_opt = c["vlc_opt"]
            
            # Obtener categoría por país en español
            categoria_espanol = NOMBRES_PAISES.get(country, country)

            if PROBAR:
                estado = "FUNCIONA" if resultados.get(url) else "FALLA"
            else:
                estado = "SIN PROBAR"

            paises_stats[country]["total"] += 1
            if estado == "FUNCIONA":
                total_ok += 1
                paises_stats[country]["ok"] += 1
                canales_unicos.add(ch_id or nombre)
            elif estado == "FALLA":
                total_fail += 1
                paises_stats[country]["fail"] += 1
            else:
                total_ok += 1
                canales_unicos.add(ch_id or nombre)

            ftxt.write(f"Nombre: {nombre}\n")
            ftxt.write(f"País: {categoria_espanol}\n")
            ftxt.write(f"User-Agent: {ua or 'Predeterminado'}\n")
            ftxt.write(f"Logo: {logo or 'No disponible'}\n")
            ftxt.write(f"URL: {url}\n")
            ftxt.write(f"Estado: {estado}\n")
            ftxt.write("---\n")

            if estado != "FALLA":
                attrs = []
                if ch_id:
                    attrs.append(f'tvg-id="{ch_id}"')
                if logo:
                    attrs.append(f'tvg-logo="{logo}"')
                if ua:
                    attrs.append(f'http-user-agent="{ua}"')
                
                # Asignar estrictamente el Nombre del País en Español como categoría
                attrs.append(f'group-title="{categoria_espanol}"')
                
                linea_extinf = f'#EXTINF:-1 {" ".join(attrs)},{nombre}'
                fm3u.write(f"{linea_extinf}\n")
                
                if vlc_opt:
                    fm3u.write(f"{vlc_opt}\n")
                elif ua:
                    fm3u.write(f"#EXTVLCOPT:http-user-agent={ua}\n")
                    
                fm3u.write(f"{url}\n\n")

        ftxt.write("\n" + "=" * 60 + "\n")
        ftxt.write("  RESUMEN GLOBAL\n")
        ftxt.write(f"  Total de streams probados: {len(todos_canales)}\n")
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
        ftxt.write("=" * 60 + "\n")

    log(f"M3U unificado: {UNIFICADO_M3U} — {total_ok}/{len(todos_canales)} canales", C.CYAN)
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
    log("IPTV Manager - Categorías por País", C.WHITE)
    log(f"Inicio: {inicio:%Y-%m-%d %H:%M:%S}", C.CYAN)
    log("=" * 60, C.CYAN)

    log("\n[1/5] Descargando archivo M3U maestro de iptv-org...", C.BLUE)
    m3u_raw = descargar_texto(INDEX_M3U_URL)
    
    log("[2/5] Parseando canales y atributos...", C.BLUE)
    todos_canales_raw = parsear_m3u(m3u_raw)
    log(f"Canales totales parseados: {len(todos_canales_raw)}", C.GREEN)

    log(f"\n[3/5] Filtrando países en español ({len(PAISES_ES)})...", C.BLUE)
    canales_por_pais, todos_canales = filtrar_por_paises(todos_canales_raw, PAISES_ES)
    log(f"Streams filtrados únicos: {len(todos_canales)}", C.GREEN)

    log(f"\n[4/5] Probando streams con sus User-Agents...", C.BLUE)
    resultados_globales = {}
    if PROBAR and todos_canales:
        resultados_globales = probar_streams_concurrente(
            todos_canales, etiqueta="streams en español"
        )

    log(f"\n[5/5] Generando archivos M3U por país y unificado...", C.BLUE)
    for pais in PAISES_ES:
        canales_pais = canales_por_pais.get(pais, [])
        if canales_pais:
            generar_listados(pais, canales_pais, resultados=resultados_globales)

    unificado_m3u, paises_stats = generar_unificado(
        PAISES_ES, todos_canales, resultados=resultados_globales
    )

    total_ok_uni = _contar_canales_m3u(unificado_m3u)
    total_probados = len(todos_canales)
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

```
