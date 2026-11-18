"""Consulta o perímetro de um CAR de MT nos servidores públicos (SEMA-MT e SICAR).

Lê cada arquivo car/<qualquer>.txt (uma linha por número de CAR, ex.: MT354673/2026)
e grava em saida/car/<numero>/:
  - diagnostico.txt  (o que cada servidor respondeu)
  - perimetro.geojson / perimetro.kml (se encontrar)
"""
import json
import re
from pathlib import Path

import requests

SEMA = "https://geo.sema.mt.gov.br/geoserver"
SERVIDORES = {
    "SEMA-MT": f"{SEMA}/ows",
    "SEMA-MT simcar_prod": f"{SEMA}/simcar_prod/ows",
    "SEMA-MT SIMCAR_em_analise": f"{SEMA}/SIMCAR_em_analise/ows",
    "SEMA-MT semamt": f"{SEMA}/semamt/ows",
    "SEMA-MT Geoportal": f"{SEMA}/Geoportal/ows",
    "SICAR": "https://geoserver.car.gov.br/geoserver/sicar/wfs",
}
PALAVRAS = ("car", "simcar", "imove", "imovei", "atp", "perimetro")
HEAD = {"User-Agent": "Mozilla/5.0 (capas-meta consulta CAR)"}
TIMEOUT = 60

log_linhas = []


def log(msg):
    print(msg)
    log_linhas.append(str(msg))


def get(url, params):
    try:
        r = requests.get(url, params=params, headers=HEAD, timeout=TIMEOUT)
        return r.status_code, r.text
    except Exception as e:  # noqa: BLE001
        return None, f"ERRO: {e}"[:300]


def listar_camadas(url):
    st, txt = get(url, {"service": "WFS", "version": "1.1.0", "request": "GetCapabilities"})
    log(f"  WFS GetCapabilities -> HTTP {st}, {len(txt)} bytes")
    Path("saida/car/_bruto").mkdir(parents=True, exist_ok=True)
    tag = re.sub(r"[^a-z0-9]+", "_", url.lower())[-40:]
    Path(f"saida/car/_bruto/wfs_{tag}.xml").write_text(txt[:200000], encoding="utf-8")
    st2, wms = get(url, {"service": "WMS", "version": "1.3.0", "request": "GetCapabilities"})
    Path(f"saida/car/_bruto/wms_{tag}.xml").write_text(wms[:400000], encoding="utf-8")
    log(f"  WMS GetCapabilities -> HTTP {st2}, {len(wms)} bytes")
    nomes = re.findall(r"<(?:\w+:)?Name>([^<]+)</(?:\w+:)?Name>", txt)
    log(f"  {len(nomes)} camadas no total")
    alvo = [n for n in nomes if any(p in n.lower() for p in PALAVRAS)]
    log(f"  camadas candidatas ({len(alvo)}): {alvo[:60]}")
    return alvo


def campos(url, camada):
    st, txt = get(url, {"service": "WFS", "version": "1.1.0", "request": "DescribeFeatureType", "typeName": camada})
    pares = re.findall(r'name="([^"]+)"[^>]*type="([^"]+)"', txt)
    texto = [n for n, t in pares if "string" in t.lower()]
    log(f"    {camada}: HTTP {st}, campos texto = {texto}")
    return texto


def buscar(url, camada, campo, termo):
    filtro = f"{campo} ILIKE '%{termo}%'"
    st, txt = get(url, {
        "service": "WFS", "version": "1.0.0", "request": "GetFeature",
        "typeName": camada, "outputFormat": "application/json",
        "maxFeatures": "5", "CQL_FILTER": filtro, "srsName": "EPSG:4326",
    })
    try:
        dados = json.loads(txt)
    except Exception:  # noqa: BLE001
        return None
    n = len(dados.get("features", []))
    if n:
        log(f"    ACHOU {n} em {camada} pelo campo {campo}")
        return dados
    return None


def kml(geo, nome):
    partes = []
    for f in geo["features"]:
        g = f["geometry"]
        polis = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        for p in polis:
            anel = " ".join(f"{x},{y},0" for x, y, *_ in p[0])
            partes.append(f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{anel}</coordinates></LinearRing></outerBoundaryIs></Polygon>")
    return ('<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            f"<Placemark><name>{nome}</name><MultiGeometry>{''.join(partes)}</MultiGeometry></Placemark>"
            "</Document></kml>")


def consultar(numero):
    log_linhas.clear()
    log(f"CAR: {numero}")
    m = re.search(r"(\d+)\s*/?\s*(\d{4})?", numero)
    termos = [m.group(1)] if m else [numero]
    pasta = Path("saida/car") / re.sub(r"[^A-Za-z0-9]+", "-", numero).strip("-").lower()
    pasta.mkdir(parents=True, exist_ok=True)
    achado = None
    for nome, url in SERVIDORES.items():
        log(f"\n== {nome} ({url})")
        try:
            camadas = listar_camadas(url)
        except Exception as e:  # noqa: BLE001
            log(f"  falhou: {e}")
            continue
        if not camadas:
            st, txt = get(url, {"service": "WFS", "version": "1.1.0", "request": "GetCapabilities"})
            if st is None:
                log(f"  {txt}")
        for camada in camadas[:25]:
            for campo in campos(url, camada)[:8]:
                for termo in termos:
                    achado = buscar(url, camada, campo, termo)
                    if achado:
                        break
                if achado:
                    break
            if achado:
                log(f"  propriedades: {json.dumps(achado['features'][0]['properties'], ensure_ascii=False)[:800]}")
                break
        if achado:
            break
    if achado:
        (pasta / "perimetro.geojson").write_text(json.dumps(achado, ensure_ascii=False))
        (pasta / "perimetro.kml").write_text(kml(achado, numero))
        log("\nRESULTADO: perímetro encontrado e salvo.")
    else:
        log("\nRESULTADO: perímetro não encontrado.")
    (pasta / "diagnostico.txt").write_text("\n".join(log_linhas), encoding="utf-8")


def main():
    for arq in sorted(Path("car").glob("*.txt")):
        for linha in arq.read_text(encoding="utf-8").splitlines():
            if linha.strip():
                consultar(linha.strip())


if __name__ == "__main__":
    main()
