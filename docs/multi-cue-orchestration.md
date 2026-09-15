# Multi-Cue Orchestration

## Finalidade

Este documento descreve `src/audio_cue_locator/application/multi_cue_orchestration.py`
(M3-03): a orquestração em Application que permite a uma `Analysis`
coordenar mais de uma `Cue`, reutilizando o matcher single-cue já validado
do M2 (`match_cue`, `src/audio_cue_locator/infrastructure/acoustic_matching/baseline.py`)
uma vez por cue, sem introduzir uma segunda pipeline de matching paralela ou
duplicada (issue formal M3-03, risco de arquitetura de severidade alta).

M2 validou apenas localizar uma única cue em um áudio de origem. O
objetivo do M3 exige que uma Analysis processe mais de uma Cue e produza um
conjunto coerente de resultados por cue; Application não possuía, até esta
issue, nenhuma lógica de orquestração que executasse o matcher do M2 uma vez
por cue e agregasse os resultados. Este documento e o módulo que ele
descreve resolvem exatamente essa lacuna, e nenhuma outra.

## Posição no Fluxo

```text
Infrastructure — Media Processing (M1-03)
  CANONICAL_AUDIO_SPEC -> source/cues já canonicalizados
      |
      v
Application (este documento)
  run_multi_cue_analysis(source, cues, configuration)
      |
      | match_cue exatamente uma vez por cue, mesma configuration
      v
Infrastructure — Acoustic Matching (M2-02)
  MatchResult
      |
      | projeção M3-05, sem recalcular matching/aceitação
      v
Application -> cue_id -> CueOccurrences | CueNoMatch | CueFailure
```

`configuration` é o `EffectiveConfigurationSnapshot.matching` já anexado à
Analysis pelo M3-02 (`docs/analysis-effective-configuration.md`, seção 2);
este módulo não deriva, não recalcula e não substitui esse valor por um
default de módulo (`baseline.DEFAULT_CONFIGURATION` ou
`acceptance.EVIDENCE_BASED_CONFIGURATION`) -- apenas o encaminha, inalterado,
para cada chamada de `match_cue`.

## 1. Forma da API (Decisão G1)

`intents/M3/M3-03/implementation-handoff.json` deixou deliberadamente em
aberto se a orquestração seria uma função única ou uma classe com método
explícito (G1). Este módulo adota uma função única,
`run_multi_cue_analysis(source, cues, configuration) -> dict[str, PerCueOutcome]`,
pelos seguintes motivos:

- `src/audio_cue_locator/core/` ainda não define nenhum tipo Python
  concreto para `Analysis` ou `Cue` -- `docs/analysis-core-contracts.md`
  (M3-01) e `docs/analysis-effective-configuration.md` (M3-02) fixam esses
  contratos apenas no nível conceitual/documental. Exigir que quem chama
  primeiro construa um objeto `Analysis` que este código-fonte ainda não
  define adicionaria acoplamento a uma forma que não existe.
- Os três parâmetros (`source`, `cues`, `configuration`) correspondem
  exatamente aos três dados que uma Analysis já carrega
  conceitualmente para este propósito: o áudio de origem canonicalizado
  (`Analysis.source_asset`), suas cues (`Analysis.cues`), e a configuração
  de matching efetiva (`Analysis.effective_configuration.matching`).
- Uma função única, sem estado retido entre chamadas, é suficiente: nada
  neste módulo precisa persistir entre execuções (fora de escopo desta
  issue) ou variar por instância além dos próprios parâmetros de entrada.

## 2. Entrada: `cues` como `cue_id -> áudio já canonicalizado`

