"""Baixa o KML de um CAR estadual pelo site público do Geoportal SEMA-MT, como um usuário faria.

Lê car/*.txt (um número por linha). Para cada CAR: abre o site, abre a Pesquisa,
digita o número e tenta baixar o KML. Salva prints de cada passo em saida/car/_passos/
e o KML em entrada/car-<numero>.kml (o que dispara a capa).
"""
import re
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

URL = "https://geoportal.sema.mt.gov.br/"
PASSOS = Path("saida/car/_passos")
PASSOS.mkdir(parents=True, exist_ok=True)


def foto(page, nome):
    page.screenshot(path=str(PASSOS / f"{nome}.png"))


def textos_clicaveis(page):
    els = page.query_selector_all("button, a, mat-option, [role=button], [mattooltip], [title]")
    out = []
    for e in els:
        try:
            t = (e.inner_text() or "").strip().replace("\n", " ")
            tip = e.get_attribute("mattooltip") or e.get_attribute("title") or ""
            if t or tip:
                out.append(f"{t} | {tip}")
        except Exception:
            pass
    return out


def baixar(numero):
    slug = re.sub(r"[^A-Za-z0-9]+", "-", numero).strip("-").lower()
    log = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1600, "height": 1000}, accept_downloads=True)
        try:
            page.goto(URL, wait_until="commit", timeout=180000)
            log.append("site respondeu")
        except Exception as e:
            log.append(f"site nao abriu: {str(e)[:200]}")
            (PASSOS / f"{slug}-log.txt").write_text("\n".join(log), encoding="utf-8")
            b.close()
            return
        time.sleep(25)
        foto(page, f"{slug}-1-inicio")
        log.append("CLICAVEIS INICIO:\n" + "\n".join(textos_clicaveis(page)[:120]))
        # abre a pesquisa (ícone de lupa)
        for sel in ["[mattooltip*='esquisa']", "[title*='esquisa']", "text=search"]:
            try:
                page.click(sel, timeout=5000)
                log.append(f"abriu pesquisa com {sel}")
                break
            except Exception:
                continue
        time.sleep(2)
        foto(page, f"{slug}-2-pesquisa")
        campo = page.query_selector("input[placeholder='Informe o termo a ser buscado']")
        log.append(f"campo encontrado: {bool(campo)}")
        if campo:
            campo.fill(numero)
            campo.press("Enter")
            time.sleep(8)
        foto(page, f"{slug}-3-resultado")
        log.append("CLICAVEIS APOS BUSCA:\n" + "\n".join(textos_clicaveis(page)[:200]))
        for sel in ["text=/kml/i", "[mattooltip*='KML']", "[title*='KML']"]:
            try:
                with page.expect_download(timeout=30000) as d:
                    page.click(sel, timeout=5000)
                destino = Path("entrada") / f"car-{slug}.kml"
                d.value.save_as(str(destino))
                log.append(f"KML salvo em {destino} via {sel}")
                break
            except Exception as e:
                log.append(f"falhou {sel}: {str(e)[:150]}")
        foto(page, f"{slug}-4-final")
        b.close()
    (PASSOS / f"{slug}-log.txt").write_text("\n".join(log), encoding="utf-8")


for arq in sorted(Path("car").glob("*.txt")):
    for linha in arq.read_text(encoding="utf-8").splitlines():
        if linha.strip():
            baixar(linha.strip())
