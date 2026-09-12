# Analysis Core Contracts

## Finalidade

Este documento define o contrato interno do Core para `Analysis`, `Cue` e
`Occurrence` (M3-01): a forma conceitual de cada tipo, os estados de
lifecycle de `Analysis`, e a fronteira que os mantém independentes de
Infrastructure. Ele existe para que multi-cue orchestration (M3-03),
occurrence/multiple-occurrence policy (M3-04), effective-configuration
traceability (M3-02), a distinção por-cue entre no-match e falha (M3-05) e
o Analysis Result versionado com sua serialização (M3-06) possam depender
de um vocabulário estável em vez de re-derivar a estrutura de forma
independente (docs/milestones.md, M3 — Dependências).

Este documento não implementa persistência, scheduling, cancelamento,
orquestração multi-cue, política de múltiplas occurrences, o schema
versionado de Analysis Result, API REST, WebUI ou execução assíncrona.
Também não modifica ou reimplementa o matcher single-cue existente
(`src/audio_cue_locator/infrastructure/acoustic_matching/baseline.py`,
M2-02) nem a política de aceitação baseada em evidência
(`src/audio_cue_locator/infrastructure/acoustic_matching/acceptance.py`,
M2-05); ambos são reutilizados por cue, tal como já resolvido pelo
`intents/M3/M3-01/implementation-handoff.json`, sem uma segunda pipeline de
matching paralela ou duplicada.

## Posição no Fluxo

```text
Core (este documento)
  Analysis --- cues: list[Cue]
  Analysis --- result_or_reference: list[Occurrence] | ErrorInfo | None
      |
      | reused per cue, unmodified (M3-01 constraint)
      v
Infrastructure — Acoustic Matching (M2-02, M2-05)
  match_cue(source, cue, configuration) -> MatchResult
      |
      | consumes canonicalized audio (M1 contract, unchanged)
      v
Infrastructure — Media Processing (M1-03)
  CANONICAL_AUDIO_SPEC (sample_rate_hz, channels, sample_format, normalization)
```

