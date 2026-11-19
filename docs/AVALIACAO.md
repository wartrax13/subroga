# Avaliação do pipeline de IA (fixtures sintéticas)

Script: `backend/scripts/eval_fixtures.py [--provider openai|anthropic|heuristic] [--scenario auto|eletrico|transporte]`.
Relatórios completos: `backend/storage/eval/report_<provider>.json`, com o erro de cada página, cada campo, os conflitos e o uso de tokens.

Escopo: classificação por página, segmentação em documentos lógicos e extração de `CaseFacts` nos 3 cenários (`tests/fixtures/{auto,eletrico,transporte}`), comparados com `expected.json`. São 41 páginas em 28 arquivos. Os arquivos marcados como duplicata no gabarito ficam de fora, porque o pipeline real os descarta por hash.

## Resultado (8/11/2026)

| Métrica | heuristic | openai |
|---|---|---|
| Acurácia de `doc_type` por página | 100% (41/41) | 100% (41/41) |
| Acurácia de quebra de documento (`is_first_page_of_new_doc`) | 100% | 100% |
| Documentos lógicos idênticos ao gabarito | 31/31 | 31/31 |
| Campos de `facts` corretos (todos) | 50/52 | 50/52 |
| Campos corretos entre os que existem nos PDFs | 50/50 | 50/50 |
| Conflito de `valor_pago` detectado | 3/3 (0 espúrios) | 3/3 (0 espúrios) |
| Checklist de documentos faltantes | 3/3 | 3/3 |
| Tempo total (3 cenários) | 0,3 s | 66 s (cerca de 70% na classificação) |
| Custo aproximado | US$ 0 | US$ 0,083 (28 chamadas, cerca de 63 mil tokens de entrada e 5,4 mil de saída) |

Modelos OpenAI: `gpt-4.1-mini` para classificação e `gpt-4.1` para extração e narrativa, pela Responses API com PDF em base64 (`input_file`) e `text.format` json_schema strict. O custo foi estimado com a tabela `llm.PRICES` (US$/1M tokens: 4.1-mini 0,40/1,60; 4.1 2,00/8,00). Por cenário, cada caso custou cerca de US$ 0,03 e levou de 21 a 25 s. A narrativa ("Dos fatos") acrescenta cerca de US$ 0,01 por caso.

Os 2 campos "errados" (`causador_endereco` em eletrico e em transporte) não aparecem em nenhum PDF das fixtures. Os dois provedores devolveram `null`, que é o comportamento correto. Corrigir o gabarito depende de quem mantém `make_fixtures.py`.

## Rodadas OpenAI

1. Prompts classify-v2 e extract-v2: páginas 100%, quebras 100%, campos 49/50 alcançáveis. O erro foi `sinistro_local` no auto: o modelo usou o texto do aviso ("altura do nº 1500") em vez do local do B.O.
2. Prompt extract-v3, que manda copiar o "Local" do B.O. quando ele existe: 50/50.
3. Narrativa: narrative-v2 passou a marcar `[[REVISAR: valor_pago diverge entre documentos: …]]`, a pedir o termo de sub-rogação faltante e a proibir adjetivos inventados. Verificado com 1 chamada (US$ 0,009), sem nova rodada completa.

## Ressalvas

- As fixtures são sintéticas, pequenas e têm texto embutido. O provedor `heuristic` foi calibrado nelas: os 100% medem que as regras cobrem esse padrão e **não** valem como estimativa de desempenho com documentos reais (scans sem OCR caem em OUTROS com confiança 0,3 e vão para revisão).
- Os prompts enviam, junto com o PDF, o texto embutido de cada página (`--- página N ---`) para alinhar os índices. Em digitalizações esse texto vem vazio e o modelo depende da imagem da página. Ainda falta avaliar o pipeline com PDFs escaneados.
- O gabarito trata o TOTAL do orçamento como ocorrência de `valor_pago` (conflito do auto: 48.320,00 × 48.230,00). O prompt de extração segue essa convenção.
- O caminho Anthropic (tool use, `claude-haiku-5-5`/`claude-sonnet-5-5`) foi mantido e tem testes com cliente fake, mas não foi avaliado com a API real.
