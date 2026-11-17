# [Claude] ARQUIVO NOVO — gerador do links.json do Kyroos
#
# O que faz:
#   1. Lê o catalogo.json (lista dos programas que o Kyroos conhece, com o ID do winget).
#   2. Baixa do GitHub só a parte necessária do repositório oficial do winget
#      (microsoft/winget-pkgs) — sem baixar os outros milhares de programas.
#   3. Para cada programa, acha a versão ESTÁVEL mais nova (ignora beta, rc, preview...)
#      e lê o manifesto: link oficial do instalador, SHA256 e parâmetros silenciosos.
#   4. Grava tudo em um único links.json, que o Kyroos baixa ao abrir.
#
# Como rodar no seu PC (precisa de Python 3 e Git):
#   pip install pyyaml
#   python scripts/gerar_links.py --catalogo catalogo.json --saida links.json
#
# No GitHub ele roda sozinho todo dia pelo arquivo .github/workflows/atualizar-links.yml

import argparse
import datetime
import json
import os
import re
import subprocess
import sys

import yaml

REPO_WINGET = "https://github.com/microsoft/winget-pkgs"

# Palavras que indicam versão de teste — essas versões nunca entram no links.json
PALAVRAS_TESTE = re.compile(r"(alpha|beta|preview|rc\d*|dev|canary|nightly|insider|pre|snapshot|test)", re.I)

# Tipos de instalador que o Kyroos sabe rodar em silêncio sozinho
TIPOS_ACEITOS = {"exe", "msi", "wix", "inno", "nullsoft", "burn"}

# Parâmetros silenciosos padrão quando o manifesto não informa (mesmos que o winget usa)
SILENCIOSO_PADRAO = {
    "msi": "/qn /norestart",
    "wix": "/qn /norestart",
    "inno": "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-",
    "nullsoft": "/S",
    "burn": "/quiet /norestart",
}


def git(pasta, *args, check=True):
    r = subprocess.run(["git", "-C", pasta, *args], capture_output=True, text=True, encoding="utf-8")
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} falhou: {r.stderr.strip()}")
    return r.stdout


def clonar_winget(pasta):
    """Clone 'magro': só a estrutura de pastas, sem o conteúdo dos arquivos (rápido, ~150 MB)."""
    if os.path.isdir(os.path.join(pasta, ".git")):
        git(pasta, "fetch", "--depth", "1", "--filter=blob:none", "origin", "master")
        git(pasta, "reset", "--hard", "origin/master")
        return
    subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", REPO_WINGET, pasta],
                   check=True)


def caminho_pacote(pasta_repo, winget_id):
    """Ex.: "Google.Chrome" -> manifests/g/Google/Chrome
    O ID do winget não diferencia maiúsculas ("dbeaver.dbeaver" = "dbeaver.DBeaver"), mas as
    pastas no GitHub sim — por isso cada parte é procurada sem diferenciar maiúsculas."""
    caminho = f"manifests/{winget_id[0].lower()}"
    for parte in winget_id.split("."):
        pastas = listar_pastas(pasta_repo, caminho)
        achada = next((p for p in pastas if p == parte), None) \
            or next((p for p in pastas if p.lower() == parte.lower()), None)
        if achada is None:
            return None
        caminho = f"{caminho}/{achada}"
    return caminho


def chave_versao(v):
    """Ordena versões como número: 10.2 > 9.10 > 9.9"""
    partes = re.split(r"[.\-_+ ]", v)
    chave = []
    for p in partes:
        if p.isdigit():
            chave.append((1, int(p), ""))
        else:
            m = re.match(r"(\d+)(.*)", p)
            chave.append((1, int(m.group(1)), m.group(2)) if m else (0, 0, p))
    return chave


def listar_pastas(pasta_repo, caminho):
    saida = git(pasta_repo, "ls-tree", "-d", "--name-only", f"HEAD:{caminho}", check=False)
    return [linha.strip() for linha in saida.splitlines() if linha.strip()]


