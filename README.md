# atualiza-cadastro-altasoft

Enriquece a planilha de clientes com **CNAE principal e secundários**,
**situação cadastral** e **contato (telefone/e-mail)**, consultando APIs
públicas de CNPJ.

A planilha de entrada **nunca é modificada**. As colunas novas são
acrescentadas no fim de uma cópia, e nada que já existe é sobrescrito.

## O problema

A base tem ~2300 clientes e faltam os CNAEs, a situação cadastral e boa parte
dos telefones. Esses dados existem na base pública da Receita Federal, exposta
por várias APIs gratuitas — mas todas com limite de vazão, algumas em 3
consultas por minuto. Consultar 2300 CNPJs numa única API levaria mais de 12
horas e qualquer interrupção começaria do zero.

A solução tem três partes: somar provedores para multiplicar a vazão, respeitar
o limite de cada um individualmente, e guardar o progresso fora da planilha para
que parar e retomar não custe nada.

## Instalação

```bash
python -m pip install -r requirements.txt
```

Requer Python 3.11+ (usa `tomllib` da biblioteca padrão). Dependências: apenas
`requests` e `openpyxl`.

## Uso

```bash
python -m cnpj_updater inspecionar
```

Mostra como a planilha está sendo lida (aba, coluna de CNPJ, quantos válidos e
inválidos) sem gravar nada. **Rode isto primeiro** ao apontar para uma planilha
nova, para conferir se a coluna certa foi encontrada.

```bash
python -m cnpj_updater importar
```

Carrega os CNPJs da planilha para a fila no SQLite. Rodar de novo depois de
acrescentar linhas na planilha só adiciona o que é novo.

```bash
python -m cnpj_updater rodar
```

Consulta as APIs. **Pode ser interrompido com Ctrl+C e retomado a qualquer
momento** — o progresso fica no banco, e retomar não gasta consulta repetida.
Use `--sem-email` para rodar só a fase rápida.

```bash
python -m cnpj_updater exportar
```

Gera a planilha de saída. Pode rodar enquanto o worker ainda trabalha, para ver
o resultado parcial. Rodar de novo reescreve as mesmas colunas em vez de
duplicá-las.

```bash
python -m cnpj_updater status
```

Resumo do que já foi coletado, com a taxa de preenchimento de cada campo.

## Estrutura do projeto

| Arquivo | Responsabilidade |
|---|---|
| `cnpj_updater/cli.py` | Os cinco comandos e a formatação dos relatórios no terminal. |
| `cnpj_updater/config.py` | Lê o `config.toml` e resolve caminhos relativos. |
| `cnpj_updater/cnpj.py` | Normaliza, valida (dígito verificador) e formata CNPJ e código CNAE. |
| `cnpj_updater/modelo.py` | O formato único (`Dados`) em que todo provedor entrega o resultado, e a normalização de situação cadastral e telefone. |
| `cnpj_updater/store.py` | Estado das consultas em SQLite: filas, gravação, resumo. |
| `cnpj_updater/ratelimit.py` | *Token bucket* e cooldown, um por provedor. |
| `cnpj_updater/providers/base.py` | Contrato comum: monta a URL, trata o HTTP, traduz status em exceção. |
| `cnpj_updater/providers/receita_dump.py` | BrasilAPI e Minha Receita (mesmo esquema, parser compartilhado). |
| `cnpj_updater/providers/com_email.py` | CNPJá Open, CNPJ.ws Pública e ReceitaWS. |
| `cnpj_updater/worker.py` | O pool: escolhe provedor, executa as duas fases, trata falha. |
| `cnpj_updater/excel_io.py` | Lê a planilha, detecta a coluna de CNPJ, escreve as colunas novas. |
| `tests/test_parsers.py` | Testes da normalização, dos parsers e da comparação de contato. |

## Como funciona

O SQLite é a fonte da verdade, não a planilha. É isso que permite parar e
retomar sem perder progresso e sem reconsultar o que já veio — importante
porque as APIs gratuitas têm limite de vazão e a base tem ~2300 CNPJs.
Escrever direto na planilha a cada consulta significaria perder tudo num
travamento, ou corromper o arquivo se ele estivesse aberto no Excel.

```
planilha  ──importar──>  SQLite  ──rodar──>  SQLite  ──exportar──>  planilha nova
                            (fila)            (APIs)      (join por CNPJ)
```

O `join` por CNPJ no export tem um efeito útil de graça: se o mesmo CNPJ
aparece em duas linhas da planilha, ambas são preenchidas com uma única
consulta.

### As duas fases

O desenho vem de uma medição, não de suposição: **BrasilAPI e Minha Receita
retornam `email` sempre nulo** (redigido na origem), mas aguentam vazão alta.
As outras três entregam e-mail, com 3–5 req/min cada.

