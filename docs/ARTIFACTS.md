# Artefatos: documentos, dados e imagens pelo chat

O Wotan gera entregas reais (PDF, Word, Excel, PowerPoint, CSV, PNG) direto
pelo agente, com **auto-verificacao**: todo arquivo e reaberto e inspecionado
(`doc_read`) antes do agente declarar a tarefa concluida, e o `finish_task`
so aceita quando os artefatos passam no checador em disco.

## Instalacao

As bibliotecas sao as consagradas do ecossistema Python e entram como extra
opcional (o resto do Wotan continua sem dependencias novas):

```powershell
pip install -e ".[artifacts]"
# python-docx, openpyxl, python-pptx, reportlab, pillow, faker, pypdf
```

Sem o extra, as tools respondem com `ERROR / WHY / HOW TO FIX` pedindo a
instalacao - nunca quebram o turno.

## Tools

| Tool | O que gera |
|------|------------|
| `doc_pdf` | PDF a partir de markdown-lite: titulos, **negrito**, tabelas `\|...\|`, bullets, imagens `![alt](path)`, `<<<PAGEBREAK>>>`, numeracao de pagina e metadados |
| `doc_docx` | Word (.docx) do mesmo markdown-lite (tab. estilizadas, hyperlinks) |
| `doc_xlsx` | Excel (.xlsx): celulas tipadas (numero, data ISO, bool, `=FORMULA`), header congelado, autofiltro, largura de coluna automatica |
| `doc_pptx` | PowerPoint (.pptx): layouts `title`, `section`, `bullets`, `two_content`, `image`, `quote`, notas do apresentador, 16:9 ou 4:3 |
| `doc_read` | Le de volta pdf/docx/xlsx/pptx/csv/json/txt como texto (verificacao) |
| `data_csv` | CSV (delimitador, encoding `utf-8-sig` para Excel, formatos `{header, rows}` / lista de objetos) |
| `data_synthetic` | Dados sinteticos REALISTAS com seed deterministica: nome, e-mail, CPF/CNPJ com digito verificador valido, telefone, CEP, endereco, datas, money, categorias (Faker, locale pt_BR) |
| `data_chart` | Graficos PNG: `bar`, `hbar`, `line`, `area`, `pie`, `donut`, `scatter`, `histogram` (desenhados com Pillow, offline, paletas prontas) |
| `img_transform` | Cadeia de ops em imagens: resize, crop, rotate, grayscale, flip, brightness, contrast, watermark, border |
| `img_satellite` | Imagem de satelite REAL de um lat/lon (mosaico de tiles; provider configurado em `artifacts.satellite`, com allow-list de host) |

## Exemplo: contrato de venda de terreno com foto de satelite

Pedidos que o agente resolve de uma vez:

> crie um contrato de venda do meu terreno em PDF, com foto de satelite e
> planilha de parcelas

Fluxo que o agente executa:

1. `img_satellite {lat, lon, zoom: 18, path: "terreno/satelite.jpg"}` - busca
   a imagem real (requer `artifacts.satellite.base_url` no config; ver
   `config.example.yaml`). Sem rede/provider, ele avisa e segue com imagem
   local ou planta desenhada com `data_chart`.
2. Planilha de parcelas com `doc_xlsx` (total via `=SUM(...)`).
3. `doc_pdf` com o contrato em markdown-lite, embutindo `![foto aerea](terreno/satelite.jpg)`
   e a tabela de valores; Disclaimer: modelo gerado automaticamente, nao
   substitui assessoria juridica.
4. `doc_read` em cada arquivo, `finish_task` citando os artefatos - o gate
   abre os arquivos e confere header + conteudo antes de aceitar.

Markdown-lite aceito pelos writers (mesma sintaxe para PDF e Word):

```markdown
# Titulo
## Seccao
Paragrafo com **negrito**, *italico*, `codigo` e [link](https://exemplo.br).

| Coluna | Valor |
|--------|-------|
| Area   | 360m2 |

- bullet
  - sub-bullet

![legenda](caminho/da/imagem.png)

> Citação

<<<PAGEBREAK>>>
```

## Dados sinteticos (deterministicos)

```json
{
  "schema": {
    "nome": "name",
    "cpf": "cpf",
    "email": "email",
    "salario": {"type": "money", "min": 1500, "max": 20000},
    "admissao": {"type": "date_range", "start": "-3y", "end": "today"},
    "uf": {"type": "choice", "choices": ["SP", "RJ", "MG"], "weights": [5, 2, 1]}
  },
  "rows": 500, "seed": 42, "format": "xlsx", "path": "rh/funcionarios.xlsx"
}
```

Mesma `seed` => mesmos dados (reprodutivel p/ testes e auditoria). Tipos:
`name, first_name, last_name, email, company, job, cpf, cnpj, cpf_cnpj, rg,
phone, cellphone, cep, address, street, city, state, country, neighborhood,
date, date_this_year, date_range, datetime, int, number, money, bool, choice,
uuid, word, sentence, paragraph, url, ip, latitude, longitude`.

## Seguranca

- Toda saida fica **dentro do workspace** (path fora do root e recusado).
- `img_satellite` so busca hosts em `artifacts.satellite.allowed_hosts`
  (guarda contra SSRF) e embute a atribuicao do provider na imagem.
- Documentos juridicos/financeiros gerados devem conter disclaimer - o prompt
  do agente ja instrui isso.
- `doc_read`+gate fecham o ciclo: arquivo corrupto/pequeno demais recusa o
  `finish_task`.

## Notas de robustez (relacionadas)

- `limits.llm_step_retries`: o turno do agente tolera erros transientes do
  gateway (429/5xx/reset/timeout) com retry e backoff - inclusive com
  `Retry-After` em formato HTTP-date.
- Os providers OpenAI/Anthropic tem retry proprio com jitter; um pilico de
  rede nao derruba mais a sessao.
