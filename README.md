# Kyroos — catálogo online

Este repositório alimenta o Kyroos Toolkit. Ao abrir, o app baixa daqui:

| Arquivo | Para que serve | Quem atualiza |
|---|---|---|
| `catalogo.json` | Lista de programas que o Kyroos conhece | Você (editando aqui no GitHub) |
| `links.json` | Link oficial, SHA256 e parâmetros silenciosos de cada programa | Automático, todo dia |
| `verificacao.json` | Histórico da verificação dos links (quando funcionou, quantas falhas seguidas) | Automático, todo dia |
| `perfis.json` | Perfis prontos (PC Básico, Escritório, Gamer...) | Você |
| Releases | Versões novas do `Kyroos.exe` (atualização automática) | Você |

## Como colocar no ar (uma vez só)

1. No GitHub, crie um repositório **público** chamado `kyroos-catalogo`.
2. Envie os arquivos desta pasta. O arquivo `sobe-para-github/atualizar-links.yml` precisa ficar no
   GitHub em `.github/workflows/atualizar-links.yml`: no site, clique em **Add file → Create new file**,
   digite esse caminho como nome e cole o conteúdo. (A pasta `sobe-para-github` em si não precisa ser enviada.)
3. Em **Settings → Actions → General → Workflow permissions**, marque **Read and write permissions** e salve.
4. Na aba **Actions**, abra "Atualizar links.json" e clique em **Run workflow** para gerar o primeiro links.json.
5. Pronto: o Kyroos já vem configurado para `kyroos-app/kyroos-catalogo` (não precisa preencher nada).

A partir daí o `links.json` se atualiza sozinho todo dia de madrugada.

## Adicionar ou corrigir um programa

Edite o `catalogo.json` direto no GitHub. O campo `winget` precisa ser o ID exato do programa
no winget (procure em https://winget.run ou com `winget search nome`). Ao salvar, a automação
roda e gera o link do programa novo. Todos os Kyroos recebem a mudança na próxima vez que abrirem.

## Publicar uma versão nova do Kyroos

1. No projeto, aumente `<Version>` no `InstaladorProgramWin.csproj` (ex.: `1.3.0`).
2. Publique o `.exe` (veja `Ferramentas\publicar-kyroos.ps1` no projeto).
3. Aqui no GitHub: **Releases → Draft a new release**, tag `v1.3.0`, anexe o `Kyroos.exe` e publique.

Os Kyroos instalados avisam da versão nova e se atualizam sozinhos.

## Espelho no seu servidor (opcional)

Se o GitHub sair do ar, o Kyroos procura os mesmos arquivos em `https://centerservers.com.br/kyroos/`.
Para manter o espelho igual ao GitHub, coloque no servidor (cron, 1 vez por dia):

```bash
for f in links.json catalogo.json perfis.json; do
  curl -fsSL "https://raw.githubusercontent.com/kyroos-app/kyroos-catalogo/main/$f" -o "/var/www/kyroos/$f.tmp" \
    && mv "/var/www/kyroos/$f.tmp" "/var/www/kyroos/$f"
done
```

## Gerar o links.json no seu PC (teste)

```bash
pip install pyyaml
python scripts/gerar_links.py --catalogo catalogo.json --saida links.json
```

## Verificação diária dos links

Depois de gerar o `links.json`, a automação roda `scripts/verificar_links.py`:

1. Testa se cada link responde e se é mesmo um arquivo (não uma página de erro).
2. Confere o SHA256 baixando o instalador inteiro — cada programa 1 vez por semana (arquivos acima de 700 MB são pulados).
3. Link quebrado: volta para a versão de ontem, se ela ainda funcionar. Se não, tira o programa do `links.json`
   (o Kyroos usa o catálogo de dentro do app e depois o winget).
4. Se algo quebrou, abre um aviso na aba **Issues** com a lista (o GitHub manda e-mail). Quando tudo volta ao normal, o aviso fecha sozinho.
5. Grava a data do teste em cada programa (`verificadoEm`) e o histórico em `verificacao.json`.

Testar no seu PC: `python scripts/verificar_links.py --links links.json --sem-hash`