| API | Telefone | E-mail | CNAE pri+sec | Situação | Vazão |
|---|---|---|---|---|---|
| BrasilAPI | sim | **não** | sim | sim | alta |
| Minha Receita | sim | **não** | sim | sim | alta |
| CNPJá Open | sim | sim | sim | sim | ~5/min |
| CNPJ.ws Pública | sim | sim | sim | sim | ~3/min |
| ReceitaWS | sim | sim | sim | sim | ~3/min |

**Fase 1 — dados cadastrais.** Todos os provedores servem, então roda rápido.
Mas quando um provedor com e-mail tem token sobrando, ele é preferido: a
requisição ia acontecer de qualquer jeito, e assim o e-mail vem de graça e a
fila da fase 2 encurta. Isso não atrasa a fase 1, porque só é escolhido um
provedor que está livre naquele instante.

**Fase 2 — e-mail.** Só as empresas que interessam (por padrão, apenas as
`Ativa`s) e só nos provedores que retornam e-mail. É a parte lenta, mas o
essencial já está na mão quando ela começa.

### Limite de vazão

Cada provedor tem seu próprio *token bucket*, dimensionado pelo `rpm` do
config. O balde começa cheio, para não penalizar o primeiro lote, e recarrega
continuamente em vez de em janelas fixas — isso evita a rajada de `429` que
acontece quando vários provedores viram a janela ao mesmo tempo.

Um `429` põe **aquele** provedor em cooldown exponencial (60s, 120s, 240s…) sem
afetar os outros; um sucesso zera a escalada. Se o servidor mandar
`Retry-After`, esse valor é respeitado. Quando todos estão em espera, o worker
dorme em fatias de 1 segundo para continuar respondendo a Ctrl+C.

### Coluna `Fonte`

Registra qual API respondeu cada linha. Sem ela não é possível distinguir
"esta empresa não tem e-mail na Receita" de "esta veio de um provedor que não
fornece e-mail" — e essa diferença decide se vale reconsultar a linha.

### Comparação com o contato existente

Quando a planilha já tem colunas de contato (configuráveis em
`colunas_telefone_atuais` e `coluna_email_atual`), o export gera duas colunas
de veredito: `igual`, `divergente`, `só na Receita`, `só na planilha`,
`ambos vazios` ou `não consultado`. Serve para auditar uma base antiga sem
conferir linha por linha.

Dois cuidados que fazem essa coluna ser confiável:

- Telefones são comparados pelos **últimos 8 dígitos**, para tolerar DDD
  ausente na base antiga e o nono dígito que os celulares ganharam depois.
  Comparar a string inteira marcaria como divergente número que é o mesmo.
- `não consultado` existe porque a fase 1 é resolvida em boa parte por
  provedores que não retornam e-mail. Nessas linhas, dizer "só na planilha"
  afirmaria que a Receita não tem e-mail — conclusão errada, porque o campo
  nunca foi perguntado. Só depois da fase 2 o veredito vale.

## Particularidades da base tratadas

Encontradas na base real e cobertas por teste:

- **Campo de 15 posições.** O sistema de origem preenche a inscrição com zero
  à esquerda até 15 dígitos (`082509423000104` = `82509423000104`). São 14% da
  base; rejeitar por tamanho descartaria centenas de clientes válidos sem aviso.
- **Zero à esquerda perdido.** CNPJ guardado como número no Excel vira `191`;
  é reconstruído para `00000000000191`.
- **Apóstrofo de texto** (`'05879113000122`) e formatação (`33.000.167/0001-01`).
- **Dígito verificador** conferido, para separar erro de digitação de dado
  faltando. Na base real isolou 7 linhas: `00000000000000`, `88888888888888` e
  5 erros de digitação.
- **ReceitaWS sinaliza erro com HTTP 200** e `{"status":"ERROR"}` no corpo.
  Sem tratar isso, o worker gravaria "sucesso" com todos os campos vazios.
- **CNAE `0`/`00.00-0-00`** é marcador de "sem atividade secundária", não um
  CNAE real.

## Limitação conhecida

Telefone e e-mail vêm do cadastro da Receita Federal, preenchido pelo contador
na abertura ou na última alteração. Uma parte vem vazia ou desatualizada — não
é falha do programa. Os CNAEs e a situação cadastral são confiáveis.

Medido na base real: CNAE e situação chegam praticamente completos (~99%),
telefone em torno de 60%. Rode `status` para ver a taxa atual da sua base.

## Testes

```bash
python -m unittest discover -s tests -v
```

Cobrem a normalização de CNPJ, os parsers de cada provedor (com recortes reais
das respostas), o limitador de vazão e a comparação de contato. Se uma API
mudar o formato, o teste quebra aqui em vez de o worker gravar 2300 linhas
vazias sem ninguém perceber.

## Configuração

Tudo em [`config.toml`](config.toml): caminhos da planilha, aba e coluna de
CNPJ, vazão de cada provedor (`rpm`), tentativas, e quais situações cadastrais
merecem a consulta de e-mail. Provedores podem ser desligados com
`ativo = false` sem mexer no código.

A pasta `dados/` está no `.gitignore`: a planilha de clientes e o banco de
consultas não vão para o repositório.
