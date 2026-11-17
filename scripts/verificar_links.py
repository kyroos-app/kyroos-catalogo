# [Claude 16/11] ARQUIVO NOVO — confere todo dia se os links do links.json funcionam.
#
# O que faz, para cada programa do links.json:
#   1. Testa se o link responde e se é mesmo um arquivo (e não uma página de erro).
#   2. Confere o SHA256 baixando o instalador inteiro, mas só de uma parte dos programas por dia
#      (cada programa é conferido 1 vez por semana; arquivos maiores que 700 MB são pulados).
#   3. Se o link quebrou: usa a versão anterior que funcionava (do links.json de ontem), se ela ainda funcionar.
#      Se nada funcionar, tira o programa do links.json — aí o Kyroos usa o catálogo de dentro do app e o winget.
#   4. Escreve um relatório (relatorio-links.md) com o que quebrou; a automação abre um aviso (Issue) no GitHub.
#   5. Guarda no links.json quando cada link foi testado ("verificadoEm") e o histórico em verificacao.json.
#
# Não precisa instalar nada além do Python 3.
# Teste no seu PC:  python scripts/verificar_links.py --links links.json

import argparse
import datetime
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor

AGENTE = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Kyroos-VerificadorLinks/1.0"
TEMPO_LIMITE = 25
LIMITE_HASH = 700 * 1024 * 1024  # não baixa inteiro arquivo maior que isso
TIPOS_DE_PAGINA = ("text/html", "application/json", "text/plain", "application/xml", "text/xml")


def agora_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def testar_link(url):
    """Devolve (ok, motivo, tamanho). Tenta HEAD; se o servidor não aceitar, pede só o 1º byte (GET Range)."""
    ultimo_erro = "sem resposta"
    for metodo in ("HEAD", "GET"):
        req = urllib.request.Request(url, method=metodo, headers={"User-Agent": AGENTE})
        if metodo == "GET":
            req.add_header("Range", "bytes=0-0")
        try:
            with urllib.request.urlopen(req, timeout=TEMPO_LIMITE) as r:
                tipo = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                tamanho = r.headers.get("Content-Length")
                if r.headers.get("Content-Range") and "/" in r.headers["Content-Range"]:
                    tamanho = r.headers["Content-Range"].split("/")[-1]
                tamanho = int(tamanho) if tamanho and str(tamanho).isdigit() else None
                if tipo in TIPOS_DE_PAGINA:
                    return False, f"o link abre uma página ({tipo}), não um arquivo", tamanho
                if tamanho is not None and tamanho < 50_000 and metodo == "HEAD":
                    return False, f"arquivo pequeno demais ({tamanho} bytes)", tamanho
                return True, "ok", tamanho
        except urllib.error.HTTPError as e:
            ultimo_erro = f"erro HTTP {e.code}"
            if e.code in (404, 410):
                return False, ultimo_erro, None  # não adianta tentar GET
        except Exception as e:  # tempo esgotado, DNS, certificado...
            ultimo_erro = f"{type(e).__name__}: {e}"
    return False, ultimo_erro, None


def conferir_hash(url, esperado):
    """Baixa o arquivo inteiro e compara o SHA256. Devolve (ok, motivo)."""
    h = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": AGENTE})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            baixado = 0
            while True:
                bloco = r.read(1024 * 1024)
                if not bloco:
                    break
                baixado += len(bloco)
                if baixado > LIMITE_HASH:
                    return True, "arquivo grande demais para conferir (pulado)"
                h.update(bloco)
    except Exception as e:
        return False, f"falhou ao baixar para conferir: {e}"
    if h.hexdigest().lower() != (esperado or "").lower():
        return False, "SHA256 diferente do esperado (arquivo trocado ou corrompido)"
    return True, "SHA256 confere"


def dia_do_hash(winget_id):
    """Distribui os programas nos 7 dias da semana (sempre o mesmo dia para o mesmo programa)."""
    return zlib.crc32(winget_id.lower().encode()) % 7