`cues` é um mapeamento (`Mapping[str, numpy.ndarray]`) de `cue_id` para o
áudio já canonicalizado daquela cue (`Cue.asset_reference`,
`docs/analysis-core-contracts.md`, seção 2). Usar um mapeamento, em vez de
uma sequência posicional, faz da atribuição por `cue_id` uma propriedade
estrutural: o resultado é construído iterando os próprios itens desse
mapeamento, então não existe um passo posicional ou indexado separado em
que uma cue possa ser atribuída ao resultado errado (issue formal, critério
de aceite 2; estado operacional, risco de validação "cues são processadas
mas seus resultados não são corretamente atribuídos à cue de origem").

Um mapeamento vazio levanta `ValueError`: `docs/analysis-core-contracts.md`
(seção 1) já define `cues` como uma sequência não vazia, então uma Analysis
sem nenhuma cue é uma violação de pré-condição de quem chama, não um caso
válido de resultado vazio.

## 3. Saída: `dict[str, PerCueOutcome]` (M3-05)

`docs/analysis-core-contracts.md`, seção 3.1, é a fonte normativa da união
fechada por-cue. Este módulo contém sua projeção executável imutável:

- `CueOccurrences(kind="occurrences", occurrences=(...))` para `FOUND`;
- `CueNoMatch(kind="no_match")` para `NO_MATCH`;
- `CueFailure(kind="failure", category=..., message=...)` para
  `INVALID_INPUT` e `PROCESSING_FAILURE`.

As três formas não se sobrepõem. `CueOccurrences` valida no construtor que
sua coleção tem pelo menos um item; zero occurrences não representa
no-match nem falha. A cardinalidade é específica do método: `0..1` para o histórico e
`0..100` para o produtor multi-occurrence, embora o contrato aceite
`1..N` na variante matched para evolução futura.

O mapeamento de Infrastructure para Core é explícito:

| Resultado M2 | Variante M3-05 | Observação |
|---|---|---|
| `FOUND` | `CueOccurrences` | Copia timestamp, score e método; `end` permanece ausente para o método atual. |
| `NO_MATCH` | `CueNoMatch` | Diagnósticos do candidato rejeitado não são promovidos a occurrence. |
| `INVALID_INPUT` | `CueFailure(invalid_input)` | Depois da validação única do source, representa entrada inválida da cue. |
| `PROCESSING_FAILURE` | `CueFailure(matching_failure)` | Usa mensagem segura e não propaga detalhe bruto da exceção. |

Assim, o enum `MatchOutcome` continua sendo detalhe de Infrastructure.
Application o converte, mas não muda o resultado do matcher nem reaplica o
threshold.

### 3.1. Ownership de falhas compartilhadas e por-cue

O source compartilhado é validado uma vez antes do loop. Entrada inválida
nesse source levanta `AnalysisSourceInputError(category=invalid_input)` e
não é duplicada como uma falha para cada cue.

Falhas de mídia, decode ou canonicalização que ocorram antes desta função
receber arrays já canônicos pertencem ao asset que falhou: source
compartilhado no escopo da Analysis; asset isolado de cue no escopo daquela
cue. Falha de matching retornada por uma invocação de `match_cue` pertence à
cue correspondente. Persistência nunca é outcome por-cue. Limitação de
recursos e falha interna só são por-cue quando isoladas e atribuíveis; caso
contrário permanecem no escopo da Analysis.

Esta regra define ownership, não agregação: decidir como uma falha por-cue
afeta `Analysis.state` ou o documento externo é responsabilidade de M3-06.

## 4. Configuração Lida Uma Vez por Analysis (Decisão G7)

`configuration` é um único parâmetro para toda a chamada de
`run_multi_cue_analysis`, lido uma vez por quem chama a partir do
`effective_configuration.matching` da própria Analysis e repassado
inalterado para cada cue. `intents/M3/M3-03/implementation-handoff.json`
deixou em aberto (G7) se esse valor deveria ser lido uma vez por Analysis
ou poderia, em princípio, variar por cue; esta issue resolve isso lendo-o
uma única vez, por dois motivos:

- `docs/analysis-effective-configuration.md` (seção 4, "Captura no Ponto de
  Uso") já estabelece que o snapshot de configuração efetiva é um valor
  capturado e anexado à Analysis no ponto de uso, não uma leitura ao vivo
  repetida; ler `configuration` uma vez por Analysis e reutilizá-la para
  toda cue estende esse mesmo princípio da anexação para o consumo.
- Nada na issue formal ou no estado operacional exige, ou apresenta um
  caso de uso concreto para, configurações diferentes por cue dentro da
  mesma Analysis; introduzir essa variação sem uma exigência real violaria
  o princípio de não adicionar escopo além do estritamente necessário.

## 5. Verificação em Exemplos Controlados

`tests/test_multi_cue_orchestration.py` cobre, sobre exemplos sintéticos
controlados (sem mídia representativa ao vivo):

- uma Analysis com mais de uma cue produzindo um conjunto coerente de
  variantes explícitas por cue;
- atribuição por `cue_id` correta independentemente da ordem de entrada das
  cues;
- uma operação Infrastructure invocada exatamente uma vez por cue:
  `match_cue` para v1 ou `match_cue_occurrences` para multi_v1;
- a `EffectiveConfiguration` efetivamente usada em cada chamada é a que foi
  explicitamente fornecida à orquestração, nunca `DEFAULT_CONFIGURATION` ou
  `EVIDENCE_BASED_CONFIGURATION` lida como default pelo próprio módulo
  (risco de execução de severidade alta do estado operacional);
- nenhum outcome por cue é descartado silenciosamente, incluindo
  `INVALID_INPUT`;
- uma Analysis mista preserva simultaneamente `CueNoMatch` para uma cue e
  `CueFailure(matching_failure)` para outra, sem carregar detalhe bruto;
- `CueOccurrences` rejeita uma coleção vazia;
- source compartilhado inválido é rejeitado uma vez antes de qualquer
  chamada ao matcher, em vez de ser copiado para todas as cues;
- um mapeamento de cues vazio levanta `ValueError` em vez de produzir um
  resultado vazio silencioso;
- o caso degenerado de uma única cue permanece uma Analysis válida.

## 6. Fora de Escopo Deste Módulo

- Qualquer novo algoritmo de matching, scoring, correlação ou política de
  aceitação fora de `baseline.py`/`acceptance.py`; nenhuma segunda pipeline
  de matching, paralela ou duplicada, é introduzida.
- Execução paralela ou assíncrona de cues; `run_multi_cue_analysis` processa
  as cues sequencialmente, sem concorrência.
- Qualquer política de seleção/deduplicação diferente da semântica fixa de
  `normalized_cross_correlation_multi_v1`.
- Agregação das variantes por-cue em lifecycle final ou no Analysis Result
  versionado (M3-06).
- API REST, WebUI, ou persistência de estado de orquestração.
- O schema versionado de Analysis Result e sua serialização (M3-06).
- Canonicalização de áudio: `source` e cada cue em `cues` devem já
  satisfazer `CANONICAL_AUDIO_SPEC` antes de chegar a este módulo, tal como
  já exigido por `match_cue` (`docs/matching-contract.md`, seção 1).

## Roteamento por método após S0009

A configuração persistida decide uma única operação de Infrastructure por
cue: `normalized_cross_correlation_v1` chama `match_cue`, e
`normalized_cross_correlation_multi_v1` chama `match_cue_occurrences`. Não há
loop de remover resultado e buscar novamente, nem correlação na Application.
Todos os candidatos aceitos do novo método viram `Occurrence`; quando existe
uma janela S0008, o mesmo offset alinhado à grade de samples é aplicado a cada
item antes da publicação.

## Referências

- `docs/analysis-core-contracts.md` -- contratos de `Analysis`, `Cue` e
  `Occurrence` (M3-01) e contrato normativo `PerCueOutcome` (M3-05),
  incluindo a união fechada, categorias de falha e ownership.
- `docs/analysis-effective-configuration.md` -- `EffectiveConfigurationSnapshot`
  e o princípio de captura no ponto de uso (M3-02) que a decisão G7 acima
  estende ao consumo da configuração.
- `docs/matching-contract.md` -- semântica de score, timestamp e das quatro
  alternativas de `MatchOutcome` que `match_cue` já fixa (M2-01).
- `src/audio_cue_locator/infrastructure/acoustic_matching/baseline.py` --
  `match_cue`, `EffectiveConfiguration`, `MatchResult`, `MatchOutcome`
  (M2-02), reutilizados sem modificação por este módulo.
- `docs/occurrence-temporal-semantics-and-policy.md` -- coleção conceitual
  `0..N`, produtor atual `0..1` e `end` ausente para o método atual.
- `intents/M3/M3-03/implementation-handoff.json` -- escopo autorizado,
  decisões já fixadas (reutilização do matcher, leitura de configuração a
  partir da Analysis, agregação por `cue_id`) e lacunas G1/G2/G7 que este
  documento resolve.