Core não conhece `MatchResult`, `EffectiveConfiguration`,
`MatchOutcome` ou `CanonicalAudioSpec` como tipos importados — essas são
formas de Infrastructure (docs/architecture.md, Princípio 1: "O Core não
conhece interfaces externas"). A seção 6 declara, em vez disso, uma
compatibilidade estrutural explícita: os campos de `Analysis` e
`Occurrence` abaixo devem ser populáveis a partir dessas formas por uma
camada de conversão fora do Core (Application, em uma issue futura), sem
que o Core precise importar `numpy`, `FFmpeg`, SQLite, filesystem, FastAPI
ou Pydantic-como-transporte para isso.

## 1. `Analysis`

Representa uma solicitação de localização acústica (docs/architecture.md,
"Analysis"). Campos conceituais:

| Campo | Significado |
|---|---|
| `analysis_id` | Identificador estável da Analysis. |
| `state` | Um dos quatro estados de lifecycle definidos na seção 4. |
| `source_asset` | Referência ao asset de origem já canonicalizado (M1); não é o áudio decodificado bruto, nem um caminho de filesystem, nem um objeto de storage. |
| `cues` | Sequência não vazia de `Cue` (seção 2) associadas a esta Analysis. |
| `effective_configuration` | O método de matching e seus parâmetros efetivamente usados para esta Analysis (ex.: identificador do método e threshold de aceitação), sem fixar sua forma numérica interna — rastreabilidade de configuração completa é escopo de M3-02. |
| `lifecycle_timestamps` | Marcações de quando a Analysis entrou em cada estado observado (por exemplo, enfileiramento e conclusão); não implica scheduling nem persistência. |
| `method_or_configuration` | Identificador do método de matching usado (ex.: `"normalized_cross_correlation_v1"`), mantido junto de `effective_configuration` para permitir rastrear qual matcher produziu o resultado. |
| `result_or_reference` | O resultado da Analysis quando `SUCCEEDED` — uma coleção de `Occurrence` (seção 3), uma por cue processada com sucesso — ou uma referência a esse resultado quando ele não é mantido inline; nunca ambos ausentes quando `state == SUCCEEDED`. |
| `structured_error` | Erro estruturado (não uma string livre) quando `state == FAILED`; ausente nos demais estados. |

`Analysis` não define como `source_asset`, `effective_configuration` ou
`structured_error` são serializados ou persistidos — isso é explicitamente
excluído deste documento (ver "Fora de Escopo", seção 7) e permanece
prerrogativa de M3-06 (Analysis Result versionado) e de uma futura camada
de persistência (fora de M3).

## 2. `Cue`

Representa uma referência acústica fornecida para comparação
(docs/architecture.md, "Cue"). Campos conceituais:

| Campo | Significado |
|---|---|
| `cue_id` | Identidade da Cue, única dentro da Analysis a que pertence (não globalmente única por si só). |
| `asset_reference` | Referência ao asset da cue, na mesma forma canonicalizada usada por `Analysis.source_asset`. |
| `presentation_metadata` | Metadata opcional destinada apenas à apresentação (ex.: um rótulo legível ou uma cor de destaque em UI futura). |

**Separação estrutural obrigatória:** `presentation_metadata` não é lida por
nenhuma lógica de matching ou de resultado. Isto não é uma convenção de
nome ou um comentário — é uma separação estrutural: nenhum campo usado
para calcular ou interpretar um `Occurrence` (seção 3) pode ser derivado de
`presentation_metadata`, e `presentation_metadata` pode estar totalmente
ausente sem afetar a capacidade de uma `Cue` ser processada pelo matcher.
Isso resolve diretamente o risco arquitetural registrado por
`states/M3/M3-01/issue-operational-state.json` (risks[1]): metadata de
apresentação vazando para significado de matching/resultado.

## 3. `Occurrence`

Representa uma localização produzida pelo matcher para uma cue específica
(docs/architecture.md, "Occurrence"). Campos conceituais:

| Campo | Significado |
|---|---|
| `cue_id` | Identifica qual `Cue` desta Analysis produziu esta Occurrence. |
| `temporal_position` | Posição temporal do candidato encontrado, na mesma convenção de origem/unidade já fixada por `docs/matching-contract.md` (seção 3): origem 0 do source canonicalizado, segundos não negativos, limitado pela duração do source. A presença e semântica de um `end` temporal permanecem indefinidas, tal como já registrado por `docs/architecture.md` ("Occurrence"); este documento não inventa essa precisão. |
| `score` | Valor de similaridade específico do método usado — nunca confidence calibrada (docs/architecture.md, Princípio 5; `docs/matching-contract.md`, seção 4). Este documento herda essa política; não a redefine. |
| `matching_method` | Identificador estável do método que produziu esta Occurrence (ex.: `"normalized_cross_correlation_v1"`), correspondente a `effective_configuration` da Analysis correspondente. |

`Occurrence` representa apenas o caso de match encontrado (`found`); os
casos `no_match`, `invalid_input` e `processing_failure` do matcher de
cue única (M2-02) não produzem uma `Occurrence` para aquela cue — a
distinção por-cue entre "sem match" e "falha de processamento" no nível de
`Analysis.result_or_reference`/`structured_error` é escopo de M3-05, não
deste documento. Múltiplas `Occurrence` por cue (política de múltiplas
occurrences) são escopo de M3-04.

## 4. Estados de Lifecycle da `Analysis`

Os quatro estados abaixo são documentados apenas no nível conceitual —
sem semântica de persistência, scheduling ou cancelamento (formal issue
M3-01, critério de aceite 2; `docs/architecture.md`, "Analysis"):

```text
QUEUED -> RUNNING -> SUCCEEDED
                  \-> FAILED
```

- **`QUEUED`**: a Analysis foi aceita conceitualmente e aguarda
  processamento; nenhuma cue foi ainda avaliada pelo matcher.
- **`RUNNING`**: o processamento por cue está em andamento; `cues` é
  imutável neste ponto, mas `result_or_reference` ainda não está completo.
- **`SUCCEEDED`**: todas as cues foram processadas e
  `result_or_reference` está presente (uma `Occurrence`, ou sua ausência
  explícita por cue, dependendo da política de M3-04/M3-05 que este
  documento não define). `structured_error` está ausente.
- **`FAILED`**: o processamento não pôde concluir de forma válida;
  `structured_error` está presente e descreve a falha.

Não existe estado de cancelamento explícito neste contrato inicial
(docs/architecture.md, "Analysis": "Cancelamento não faz parte do
contrato inicial."); este documento preserva essa decisão em vez de
introduzir um quinto estado. Scheduling (fila real, workers, timeouts) e
persistência de estado entre reinícios do processo pertencem a uma fase
posterior a M3 (docs/architecture.md, Princípio 9: "Assíncrono não
significa distribuído").

## 5. Fronteira do Core (Independência de Infrastructure)

Nenhum dos três tipos acima — `Analysis`, `Cue`, `Occurrence` — importa ou
requer, direta ou indiretamente:

- FastAPI ou qualquer framework HTTP;
- Pydantic usado como schema de transporte;
- SQLite ou qualquer driver de banco de dados;
- caminhos de filesystem como identidade de asset;
- FFmpeg ou tipos de decodificação de mídia;
- `numpy` ou qualquer biblioteca numérica.

Isto satisfaz diretamente o critério de aceite 4 da issue formal e o
Princípio 1 de `docs/architecture.md` ("O Core não conhece interfaces
externas"). A seção 6 declara compatibilidade estrutural com os tipos que
Infrastructure já possui, sem que o Core precise depender deles.

## 6. Compatibilidade com M2 (Matching) e M1 (Canonical Audio)

Esta seção não redefine nenhum contrato existente; ela declara, para
rastreabilidade, como os campos das seções 1–3 se relacionam com as formas
já validadas de Infrastructure, confirmadas por inspeção direta do código
nesta issue:

| Campo do Core | Forma de Infrastructure correspondente | Origem |
|---|---|---|
| `Occurrence.score` | `MatchResult.score` | `baseline.py` (M2-02) |
| `Occurrence.matching_method` | `EffectiveConfiguration.method` | `baseline.py` (M2-02) |
| `Occurrence.temporal_position` | `MatchResult.timestamp_seconds` | `baseline.py` (M2-02) |
| `Analysis.method_or_configuration` | `EffectiveConfiguration` (`method`, `acceptance_threshold`) | `baseline.py` (M2-02), reutilizado sem modificação por `acceptance.py` (M2-05) |
| `Analysis.structured_error` (quando aplicável por cue) | `MatchResult.reason`, presente para `invalid_input` e `processing_failure` | `baseline.py` (M2-02) |
| `Analysis.source_asset` / `Cue.asset_reference` | Áudio já em conformidade com `CanonicalAudioSpec` (`sample_rate_hz=48000`, `channels=1`, `sample_format="float32"`, normalização de pico) | `canonical_audio.py` (M1-03) |

O mapeamento explícito acima é o que permite a M3-02 através de M3-06
depender destes três contratos sem re-inspecionar `baseline.py`,
`acceptance.py` ou `canonical_audio.py` para descobrir a forma correta —
mas a conversão efetiva entre as formas de Infrastructure e estas de Core
(por exemplo, agregar múltiplos `MatchResult` em `Analysis.result_or_reference`)
não é implementada por este documento; ela pertence a uma camada de
Application futura, fora do escopo de M3-01.

`MatchOutcome.FOUND`, `NO_MATCH`, `INVALID_INPUT` e `PROCESSING_FAILURE`
(`baseline.py`) permanecem exclusivos de Infrastructure; este documento não
os importa nem os redefine no Core.

## 7. Fora de Escopo Deste Documento

- Persistência de `Analysis`, `Cue` ou `Occurrence` em SQLite ou qualquer
  outro armazenamento.
- Lógica de orquestração multi-cue e política de seleção/deduplicação de
  múltiplas occurrences (M3-03, M3-04).
- O schema versionado de Analysis Result e sua serialização JSON (M3-06).
- Rastreabilidade completa de configuração efetiva além de nomear o campo
  (M3-02).
- Distinção detalhada por-cue entre no-match e falha de processamento além
  do que `structured_error`/`MatchResult.reason` já expõem (M3-05).
- API REST, WebUI, autenticação ou execução assíncrona.
- Qualquer implementação numérica de matching, calibração de threshold, ou
  uma pipeline de matching paralela/duplicada ao matcher single-cue
  existente.
- Modificação de `baseline.py`, `acceptance.py` ou `canonical_audio.py`;
  todos os três permanecem inalterados por este documento.

## 8. Lacunas e Decisões Pendentes

- **G1 (forma exata dos campos):** os nomes de campo usados nas seções
  1–3 (`analysis_id`, `source_asset`, `effective_configuration`,
  `method_or_configuration`, `result_or_reference`, `structured_error`,
  `cue_id`, `asset_reference`, `presentation_metadata`,
  `temporal_position`, `matching_method`) são a decisão de nomenclatura
  deste documento, derivada diretamente das listas de campos da issue
  formal (seção 4) e da compatibilidade declarada na seção 6 acima; eles
  ainda não foram implementados como tipos Python e podem ser ajustados
  por revisão explícita antes de M3-02 depender deles.
- **G2 (local do documento):** confirmado neste handoff — nenhum
  documento equivalente já existente em `docs/` cobria este escopo;
  `docs/analysis-core-contracts.md` é a localização definitiva.
- **G3 (pontos de reuso do M2/M1):** resolvido pela seção 6 acima, por
  inspeção direta de `baseline.py`, `acceptance.py` e `canonical_audio.py`.
- **G4 (contratos de handoff ausentes):** `reference-library/contracts/ai-assistive-task-package-contract.md`
  e `reference-library/contracts/implementation-handoff-contract.md`
  permanecem ausentes na reference-library do repositório alvo; nenhum
  dos dois é gerado ou pressuposto por este documento.
- Este documento não substitui revisão de fronteira explícita antes que
  M3-02 até M3-06 dependam destes contratos (ver seção 5 e o risco de alto
  severity registrado por `states/M3/M3-01/issue-operational-state.json`).

## Referências

- `docs/architecture.md` — "Core", "Analysis", "Cue", "Occurrence",
  "Analysis Result", "Canonical Audio", Princípios e Restrições 1 e 5.
- `docs/milestones.md` — M3, Escopo Núcleo e Dependências (M3 depende de
  M1 e M2 concluídas; M3-02 a M3-06 bloqueadas por M3-01 conforme
  `issues/M3/M3-01/formal-issue.json`, seção 12).
- `docs/matching-contract.md` — semântica de score, timestamp e
  alternativas de resultado do matcher single-cue.
- `src/audio_cue_locator/infrastructure/acoustic_matching/baseline.py` —
  `MatchOutcome`, `EffectiveConfiguration`, `MatchResult` (M2-02).
- `src/audio_cue_locator/infrastructure/acoustic_matching/acceptance.py` —
  `EVIDENCE_BASED_CONFIGURATION` (M2-05).
- `src/audio_cue_locator/infrastructure/media_processing/canonical_audio.py` —
  `CanonicalAudioSpec`, `CANONICAL_AUDIO_SPEC` (M1-03).
- `intents/M3/M3-01/implementation-handoff.json` — escopo autorizado desta
  issue e decisões de handoff (G1–G5).
