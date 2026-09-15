# Analysis Result Schema

## Finalidade

Este documento define o schema versionado do Analysis Result e as regras
de serialização JSON canônica que `src/audio_cue_locator/core/
analysis_result.py` implementa (M3-06). Ele dá ao projeto uma única
representação JSON exportável, autoritativa e versionada de um resultado
de Analysis, independente de qualquer schema futuro de resposta REST ou de
persistência, para que M3-07 e milestones posteriores a adotem em vez de
redefini-la (`issues/M3/M3-06/formal-issue.json`).

Este documento não implementa persistência, API REST, WebUI, execução
assíncrona, nem a montagem de um `AnalysisResult` a partir de uma
`Analysis` completa em execução -- essa integração fim-a-fim permanece
trabalho de uma issue futura. Também não modifica o matcher single-cue
existente, sua política de aceitação, a canonicalização M1, ou a
orquestração multi-cue existente
(`src/audio_cue_locator/application/multi_cue_orchestration.py`); todos
permanecem inalterados e são apenas referenciados por compatibilidade
estrutural.

## Posição no Fluxo

```text
Core (docs/analysis-core-contracts.md, M3-01/M3-05)
  Analysis --- cues: list[Cue]
  Analysis --- per_cue_outcomes: map[cue_id, PerCueOutcome]
      |
      | valores já produzidos, consumidos por valor (não por wiring automático)
      v
Core (este documento, M3-06)
  AnalysisResult
    schema_version, analysis_id, final_state (derivado), method,
    configuration: EffectiveConfigurationSnapshot,
    cues: tuple[CueResult, ...],
    structured_error: StructuredError | None
      |
      | serialize_analysis_result()
      v
JSON canônico determinístico
```

`AnalysisResult` não importa `multi_cue_orchestration.py` (Application),
`baseline.py` ou `acceptance.py` (Infrastructure). Ele declara seus
próprios tipos Core -- `Occurrence`, `CueOccurrences`, `CueNoMatch`,
`CueFailure`, `FailureCategory`, `EffectiveConfigurationSnapshot` --
estruturalmente equivalentes aos já documentados por
`docs/analysis-core-contracts.md` e `docs/analysis-effective-
configuration.md`, porque Application depende de Core e não o inverso
(`docs/architecture.md`, Princípio 1). Converter uma saída já produzida
por `run_multi_cue_analysis` (Application) para estes tipos Core é
trabalho de uma futura camada de integração, não deste documento.

## 1. Campos do `AnalysisResult`

| Campo | Tipo | Obrigatório | Significado |
|---|---|---|---|
| `schema_version` | `str` | Sim | Identificador de versão do schema. Valor inicial: `"analysis_result.v1"` (seção 6). |
| `analysis_id` | `str` não vazio | Sim | Identificador estável da Analysis, fornecido pelo chamador (seção 3). |
| `final_state` | `"completed"` \| `"failed"` | Sim | Derivado, nunca fornecido diretamente (seção 2). |
| `method` | `str` | Sim | Identificador do método de matching; deve ser igual a `configuration.matching.method` (seção 5). |
| `configuration` | `EffectiveConfigurationSnapshot` | Sim | Snapshot de configuração efetiva (seção 5). |
| `cues` | `tuple[CueResult, ...]` não vazia | Sim | Lista ordenada de `{cue_id, outcome}` (seção 4). |
| `structured_error` | `StructuredError \| null` | Sim (chave sempre presente) | Erro de escopo da Analysis; presente se e somente se `final_state == "failed"` (seção 2). |

## 2. `final_state`: vocabulário fechado e regra de derivação

`final_state` é um enum fechado de dois valores: `"completed"` e
`"failed"`. Ele não é um campo de entrada do construtor -- é uma
propriedade derivada:

```text
final_state = "failed"    se structured_error is not None
final_state = "completed" caso contrário
```

Esta regra espelha diretamente a distinção `SUCCEEDED`/`FAILED` já fixada
por `docs/analysis-core-contracts.md`, seção 4: `FAILED` significa que
`structured_error` está presente e descreve a falha; `SUCCEEDED` significa
que todas as cues processadas têm um outcome independente registrado.

**Uma `CueFailure` por-cue não torna a Analysis inteira `"failed"`.** A
seção 3.1 de `docs/analysis-core-contracts.md` mantém a falha por-cue
estruturalmente distinta da falha de escopo da Analysis; agregar uma falha
independente de uma cue ao `final_state` esconderia as outras cues com
occurrences ou no_match legítimos, violando o requisito de rastreabilidade
por-cue da issue formal. `structured_error` representa apenas uma falha
compartilhada, anterior ao processamento independente por cue (por
exemplo, uma falha de precondição do source compartilhado) -- exatamente o
caso já isolado por `AnalysisSourceInputError` em
`multi_cue_orchestration.py`.

## 3. `analysis_id`: propriedade e estabilidade

`analysis_id` é uma string opaca, não vazia, **fornecida pelo chamador**
(Application, em uma integração futura) e capturada uma única vez. Nem
`AnalysisResult` nem `serialize_analysis_result` geram, derivam, ou
normalizam esse valor -- ele é apenas validado como não-vazio e
repassado sem alteração.

