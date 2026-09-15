# Occurrence Temporal Semantics and Initial Multiple-Occurrence Policy

## Finalidade

Este documento define o contrato temporal de `Occurrence` e a política
inicial para múltiplas occurrences (M3-04). Ele completa a semântica que
`docs/analysis-core-contracts.md` reservou para `Occurrence.temporal_position`
e para um `end` eventual, sem alterar o matcher single-cue, sua política de
aceitação ou a orquestração multi-cue existentes.

O contrato conceitual admite que uma cue produza de zero a várias
occurrences. A capacidade material disponível hoje é mais estreita: uma
invocação de `match_cue` com o método
`normalized_cross_correlation_v1` expõe no máximo um candidato aceito. A
diferença é intencional. O modelo não impede métodos futuros de produzir
várias occurrences, mas este documento não atribui ao método atual um
comportamento que ele não implementa nem que as fixtures de M2 tenham
observado.

Este documento é normativo para a semântica de tempo e multiplicidade, não
para a forma de serialização JSON. A forma versionada do Analysis Result,
inclusive a representação de um campo opcional ausente, pertence a M3-06.

## 1. Contrato Temporal de `Occurrence`

Uma `Occurrence` representa somente um match aceito para uma cue identificada.
Seu contrato temporal possui dois valores conceituais:

| Valor | Obrigatoriedade | Semântica |
|---|---|---|
| `temporal_position` | Obrigatório | Início ou âncora temporal da occurrence na timeline do source canonicalizado. |
| `end` | Opcional e dependente do método | Limite superior exclusivo da occurrence na mesma timeline, presente somente quando o método consegue produzi-lo com semântica e precisão sustentadas por evidência. |

### 1.1. `temporal_position`

`temporal_position` mantém exatamente a convenção já fixada por
`docs/matching-contract.md` seção 3:

- a origem é o instante zero do áudio de source **após** canonicalização;
- a unidade é segundos;
- o valor é finito e não negativo;
- o valor não ultrapassa a duração do source canonicalizado;
- para um resultado `found` de `match_cue`, o valor corresponde a
  `MatchResult.timestamp_seconds`, isto é, ao início da janela candidata de
  melhor score aceita pela configuração efetiva.

O valor é obrigatório em toda `Occurrence`. Ausência de posição em um
resultado que se apresenta como occurrence é uma violação de contrato, não
um `no_match` implícito.

Para `normalized_cross_correlation_v1`, o timestamp é obtido dividindo o
índice inteiro de lag pela taxa de amostragem canônica. Isso coloca o valor
na grade de amostras do áudio canonicalizado. A evidência sintética de M2-03
adota um período de amostra (`1 / 48000` segundo) como critério do baseline e
observa erro zero nos dois offsets conhecidos. Essa evidência não promete a
mesma precisão para mídia real, para o instante da mídia anterior à
canonicalização, nem para outro método.

Uma janela de busca por Cue não altera essa origem. A Application converte o
início solicitado para o índice da grade canônica, apresenta somente essa
fatia do source ao matcher e soma `start_index / sample_rate` ao timestamp
relativo antes de construir a `Occurrence`. Um `end` não nulo é deslocado pelo
mesmo valor. Portanto, os tempos publicados continuam absolutos na timeline do
source canonicalizado, inclusive quando a busca começou após zero.

### 1.2. `end`

Quando presente, `end` representa o primeiro instante imediatamente após a
extensão temporal atribuída à occurrence; portanto, o intervalo conceitual é
`[temporal_position, end)`. Um `end` válido:

- usa a mesma origem e a mesma unidade de `temporal_position`;
- é finito;
- é estritamente maior que `temporal_position`;
- não ultrapassa a duração do source canonicalizado;
- identifica o método e a regra documentada que o produziram;
- possui precisão sustentada pela saída ou por evidência específica desse
  método.

Se qualquer uma dessas condições não puder ser satisfeita, `end` deve estar
ausente. Não é permitido inventar, estimar ou inferir `end` apenas a partir de
`temporal_position`, do score, da duração da cue ou da duração da janela de
correlação. Em particular, o contrato atual de `match_cue` retorna um único
timestamp e um score; nenhum dos dois mede a extensão temporal da ocorrência.

Para `normalized_cross_correlation_v1`, `end` está **ausente**. Este documento
não escolhe se M3-06 representará essa ausência omitindo uma propriedade JSON
ou usando outro mecanismo de schema; ele fixa apenas que nenhum valor temporal
de fim pode ser publicado para esse método com a evidência atual.

## 2. Métodos Suportados

A política inicial deste documento suporta exclusivamente:

```text
normalized_cross_correlation_v1
```

Esse identificador é o usado por `EffectiveConfiguration` em
`baseline.py` e em `acceptance.py`. A política não é universal: um método
futuro não herda automaticamente as decisões de seleção, multiplicidade,
empate ou `end` aqui descritas. Ele precisa declarar sua própria compatibilidade
com este contrato e produzir evidência para qualquer capacidade adicional.