def listar_arquivos(pasta_repo, caminho):
    saida = git(pasta_repo, "ls-tree", "--name-only", f"HEAD:{caminho}", check=False)
    return [linha.strip() for linha in saida.splitlines() if linha.strip()]


def escolher_versao(pasta_repo, winget_id):
    """Devolve (versao, caminho_do_manifesto) da versão estável mais nova, ou None."""
    base = caminho_pacote(pasta_repo, winget_id)
    if base is None:
        return None
    nome_id = base.split("/", 2)[2].replace("/", ".")  # ID com as maiúsculas certas
    candidatas = [p for p in listar_pastas(pasta_repo, base)
                  if re.search(r"\d", p) and not PALAVRAS_TESTE.search(p)]
    for versao in sorted(candidatas, key=chave_versao, reverse=True)[:5]:
        arquivos = listar_arquivos(pasta_repo, f"{base}/{versao}")
        # Formato atual: <ID>.installer.yaml ; formato antigo: um único <ID>.yaml
        for nome in (f"{nome_id}.installer.yaml", f"{nome_id}.yaml"):
            if nome in arquivos:
                return versao, f"{base}/{versao}/{nome}"
    return None


def baixar_arquivos(pasta_repo, caminhos):
    """[Claude 16/11] Descobre o ID (hash) de cada arquivo, baixa todos num pedido só e lê com 'git cat-file --batch'."""
    oids = {}
    for i in range(0, len(caminhos), 200):
        lote = caminhos[i:i + 200]
        saida = git(pasta_repo, "ls-tree", "HEAD", "--", *lote, check=False)
        for linha in saida.splitlines():
            meta, caminho = linha.split("\t", 1)
            oids[caminho] = meta.split()[2]
    if not oids:
        return {}
    unicos = sorted(set(oids.values()))
    subprocess.run(["git", "-C", pasta_repo, "-c", "fetch.negotiationAlgorithm=noop", "fetch", "-q", "origin",
                    "--no-tags", "--no-write-fetch-head", "--recurse-submodules=no", "--filter=blob:none", "--stdin"],
                   input="\n".join(unicos) + "\n", text=True, check=False)
    r = subprocess.run(["git", "-C", pasta_repo, "cat-file", "--batch"], input=("\n".join(unicos) + "\n").encode(),
                       capture_output=True, check=True)
    dados, pos, por_oid = r.stdout, 0, {}
    for oid in unicos:
        fim = dados.index(b"\n", pos)
        cab = dados[pos:fim].split()
        if len(cab) < 3 or cab[1] == b"missing":
            pos = fim + 1
            continue
        tam = int(cab[2])
        por_oid[oid] = dados[fim + 1:fim + 1 + tam].decode("utf-8", "replace")
        pos = fim + 1 + tam + 1
    return {c: por_oid.get(o) for c, o in oids.items()}


def ler_arquivo(pasta_repo, caminho):
    return git(pasta_repo, "show", f"HEAD:{caminho}")


def pontuar(inst):
    """Quanto maior, melhor: 64 bits, instalação para todos os usuários, português/inglês."""
    arq = {"x64": 30, "neutral": 20, "x86": 10}.get(str(inst.get("Architecture", "")).lower(), 0)
    escopo = {"machine": 5, "": 3, "user": 1}.get(str(inst.get("Scope", "")).lower(), 0)
    idioma = str(inst.get("InstallerLocale", "")).lower()
    pont_idioma = 3 if idioma.startswith("pt") else 2 if idioma in ("", "en-us") else 0
    return arq + escopo + pont_idioma