Isso torna a determinism trivial por construção: serializar duas vezes o
mesmo valor de entrada (incluindo o mesmo `analysis_id`) produz sempre a
mesma saída, sem depender de nenhuma fonte de aleatoriedade, relógio, ou
biblioteca de geração de identidade (UUID ou similar) dentro do Core.

## 4. Ordem e forma de `cues`

`cues` é uma tupla ordenada de `CueResult { cue_id, outcome }`, na mesma
ordem em que as cues foram fornecidas à `AnalysisResult` -- normalmente a
mesma ordem em que `run_multi_cue_analysis` iterou o mapeamento de cues da
Analysis. `cue_id` deve ser único dentro de `cues`; duplicatas são
rejeitadas na construção.

`outcome` é a união fechada `CueOutcome = CueOccurrences | CueNoMatch |
CueFailure`, idêntica em forma e invariantes à união já normativa de
`docs/analysis-core-contracts.md`, seção 3.1:

| `kind` | Forma | Invariante |
|---|---|---|
| `"occurrences"` | `CueOccurrences(occurrences: tuple[Occurrence, ...])` | Uma ou mais occurrences; vazio é inválido. |
| `"no_match"` | `CueNoMatch()` | Conclusão legítima sem occurrence aceita. |
| `"failure"` | `CueFailure(category: FailureCategory, message: str)` | Falha técnica por-cue; `message` não vazia. |

`Occurrence` preserva exatamente os campos já fixados por `docs/analysis-
core-contracts.md`, seção 3, e `docs/occurrence-temporal-semantics-and-
policy.md`: `cue_id`, `temporal_position` (obrigatório), `score`,
`matching_method`, `end` (opcional, ausente para
`normalized_cross_correlation_v1`).

## 5. `configuration`: reuso do snapshot M3-02

`configuration` é o `EffectiveConfigurationSnapshot` já definido por
`docs/analysis-effective-configuration.md`, seção 2, reproduzido
verbatim, não redefinido:

```text
EffectiveConfigurationSnapshot
  canonicalization: CanonicalizationSnapshot
    sample_rate_hz, channels, sample_format,
    normalization: { enabled, method, target_peak_amplitude }
  matching: MatchingSnapshot
    method, acceptance_threshold
  configuration_source_name
```

O campo de nível superior `method` do `AnalysisResult` é obrigatoriamente
igual a `configuration.matching.method` -- validado no construtor, nunca
apenas presumido -- para que os dois nunca divirjam silenciosamente. Isso
satisfaz o requisito da issue formal de expor `method` como campo próprio
sem introduzir um segundo valor independente.

## 6. `schema_version`: valor inicial e regra de evolução

O valor inicial é o literal `"analysis_result.v1"`
(`SCHEMA_VERSION` em `analysis_result.py`), seguindo a convenção já
estabelecida neste projeto de `contract_version` no formato
`"<artifact_type>.v1"`.

Qualquer mudança retroativamente incompatível nos campos, semântica, ou
regras de serialização canônica fixadas por este documento exige um novo
valor explícito (por exemplo `"analysis_result.v2"`); redefinir o
significado de `"analysis_result.v1"` silenciosamente não é permitido
(critério de aceite 5 da issue formal).

## 7. Estrutura JSON serializada

```json
{
  "schema_version": "analysis_result.v1",
  "analysis_id": "analysis-0001",
  "final_state": "completed",
  "method": "normalized_cross_correlation_v1",
  "configuration": {
    "canonicalization": {
      "sample_rate_hz": 48000,
      "channels": 1,
      "sample_format": "float32",
      "normalization": {
        "enabled": true,
        "method": "peak",
        "target_peak_amplitude": 1.0
      }
    },
    "matching": {
      "method": "normalized_cross_correlation_v1",
      "acceptance_threshold": 0.7056698933077521
    },
    "configuration_source_name": "acoustic_matching.acceptance.EVIDENCE_BASED_CONFIGURATION"
  },
  "cues": [
    {
      "cue_id": "cue-found",
      "outcome": {
        "kind": "occurrences",
        "occurrences": [
          {
            "cue_id": "cue-found",
            "temporal_position": 1.5,
            "score": 0.912345,
            "matching_method": "normalized_cross_correlation_v1",
            "end": null
          }
        ],
        "failure": null
      }
    },
    {
      "cue_id": "cue-no-match",
      "outcome": { "kind": "no_match", "occurrences": null, "failure": null }
    },
    {
      "cue_id": "cue-failed",
      "outcome": {
        "kind": "failure",
        "occurrences": null,
        "failure": {
          "category": "matching_failure",
          "message": "single-cue matching failed while processing this cue"
        }
      }
    }
  ],
  "structured_error": null
}
```

## 8. Política de Serialização Canônica

`serialize_analysis_result` produz saída determinística por construção,
cobrindo as oito dimensões deixadas em aberto pelo estado operacional da
issue:

