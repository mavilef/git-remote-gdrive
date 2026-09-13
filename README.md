# git-remote-gdrive

Use uma pasta do Google Drive como remote de um repositório Git.

O helper implementa o protocolo `gitremote-helpers` e armazena os objetos em
`git bundle`. Cada push acrescenta um bundle imutável com os objetos novos e só
depois atualiza um manifesto com as refs remotas. Isso permite usar `git clone`,
`git fetch` e `git push` sem sincronizar a pasta do Drive no sistema de arquivos.

## Estado atual

O MVP suporta:

- clone e fetch;
- push incremental de branches e tags;
- exclusão de refs;
- push forçado e rejeição de non-fast-forward;
- `git push --dry-run`;
- cache local dos bundles baixados;
- verificação SHA-256 de cada bundle;
- arquivos Git LFS armazenados no Drive, com verificação SHA-256;
- detecção otimista de pushes concorrentes.

O uso suportado pelo MVP é de um push por vez para cada pasta remota.

## Instalação

Requisitos: Git e Python 3.10 ou mais recente.

Para instalar a partir deste checkout com `uv`:

```bash
uv tool install .
```

Durante o desenvolvimento, também é possível usar o ambiente do projeto:

```bash
uv sync
export PATH="$PWD/.venv/bin:$PATH"
```

Confirme que o Git consegue encontrar o helper:

```bash
git-remote-gdrive --help
```

## Configuração do Google

1. Crie ou selecione um projeto no Google Cloud.
2. Ative a Google Drive API.
3. Configure a tela de consentimento OAuth.
4. Crie um OAuth Client ID do tipo **Desktop app**.
5. Baixe o JSON das credenciais.

Defina o caminho do arquivo baixado:

```bash
export GDRIVE_CREDENTIALS_PATH=~/.config/git-remote-gdrive/credentials.json
```

Na primeira operação que acessar o Drive, o navegador será aberto para a
autorização. Por padrão, o token fica em
`~/.local/state/git-remote-gdrive/token.json`.

Variáveis opcionais:

```bash
export GDRIVE_TOKEN_PATH=/outro/local/token.json
export GDRIVE_CACHE_DIR=/disco/com/espaco/git-remote-gdrive-cache
export GDRIVE_LOG_LEVEL=INFO
```

O helper solicita o escopo completo do Drive. Use uma conta e um OAuth Client
ID sob seu controle e proteja tanto o JSON de credenciais quanto o token.

## Uso

Crie uma pasta dedicada no Google Drive e copie seu ID. Em uma URL como:

```text
https://drive.google.com/drive/folders/1AbCdEfGhIjKlMn
```

o ID é `1AbCdEfGhIjKlMn`.

Adicione o remote e faça o primeiro push:

```bash
git remote add drive gd://1AbCdEfGhIjKlMn
git push -u drive main
```

Para clonar em outra máquina um repositório sem Git LFS:

```bash
git clone gd://1AbCdEfGhIjKlMn meu-repositorio
```