O score continua sendo similaridade específica do método, nunca confidence
calibrada e nunca um valor assumido como comparável entre métodos.

## 3. Evidência Disponível e Limite da Derivação

A evidência registrada por M2 não contém um caso em que a mesma cue apareça
mais de uma vez no mesmo source:

- `docs/matching-regression.md` registra seis casos determinísticos, todos
  com no máximo um offset verdadeiro por source;
- `docs/matching-robustness.md` registra seis casos de robustez, todos com no
  máximo uma ocorrência verdadeira por source;
- `docs/matching-acceptance.md` deriva a decisão `found`/`no_match` do mesmo
  corpus single-occurrence e não acrescenta fixtures multi-occurrence.

Logo, não existe observação de M2 da qual se possa inferir uma distância de
deduplicação, uma janela de agrupamento, uma regra de non-maximum suppression
ou o tratamento correto de duas ocorrências legítimas próximas. Este
documento registra essa ausência explicitamente; não apresenta uma regra
arbitrária como se tivesse sido observada.

O comportamento material que a evidência e a implementação permitem afirmar
é:

1. `_locate_best_candidate` calcula scores por lag e seleciona exatamente um
   índice com `numpy.argmax`;
2. o vetor de scores e todos os demais lags são internos e não aparecem em
   `MatchResult`;
3. em empate de score máximo, `numpy.argmax` escolhe o primeiro lag, portanto
   o mais cedo na timeline;
4. `match_cue` publica esse candidato somente quando seu score satisfaz a
   política de aceitação configurada;
5. `run_multi_cue_analysis` preserva esse caminho histórico quando o método
   persistido é `normalized_cross_correlation_v1`.

O desempate pelo lag mais cedo é comportamento específico da implementação
atual, não uma regra validada por fixture multi-occurrence nem uma garantia
para métodos futuros.

## 4. Política Histórica de Multiplicidade

Para cada cue processada uma vez por `run_multi_cue_analysis` com
`normalized_cross_correlation_v1`, aplica-se a seguinte política:

| Resultado de `match_cue` | Cardinalidade de `Occurrence` | Regra |
|---|---:|---|
| `found` | 1 | Criar uma occurrence para o único best candidate já aceito pelo matcher. Usar `timestamp_seconds` como `temporal_position`, manter `end` ausente, preservar score, método e configuração efetiva. |
| `no_match` | 0 | Não criar occurrence. `diagnostic_best_timestamp_seconds` e `diagnostic_best_score`, quando presentes, permanecem diagnóstico de candidato rejeitado e nunca são promovidos a occurrence. |
| `invalid_input` | 0 | Não criar occurrence e não reclassificar a violação de pré-condição como `no_match`. |
| `processing_failure` | 0 | Não criar occurrence e não converter a falha em uma coleção vazia que pareça processamento bem-sucedido. |

Portanto, a cardinalidade observável do método histórico é `0..1` occurrence por
cue por invocação. Se o source contiver duas ou mais aparições reais da mesma
cue, somente o best candidate selecionado internamente pode tornar-se uma
occurrence. Os demais lags não são resultados candidatos expostos e não podem
ser recuperados, agrupados, deduplicados ou suprimidos por esta política.

Esta é uma política de **seleção do best candidate existente**, não uma nova
camada de ranking:

- nenhuma segunda comparação de scores é executada após `match_cue`;
- nenhuma occurrence é agrupada com outra;
- nenhuma janela de distância temporal é definida;
- nenhuma non-maximum suppression é aplicada;
- nenhuma confidence ou probabilidade é calculada;
- nenhuma chamada adicional ao matcher é feita para procurar ocorrências
  subsequentes.

A seleção é o default conservador porque é a única saída observável do
matcher validado. A política deriva do limite documentado da implementação e
da ausência documentada de evidência multi-occurrence, não de uma alegação de
que M2 tenha exercitado múltiplas ocorrências.

## 5. Separação entre Cues e entre Resultados

Multiplicity é avaliada por `cue_id`. Occurrences de cues diferentes não são
agrupadas ou deduplicadas entre si, mesmo quando suas posições temporais se
sobrepõem. A orquestração de M3-03 preserva a atribuição estrutural
`cue_id -> MatchResult`; este contrato não altera essa API.

`no_match`, `invalid_input` e `processing_failure` não são occurrences. A
forma final de representar esses resultados por cue no Analysis Result é
responsabilidade de M3-05/M3-06, mas ela deve preservar as distinções do
matcher e não pode usar uma lista vazia isolada para esconder uma falha.

## 6. Compatibilidade e Evolução

Este contrato mantém duas camadas deliberadamente distintas:

- **modelo conceitual:** uma cue pode ter `0..N` occurrences;
- **produtor histórico:** `normalized_cross_correlation_v1` produz `0..1`;
- **produtor atual para Analyses novas:**
  `normalized_cross_correlation_multi_v1` produz `0..100` occurrences
  selecionadas por chamada.