1. **Ordem das chaves de nível superior:** fixa e explícita --
   `schema_version`, `analysis_id`, `final_state`, `method`,
   `configuration`, `cues`, `structured_error` -- construída por literais
   de dicionário na ordem acima, nunca por `sort_keys` ou ordem de hash.
2. **Ordem de `cues`:** a mesma ordem de construção de `AnalysisResult.
   cues` (tupla), preservando a ordem original de iteração das cues da
   Analysis.
3. **Ordem de `occurrences`:** a mesma ordem já produzida pela
   Application (atualmente sempre exatamente um item).
4. **Campos opcionais/nulos:** toda chave estruturalmente definida está
   sempre presente em seu objeto; um valor estruturalmente ausente (por
   exemplo `Occurrence.end`, ou o par `occurrences`/`failure` não usado
   por um `CueOutcome`, ou `structured_error` quando `final_state` é
   `"completed"`) é serializado como `null`, nunca omitido.
5. **Precisão de ponto flutuante:** usa a codificação padrão do módulo
   `json` da biblioteca padrão (representação round-trip mais curta do
   Python), sem arredondamento ou truncamento adicional.
6. **Números não finitos:** proibidos -- `allow_nan=False` faz
   `serialize_analysis_result` levantar `ValueError` em vez de emitir
   `NaN`/`Infinity` (JSON inválido), em vez de propagar um valor não
   finito de score/timestamp.
7. **Codificação:** texto UTF-8 com `ensure_ascii=True`, de modo que a
   saída em bytes não dependa do locale da plataforma.
8. **Formatação:** separadores compactos (`","`, `":"`), sem espaço em
   branco incidental; o serializador não acrescenta nova linha final --
   isso é responsabilidade de quem grava o arquivo, não do formato
   canônico.

Igualdade byte-a-byte entre duas serializações da mesma entrada (por
valor, não por identidade de objeto) é o teste de aceite desta regra;
`tests/test_analysis_result_serialization.py` verifica isso diretamente.

## 9. Taxonomia de Erros Estruturados

Nenhuma categoria nova é introduzida. Tanto `CueFailure.category` quanto
`StructuredError.category` reutilizam exatamente os identificadores já
definidos pela arquitetura e já projetados por
`multi_cue_orchestration.py`: `invalid_input`, `unsupported_media`,
`decode_or_canonicalization_failure`, `matching_failure`,
`resource_limit`, `internal_failure`. `message` deve já chegar como texto
seguro e sanitizado -- nenhuma sanitização adicional é feita pelo
serializador, e nenhum detalhe de exceção bruta ou de host deve ser
repassado.

## 10. Fora de Escopo Deste Documento

- Persistência do `AnalysisResult` em SQLite ou qualquer outro
  armazenamento.
- O schema de resposta de uma futura API REST.
- Uma função de montagem que converta a saída de
  `run_multi_cue_analysis` (Application) nos tipos Core deste documento,
  ou que anexe um `AnalysisResult` ao lifecycle completo de uma
  `Analysis`.
- Qualquer implementação numérica de matching, calibração de threshold, ou
  mudança à política de aceitação/no-match do M2.
- Modificação de `baseline.py`, `acceptance.py`, `canonical_audio.py`,
  `multi_cue_orchestration.py`, ou de qualquer documento predecessor
  (`docs/analysis-core-contracts.md`, `docs/analysis-effective-
  configuration.md`, `docs/occurrence-temporal-semantics-and-policy.md`);
  todos permanecem inalterados.
- API REST, WebUI, autenticação, ou execução assíncrona.

## Multiplicidade após S0009

O schema permanece `analysis_result.v1`: `occurrences` já é um array e não
precisa de migração. Para `normalized_cross_correlation_multi_v1`, esse array
retém todas as occurrences selecionadas (até 100 por cue), na ordem
cronológica produzida pelo matcher, com score bruto, `matching_method` e
`end=null`. `CueOccurrences` continua inválido quando vazio, e `no_match` e
falha continuam ramos explícitos.

## Referências

- `docs/architecture.md` -- Core, Analysis, Occurrence, Analysis Result,
  Princípio 1 (independência do Core).
- `docs/milestones.md` -- M3, escopo e dependência de M3-07 e milestones
  posteriores sobre este modelo único de resultado.
- `docs/analysis-core-contracts.md` -- contrato normativo de `Analysis`,
  `Cue`, `Occurrence`, `PerCueOutcome` (M3-01/M3-05), reproduzido em forma
  Python própria do Core por este documento.
- `docs/analysis-effective-configuration.md` -- `EffectiveConfiguration
  Snapshot` (M3-02), reproduzido verbatim pelo campo `configuration`.
- `docs/occurrence-temporal-semantics-and-policy.md` -- contrato temporal
  de `Occurrence` e política inicial de multiplicidade (M3-04).
- `src/audio_cue_locator/application/multi_cue_orchestration.py` --
  projeção executável equivalente de `PerCueOutcome` em Application,
  estruturalmente compatível com, mas não importada por, este módulo de
  Core.
- `intents/M3/M3-06/implementation-handoff.json` -- escopo autorizado
  desta issue e decisões de handoff (G1-G8).
