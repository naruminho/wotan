---
name: documentos-br
description: Modelos e boas praticas para gerar documentos brasileiros com as tools de artefatos (contratos, planilhas, relatorios com foto de satelite). Ler antes de gerar documentos juridicos/financeiros.
---

# Documentos brasileiros (modelos Wotan)

## Contrato (doc_pdf) - estrutura minima

```markdown
# CONTRATO DE <NATUREZA> Nº <NNN/AAAA>

**IDENTIFICAÇÃO DAS PARTES**
**CONTRATADA:** <nome>, <nacionalidade>, <estado civil>, RG <rg>, CPF <cpf>.
**CONTRATANTE:** <nome>, <nacionalidade>, <estado civil>, RG <rg>, CPF <cpf>.

## Cláusula 1ª - Objeto
<descrição objetiva, com qualificadores do imóvel/bem: matrícula, área, endereço>

## Cláusula 2ª - Preço e Forma de Pagamento
| Condição | Valor (R$) | Vencimento |
|----------|-----------:|------------|

## Cláusula 3ª - Obrigações da(s) parte(s)
## Cláusula 4ª - Foro e Legislação Aplicável
Fica eleito o foro da comarca de <cidade>/<UF>.

<cidade>, <data por extenso>.

** Assinaturas (linhas) **

> Modelo gerado automaticamente pelo Wotan - NÃO constitui aconselhamento
> jurídico. Valide com um advogado antes de assinar.
```

Regras: use CPF/CNPJ APENAS os que o usuário informar (nunca invente);
valores em **R$ 1.234,56** no texto e `1234.56` na planilha; cláusulas sempre
com número ordinal (`Cláusula 1ª`).

## Planilha de parcelas (doc_xlsx)

- Linha 1 = header estilizado; datas ISO (`2026-10-15`) viram datas de verdade.
- Totais com fórmula: `["TOTAL", "=SUM(B2:B13)"]`.
- `autofilter: true` em tabelas > 5 linhas.

## Imóveis - foto aérea

1. `img_satellite` com lat/lon (zoom 17-19 para lotes; 14-16 para fazendas).
2. Embed no PDF/DOCX com `![Foto aérea do imóvel](caminho/satelite.jpg)`.
3. Sem provider configurado: informe o usuário e peça uma imagem local.

## Verificação (obrigatória antes do finish_task)

`doc_read` em cada arquivo gerado + citação em `artifacts: [...]`.
Confira: acentos corretos, tabelas completas, imagens presentes (o PDF
mostra `[image not found: ...]` quando falta), disclaimer incluído.

## Imagens no deck (pessoas e infograficos)

- Pessoas/equipes: gere com `img_llm` pedindo fotografia realista (a tool ja
  reforca: luz natural, textura de pele real, look 85mm). Nunca use clipart.
- Infograficos/esquemas: `img_llm` com "design editorial minimalista, paleta
  dessaturada, linhas finas" (auto-reforcado). NUNCA peca neon/gradiente.
- Graficos de dados: sempre `data_chart` (paleta `slate` combina com todos
  os temas do deck) embutidos no layout `chart`.

## Fixtures de OCR (documento escaneado sintetico)

`doc_scan_image` / `doc_scan_pdf` geram imagens/PDFs "escaneados" com campos
preenchidos a mao, checkboxes, assinatura e carimbo - deterministicos por
`seed` (mesma seed = mesma imagem, ideal p/ conjuntos de teste). Modos:
`scan`, `photo` (perspectiva de celular), `photocopy` (P&B duro). Combine com
`data_synthetic` para variar nomes/CPF entre fixtures.