M3-06 serializa uma coleção de occurrences e preserva todos os itens emitidos
pelo produtor. Não deve reduzir o
contrato a um único campo opcional que impeça multiplicidade futura.

S0009 realizou essa expansão de modo aditivo, mantendo a interface histórica
e acrescentando:

- uma interface que exponha múltiplos candidatos sem substituir silenciosamente
  o contrato atual;
- fixtures contendo ocorrências repetidas, ausentes, próximas e sobrepostas;
- evidência que justifique qualquer distância de agrupamento, deduplicação ou
  supressão;
- regra de empate e ordenação determinística;
- política de aceitação aplicada por candidato;
- declaração method-specific de quando `end` pode ser produzido;
- revisão versionada do impacto sobre M3-03 e M3-06.

Uma evolução desse tipo não pode alterar silenciosamente a canonicalização de
M1, a política de aceitação/no-match de M2 ou transformar score em confidence.

## 7. Invariantes e Fora de Escopo

São invariantes deste contrato:

- toda occurrence possui `cue_id`, `temporal_position`, score e método;
- `temporal_position` é obrigatório e `end` é opcional;
- `end` ausente não é erro e não implica duração zero;
- um diagnóstico de `no_match` nunca é uma occurrence;
- uma falha nunca é convertida silenciosamente em zero occurrences;
- o método e a configuração efetiva permanecem rastreáveis;
- o Core permanece independente de NumPy, FastAPI, Pydantic-como-transporte,
  SQLite, filesystem e FFmpeg.

Permanecem fora de escopo:

- modificar `baseline.py`, `acceptance.py`, a canonicalização de M1 ou a
  orquestração multi-cue de M3-03;
- criar um matcher multi-candidate, algoritmo de agrupamento, deduplicação ou
  supressão;
- calibrar confidence, criar thresholds por cue ou revisar o threshold de M2;
- definir o schema ou a serialização versionada do Analysis Result;
- persistência, REST API, WebUI, execução assíncrona ou deployment.

## 8. Matriz de Aceite de M3-04

| Critério | Decisão documentada |
|---|---|
| Posição obrigatória e `end` opcional/method-dependent | Seção 1 fixa `temporal_position` obrigatório, `end` opcional e ausente para o método atual; nenhuma duração é inventada. |
| Política baseada no comportamento de M2 | Seções 3 e 4 registram que M2 não observou multiplicidade e adotam somente o best candidate que o matcher já expõe, sem fabricar derivação empírica. |
| Métodos suportados explícitos | Seção 2 limita a política a `normalized_cross_correlation_v1`. |
| Sem novo método ou confidence | Seções 4 e 7 proíbem nova busca, ranking, agrupamento, supressão e calibração. |
| Contrato estável para M3-03 e M3-06 | Seções 5 e 6 preservam `cue_id -> MatchResult` e fixam coleção conceitual `0..N` com produtor atual `0..1`. |

## Capacidade vigente após S0009

- `normalized_cross_correlation_v1`: `0..1` occurrence por cue.
- `normalized_cross_correlation_multi_v1`: `0..100` occurrences selecionadas
  por cue; `CueOccurrences` continua conceitualmente `1..N` e nunca vazio.

No método multi, cada lag representa `[lag, lag + cue_length)`. Seleção ocorre
por score descendente, com lag anterior em empate; sobreposições com janelas
já selecionadas são suprimidas, intervalos apenas encostados são distintos, e
o resultado é reordenado cronologicamente. Por definição dessa versão, duas
occurrences físicas cujas janelas completas se sobrepõem não podem ser ambas
emitidas. Essa limitação não é generalizada para métodos futuros. `end`
permanece ausente e deduplicação nunca cruza `cue_id`.

## Referências

- `docs/architecture.md` — `Occurrence`, Score vs Decision, falha explícita
  e fluxo de matching.
- `docs/milestones.md` — escopo, riscos e continuidade de M3.
- `docs/analysis-core-contracts.md` — campos conceituais de `Occurrence` e
  mapeamento de `MatchResult.timestamp_seconds` para `temporal_position`.
- `docs/matching-contract.md` — origem/unidade do timestamp, score e quatro
  alternativas de resultado.
- `docs/matching-regression.md` — fixtures determinísticas M2-03 e limite de
  precisão do baseline.
- `docs/matching-robustness.md` — fixtures de robustez M2-04 e limitações do
  corpus.
- `docs/matching-acceptance.md` — política M2-05 e escopo explícito de
  `normalized_cross_correlation_v1`.
- `src/audio_cue_locator/infrastructure/acoustic_matching/baseline.py` —
  seleção de um best candidate e forma de `MatchResult`.
- `src/audio_cue_locator/infrastructure/acoustic_matching/acceptance.py` —
  configuração efetiva baseada na evidência de M2.
- `src/audio_cue_locator/application/multi_cue_orchestration.py` — uma
  chamada a `match_cue` e um `MatchResult` por `cue_id`.