def verificar(winget_id, info, conferir_sha):
    ok, motivo, tamanho = testar_link(info["url"])
    if ok and conferir_sha:
        if tamanho is not None and tamanho > LIMITE_HASH:
            motivo = "link ok (arquivo grande, SHA256 não conferido)"
        else:
            ok, motivo = conferir_hash(info["url"], info.get("sha256"))
    return winget_id, ok, motivo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--links", default="links.json")
    ap.add_argument("--anterior", default="links.anterior.json", help="links.json de antes (para voltar à versão que funcionava)")
    ap.add_argument("--estado", default="verificacao.json")
    ap.add_argument("--relatorio", default="relatorio-links.md")
    ap.add_argument("--hash-todos", action="store_true", help="confere o SHA256 de todos hoje (demora)")
    ap.add_argument("--sem-hash", action="store_true", help="só testa se os links respondem")
    a = ap.parse_args()

    with open(a.links, encoding="utf-8-sig") as f:
        links = json.load(f)
    anterior = {}
    if os.path.exists(a.anterior):
        try:
            with open(a.anterior, encoding="utf-8-sig") as f:
                anterior = json.load(f).get("programas", {})
        except Exception:
            anterior = {}
    estado = {}
    if os.path.exists(a.estado):
        try:
            with open(a.estado, encoding="utf-8-sig") as f:
                estado = json.load(f).get("programas", {})
        except Exception:
            estado = {}

    programas = links.get("programas", {})
    hoje = agora_utc()
    dia = hoje.weekday()
    tarefas = []
    for wid, info in programas.items():
        sha = not a.sem_hash and (a.hash_todos or dia_do_hash(wid) == dia)
        tarefas.append((wid, info, sha))

    print(f"Verificando {len(tarefas)} links ({sum(1 for t in tarefas if t[2])} com conferência de SHA256 hoje)…")
    with ThreadPoolExecutor(max_workers=12) as pool:
        resultados = list(pool.map(lambda t: verificar(*t), tarefas))

    carimbo = hoje.strftime("%Y-%m-%dT%H:%M:%SZ")
    quebrados, recuperados, removidos = [], [], []
    for wid, ok, motivo in resultados:
        reg = estado.get(wid, {"falhasSeguidas": 0})
        if ok:
            programas[wid]["verificadoEm"] = carimbo
            reg.update({"ultimoOk": carimbo, "falhasSeguidas": 0, "ultimoMotivo": motivo})
            estado[wid] = reg
            continue
        reg["falhasSeguidas"] = reg.get("falhasSeguidas", 0) + 1
        reg["ultimaFalha"] = carimbo
        reg["ultimoMotivo"] = motivo
        estado[wid] = reg
        quebrados.append((wid, programas[wid].get("versao", "?"), motivo))
        # Volta para a versão de ontem, se ela for diferente e ainda funcionar
        velho = anterior.get(wid)
        if velho and velho.get("url") != programas[wid].get("url"):
            ok2, motivo2, _ = testar_link(velho["url"])
            if ok2:
                velho = dict(velho)
                velho["verificadoEm"] = carimbo
                programas[wid] = velho
                recuperados.append((wid, velho.get("versao", "?")))
                continue
        removidos.append(wid)

    for wid in removidos:
        programas.pop(wid, None)
    links["programas"] = programas
    links["total"] = len(programas)
    links["verificadoEm"] = carimbo
    with open(a.links, "w", encoding="utf-8") as f:
        json.dump(links, f, ensure_ascii=False, indent=1)
    with open(a.estado, "w", encoding="utf-8") as f:
        json.dump({"atualizadoEm": carimbo, "programas": dict(sorted(estado.items(), key=lambda kv: kv[0].lower()))},
                  f, ensure_ascii=False, indent=1)

    # Relatório (vira o texto do aviso no GitHub)
    linhas = [f"# Verificação dos links — {hoje:%d/%m/%Y}", ""]
    linhas.append(f"- Testados: **{len(resultados)}**  •  com problema: **{len(quebrados)}**  •  "
                  f"voltaram para a versão anterior: **{len(recuperados)}**  •  tirados do links.json: **{len(removidos)}**")
    linhas.append("")
    if quebrados:
        linhas += ["## Links com problema", "", "| Programa | Versão | Problema | Resolvido como |", "|---|---|---|---|"]
        rec = dict(recuperados)
        for wid, versao, motivo in sorted(quebrados):
            como = f"usando a versão anterior ({rec[wid]})" if wid in rec else "tirado do links.json (o Kyroos usa o catálogo do app e o winget)"
            linhas.append(f"| `{wid}` | {versao} | {motivo} | {como} |")
        linhas += ["", "Programas que falham vários dias seguidos (verificacao.json → falhasSeguidas) provavelmente "
                       "mudaram de ID no winget: confira o ID em https://winget.run e corrija no catalogo.json."]
    else:
        linhas.append("Todos os links estão funcionando. ✅")
    with open(a.relatorio, "w", encoding="utf-8") as f:
        f.write("\n".join(linhas) + "\n")

    print(f"Com problema: {len(quebrados)}  •  versão anterior: {len(recuperados)}  •  tirados: {len(removidos)}")
    # Avisa a automação (GitHub Actions) se é preciso abrir o aviso
    saida_gh = os.environ.get("GITHUB_OUTPUT")
    if saida_gh:
        with open(saida_gh, "a", encoding="utf-8") as f:
            f.write(f"quebrados={len(quebrados)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