def escolher_instalador(manifesto):
    """Junta os campos gerais do manifesto com os de cada instalador e escolhe o melhor."""
    geral = {k: v for k, v in manifesto.items() if k != "Installers"}
    opcoes = []
    for inst in manifesto.get("Installers", []) or []:
        junto = {**geral, **inst}
        # Chaves de dicionário (ex.: InstallerSwitches) também se juntam
        if isinstance(geral.get("InstallerSwitches"), dict) or isinstance(inst.get("InstallerSwitches"), dict):
            junto["InstallerSwitches"] = {**(geral.get("InstallerSwitches") or {}),
                                          **(inst.get("InstallerSwitches") or {})}
        tipo = str(junto.get("InstallerType", "")).lower()
        if tipo not in TIPOS_ACEITOS:
            continue
        if str(junto.get("Architecture", "")).lower() == "arm64":
            continue
        if not junto.get("InstallerUrl") or not junto.get("InstallerSha256"):
            continue
        if not argumentos_silenciosos(junto):
            continue  # sem jeito de instalar em silêncio: tenta outro instalador do mesmo programa
        opcoes.append(junto)
    if not opcoes:
        return None
    return max(opcoes, key=pontuar)


def argumentos_silenciosos(inst):
    tipo = str(inst.get("InstallerType", "")).lower()
    chaves = inst.get("InstallerSwitches") or {}
    args = chaves.get("Silent") or chaves.get("SilentWithProgress") or SILENCIOSO_PADRAO.get(tipo)
    if not args:
        return None  # .exe sem parâmetro silencioso conhecido: não dá pra instalar sem janelas
    extra = chaves.get("Custom")
    return f"{args} {extra}".strip() if extra else str(args)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalogo", default="catalogo.json")
    ap.add_argument("--saida", default="links.json")
    ap.add_argument("--pasta-winget", default=os.path.join(os.getcwd(), ".winget-pkgs"))
    a = ap.parse_args()

    with open(a.catalogo, encoding="utf-8-sig") as f:
        catalogo = json.load(f)

    ids = [p["winget"] for p in catalogo["programas"]
           if p.get("winget") and (p.get("fonte") or "winget") == "winget"]

    print(f"Catálogo: {len(ids)} programas do winget")
    clonar_winget(a.pasta_winget)

    # Escolhe as versões só olhando a estrutura (sem baixar arquivos)
    escolhidos = {}
    for winget_id in ids:
        r = escolher_versao(a.pasta_winget, winget_id)
        if r:
            escolhidos[winget_id] = r
        else:
            print(f"  [sem versão estável] {winget_id}")

    # [Claude 16/11] Baixa de uma vez só os manifestos escolhidos, em lote (rápido mesmo com 1.000+ programas).
    # [Claude 16/11] Antes usava "sparse-checkout", que fica muito lento com centenas de pastas.
    textos = baixar_arquivos(a.pasta_winget, [c for _, c in escolhidos.values()])

    programas = {}
    sem_link = []
    for winget_id, (versao, caminho) in escolhidos.items():
        try:
            manifesto = yaml.safe_load(textos.get(caminho) or ler_arquivo(a.pasta_winget, caminho))
            inst = escolher_instalador(manifesto)
            if not inst:
                sem_link.append(winget_id)
                continue
            programas[winget_id] = {
                "versao": str(versao),
                "url": inst["InstallerUrl"],
                "sha256": str(inst["InstallerSha256"]).lower(),
                "tipo": str(inst["InstallerType"]).lower(),
                "args": argumentos_silenciosos(inst),
                "arquitetura": str(inst.get("Architecture", "")).lower(),
            }
        except Exception as e:  # um manifesto com problema não derruba o resto
            print(f"  [erro] {winget_id}: {e}")
            sem_link.append(winget_id)

    saida = {
        "versao": 1,
        "geradoEm": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fonte": "microsoft/winget-pkgs",
        "total": len(programas),
        "programas": dict(sorted(programas.items(), key=lambda kv: kv[0].lower())),
    }
    with open(a.saida, "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=1)

    print(f"\nlinks.json: {len(programas)} com link direto, "
          f"{len(sem_link)} sem link (ficam no winget), "
          f"{len(ids) - len(escolhidos)} sem versão estável")
    if sem_link:
        print("Sem link direto: " + ", ".join(sem_link))
    return 0


if __name__ == "__main__":
    sys.exit(main())