**Se o repositório usa LFS, siga o fluxo de clone da seção [Git LFS](#git-lfs).**

Também são aceitos `gdrive://FOLDER_ID`, `googledrive://FOLDER_ID` e a forma
explícita `gdrive::FOLDER_ID`.

Branches, tags e exclusões usam os comandos normais do Git:

```bash
git push drive minha-branch
git push drive v1.0
git push drive --delete minha-branch
git fetch drive
```

### Git LFS

Com Git LFS 3.7.1 ou mais recente instalado, configure cada clone que usará LFS
no Drive:

```bash
git-lfs-gdrive install drive
git lfs track "*.bin"
git add .gitattributes arquivo.bin
git commit -m "Adiciona arquivo com LFS"
git push drive main
```

O comando instala os filtros e o hook de pre-push do Git LFS no repositório e
configura a transferência apenas para o remote escolhido. Os arquivos são
enviados ao Drive antes das referências Git, usando as mesmas credenciais e
variáveis do helper. Outros remotes mantêm sua configuração LFS.

O Git LFS atualiza os bytes transferidos e a velocidade durante cada arquivo,
a cada bloco de até 8 MiB. Seu percentual nativo conta objetos concluídos, então
pode continuar em `0% (0/1)` enquanto o volume transferido aumenta. Nos reenvios,
a verificação do objeto já existente no Drive também informa progresso. A
verificação inicial do arquivo local e a autenticação acontecem antes da
transferência e podem manter os contadores em zero por algum tempo.

Para clonar um repositório com LFS, baixe primeiro os ponteiros e depois os
arquivos. A configuração do agente é local e não é copiada pelo clone:

```bash
GIT_LFS_SKIP_SMUDGE=1 git clone gd://1AbCdEfGhIjKlMn meu-repositorio
cd meu-repositorio
git-lfs-gdrive install origin
git lfs pull origin
```

Se um clone direto terminou com `Clone succeeded, but checkout failed` e erro
`Could not resolve hostname gd`, configure o agente e conclua o checkout na pasta
que o Git criou. Execute a recuperação abaixo apenas nesse clone recém-criado,
antes de fazer alterações locais, pois ela restaura o índice e os arquivos de
trabalho a partir de `HEAD`:

```bash
cd meu-repositorio
git-lfs-gdrive install origin
git restore --source=HEAD --staged --worktree :/
```

Nesse caso, apenas `git lfs pull` pode terminar sem erro e deixar arquivos
ausentes, porque o checkout interrompido ainda não preencheu o índice.

Ao migrar um repositório que já usa LFS em outro remote, copie também os objetos
do histórico; publicar apenas as refs Git não garante essa cópia:

```bash
git lfs fetch --all origin
git-lfs-gdrive install drive
git lfs push --all drive
```

Depois publique as branches e tags com `git push`. Se houver `lfs.url` ou
`lfs.pushurl` no Git ou em `.lfsconfig`, mova essa configuração para o remote
correspondente (`remote.<nome>.lfsurl` / `remote.<nome>.lfspushurl`) antes de
instalar. Um remote pode ter pastas distintas para fetch e push, mas apenas uma
URL em cada direção. Ao trocar suas URLs, execute o instalador novamente.

## Como os dados ficam no Drive

Dentro da pasta escolhida, o helper mantém apenas esta estrutura:

```text
.git-remote-gdrive/
├── manifest.json
├── bundles/
│   ├── bundle-00000001-....bundle
│   └── bundle-00000002-....bundle
└── lfs/objects/
    └── <primeiros-2-caracteres-do-hash>/
        └── <sha256-do-arquivo>
```

Bundles antigos continuam necessários para reconstruir clones novos. O cache
local evita baixá-los novamente na mesma máquina. Ele pode crescer até o tamanho
do histórico remoto e pode ser apagado com segurança quando for necessário
liberar espaço; os bundles serão baixados de novo no próximo fetch que precisar
deles.

Os arquivos LFS também usam cache local e são verificados antes de cada envio
ou uso. No Drive, um objeto existente é verificado e reutilizado nos reenvios.

## Limitações do MVP

- Repositórios com object format SHA-256 ainda não são suportados; o padrão
  SHA-1 do Git funciona.
- Shallow clone, partial clone e filtros ainda não são suportados.
- Os bundles são append-only. Exclusões e force-push não liberam espaço até que
  exista um comando de compactação/garbage collection.
- A conferência do manifesto e sua gravação são operações separadas. O helper
  detecta alterações anteriores à conferência, mas pushes simultâneos ainda
  podem sobrescrever referências. Execute apenas um push por vez por pasta.
- O conteúdo dos bundles não recebe criptografia adicional. Quem puder ler a
  pasta do Drive poderá baixar o histórico Git.
- Locks de arquivos LFS não são suportados. Objetos LFS antigos não são removidos
  automaticamente do Drive.

## Desenvolvimento e testes

```bash
uv sync
.venv/bin/python -m unittest discover -s tests -v
```

Por padrão, os testes não acessam sua conta. Os testes end-to-end executam Git
real e o helper instalado sobre um backend local descartável. Os testes da API
simulam as respostas HTTP usando a biblioteca oficial do Google.
Os testes LFS usam o executável real do Git LFS e cobrem push, clone, atualização,
migração do histórico, falha de upload e isolamento entre remotes. São ignorados
se Git LFS não estiver instalado; a CI exige sua presença.

A CI gera e instala um wheel em um checkout limpo e executa a suíte em Python
3.10 e 3.14. Para gerar um pacote de distribuição, use também um checkout limpo,
sem artefatos antigos em `build/`:

```bash
uv build
```

### Teste no Google Drive real

Depois de configurar as credenciais, informe o ID de uma pasta de testes:

```bash
GDRIVE_TEST_FOLDER_ID=ID_DA_PASTA_DE_TESTES \
  .venv/bin/python -m unittest discover -s tests -p test_live_drive.py -v
```

Esse teste cria uma subpasta temporária, valida push, clone com cache vazio,
fetch incremental, tags, exclusão e dry-run, e apaga apenas a subpasta criada ao
terminar. Sem `GDRIVE_TEST_FOLDER_ID`, ele é ignorado pela suíte. A primeira
execução pode abrir o navegador para autorização.

### Diagnóstico e recuperação

Para diagnosticar uma operação real:

```bash
GDRIVE_LOG_LEVEL=DEBUG git fetch drive
```

Se um push falhar durante a gravação do manifesto, a atualização pode já ter
sido concluída. Confira `git ls-remote drive` e execute `git fetch drive` antes
de repetir o push. O helper preserva os bundles após tentar publicar o manifesto;
uma falha pode deixar bundles sem referência, que ocuparão espaço até existir
compactação/garbage collection.

Documentação de referência:

- [Git remote helpers](https://git-scm.com/docs/gitremote-helpers)
- [Git bundle](https://git-scm.com/docs/git-bundle)
- [Google Drive API Python quickstart](https://developers.google.com/workspace/drive/api/quickstart/python)
