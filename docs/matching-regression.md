# Matching Regression Evidence

## Finalidade

Este documento registra a evidência de regressão determinística de
timestamp e score para o baseline de matching de cue única (M2-02),
exigida por `issues/M2/M2-03/formal-issue.json`: um conjunto mínimo de
fixtures sintéticas determinísticas, os resultados esperados vs.
observados por caso, o erro absoluto de timing, o comportamento de score,
e um critério de precisão do baseline explicitamente justificado — não
inventado.

Este documento **não redefine** o contrato interno já fixado por M2-01
(`docs/matching-contract.md`) nem a implementação e semântica já fixadas
por M2-02 (`docs/matching-baseline.md`): as quatro alternativas de
resultado (`found`, `no_match`, `invalid_input`, `processing_failure`), a
semântica de timestamp, a semântica de score e o método (correlação
cruzada normalizada) descritos ali são exercitados aqui, não alterados.
Em caso de qualquer aparente divergência, `docs/matching-contract.md` e
`docs/matching-baseline.md` prevalecem; este documento é o registro da
evidência de regressão coletada sobre eles, não uma terceira fonte de
verdade.

Fixtures: `tests/fixtures/matching/manifest.json`.
Suite de regressão: `tests/test_matching_regression.py`.
Implementação exercitada: `src/audio_cue_locator/infrastructure/acoustic_matching/baseline.py`.

## 1. Critério de Precisão do Baseline

O critério de precisão adotado é: **o erro absoluto de timing máximo
aceitável para um resultado `found` é um período de amostra na taxa
canônica**, isto é:

```text
max_absolute_timing_error_seconds = 1 / CANONICAL_AUDIO_SPEC.sample_rate_hz
                                   = 1 / 48000
                                   ≈ 2.0833333333333333e-05 segundos
```

Este critério **não é um valor numérico inventado**. Ele é derivado
diretamente do próprio algoritmo já documentado por
`docs/matching-baseline.md` §1: `_locate_best_candidate` seleciona o
candidato de melhor score como um índice de amostra **inteiro** (o
argumento máximo de `numpy.correlate(..., mode="valid")`), convertido para
segundos dividindo pela taxa de amostragem canônica. Nenhuma interpolação
sub-amostra é implementada ou reivindicada pelo baseline; logo, uma amostra
é a resolução temporal máxima que o próprio algoritmo pode, por
construção, alcançar — não uma tolerância ajustada empiricamente.

Avaliação contra as fixtures: os dois casos `found_known_offset` abaixo
embutem a cue **verbatim, sem modificação**, em um source silencioso, em um
offset de amostra inteiro conhecido. Pela igualdade de Cauchy-Schwarz
(a janela no offset correto é idêntica à cue), o score nessa janela é
exatamente `1.0`, e o índice de melhor candidato é exatamente o offset de
inserção. O erro absoluto de timing esperado para ambos os casos é,
portanto, `0.0` segundos — dentro do critério de um período de amostra
com ampla margem —, confirmando que o critério é satisfeito nas fixtures
determinísticas deste conjunto. Este documento não estende essa conclusão
a mídia representativa (fora de escopo; ver seção 5).

## 2. Conjunto de Fixtures

Todas as fixtures são arrays sintéticos construídos em memória (não mídia
decodificada), convertidos para `float32` mono pela suite de testes,
satisfazendo `CANONICAL_AUDIO_SPEC` (48000 Hz, mono, float32,
normalização de pico com entrada silenciosa preservada) por construção:
todo valor de amostra já está no intervalo normalizado esperado e nenhuma
transformação de canonicalização é necessária ou realizada por este
conjunto.

| case_id | categoria | len(source) | len(cue) | offset conhecido |
|---|---|---|---|---|
| `found_offset_near_start` | known offset (found) | 27 | 6 | amostra 12 |
| `found_offset_near_end` | known offset (found) | 62 | 7 | amostra 53 |
| `no_match_absent_cue` | absent cue (no_match) | 36 | 4 | n/a |
| `no_match_silent_cue` | silence boundary (no_match) | 10 | 4 | n/a |
| `no_match_silent_source` | silence boundary (no_match) | 25 | 4 | n/a |
| `no_match_source_shorter_than_cue` | length boundary (no_match) | 4 | 6 | n/a |

## 3. Resultados Esperados vs. Observados por Caso

Os valores "observados" abaixo são os valores que `match_cue` produz por
construção determinística do algoritmo documentado em
`docs/matching-baseline.md` (verificado por inspeção matemática direta das
fixtures contra a fórmula publicada, não por execução de teste nesta fase
de implementação); a suite de testes (`tests/test_matching_regression.py`)
os afirma automaticamente numa fase de validação posterior.

| case_id | outcome esperado | timestamp esperado (s) | erro absoluto de timing esperado (s) | score esperado | score de diagnóstico esperado |
|---|---|---|---|---|---|
| `found_offset_near_start` | `found` | `0.00025` (12/48000) | `0.0` | `1.0` (tolerância ±1e-6) | n/a |
| `found_offset_near_end` | `found` | `0.0011041666666666667` (53/48000) | `0.0` | `1.0` (tolerância ±1e-6) | n/a |
| `no_match_absent_cue` | `no_match` | n/a (`timestamp_seconds` é `None`) | n/a | n/a (`score` é `None`) | `0.0` (tolerância ±1e-9), na amostra `0` |
| `no_match_silent_cue` | `no_match` | n/a | n/a | n/a | `0.0` (tolerância ±1e-9), na amostra `0` |
| `no_match_silent_source` | `no_match` | n/a | n/a | n/a | `0.0` (tolerância ±1e-9), na amostra `0` |
| `no_match_source_shorter_than_cue` | `no_match` | n/a | n/a | n/a | n/a (nenhuma janela candidata existe; `diagnostic_best_score`/`diagnostic_best_timestamp_seconds` permanecem `None`, apenas `reason` é populado com "shorter than cue") |

Justificativa matemática resumida por caso:

- **`found_offset_near_start` / `found_offset_near_end`**: a cue não
  contém nenhuma amostra de valor zero; qualquer janela que não seja
  exatamente a posição de inserção é uma janela toda-silenciosa (energia
  ~zero, excluída do ramo de score) ou uma sobreposição parcial que não
  pode ser um múltiplo escalar positivo da cue (faltaria pelo menos uma
  amostra não-nula da cue). Logo o máximo em `1.0` é único no offset
  conhecido.
- **`no_match_absent_cue`**: a cue alternada de soma zero `[1, -1, 1, -1]`
  produz `dot(janela, cue) = valor*(1-1+1-1) = 0` para qualquer janela
  constante do source, em qualquer offset — um score `0.0` genuíno (não o
  valor-padrão de energia-degenerada), abaixo do
  `acceptance_threshold=0.75` documentado em `docs/matching-baseline.md`
  §2.
- **`no_match_silent_cue`** / **`no_match_silent_source`**: energia
  ~zero (da cue ou de toda janela do source, respectivamente) torna a
  correlação normalizada matematicamente indefinida; `baseline.py` define
  esse caso, de forma determinística, como score `0.0` em todo lag, sem
  jamais calcular a divisão indefinida.
- **`no_match_source_shorter_than_cue`**: `docs/matching-contract.md` §1
  permite explicitamente que o source seja mais curto que a cue;
  `match_cue` retorna `no_match` por um ramo explícito antes de qualquer
  busca de candidato, portanto nenhum valor de diagnóstico existe.

## 4. Evidência de Diagnóstico de Cue Ausente (para M2-05)

Os valores `diagnostic_best_score` / `diagnostic_best_timestamp_seconds`
registrados nos três casos `no_match` de energia-degenerada acima (todos
exatamente `0.0`, na amostra `0`) são retidos **apenas como evidência de
regressão** para a futura política de aceitação de M2-05
(`docs/matching-contract.md` §4, §5.2). Este documento e a suite de testes
que o acompanha **não** tratam esses valores como uma decisão prematura de
`found`/`no_match`: a fronteira Score vs. Decision já estabelecida por
M2-01/M2-02 é preservada integralmente por este conjunto de evidências.

## 5. Determinismo

Cada caso do manifesto é exercitado duas vezes por
`tests/test_matching_regression.py`
(`test_matching_regression_case_is_deterministic_across_repeated_calls`),
afirmando igualdade estrita entre chamadas repetidas de `match_cue` com as
mesmas entradas e configuração — consistente com o precedente já
estabelecido por `tests/test_matching_baseline.py`. Isso satisfaz o
critério de aceite do issue formal de que os testes de regressão sejam
repetíveis.

## 6. Fora de Escopo Deste Documento

- Seleção de um corpus de mídia real representativo, escolha do threshold
  de aceitação final, e medição de desempenho computacional (reservados
  para M2-05 e para uma etapa posterior orientada por necessidade
  medida).
- Redefinição da taxonomia de quatro resultados, da semântica de score ou
  da convenção de timestamp já fixadas por M2-01/M2-02.
- Qualquer alteração em `src/audio_cue_locator/infrastructure/acoustic_matching/baseline.py`,
  `docs/matching-contract.md` ou `docs/matching-baseline.md`: este
  documento exercita a implementação existente, não a modifica.
- A política de aceitação/rejeição baseada em evidência (M2-05).
- Verificação de probing/decodificação ao vivo (não mockada) de mídia
  representativa — pré-requisito herdado de M1/M2-01/M2-02, pendente e não
  resolvido por este documento.
- Reuso das fixtures de mídia M1-era existentes em `tests/fixtures/`
  (`not_media.txt`, `no_audio_stream.mp4`, `sine_440hz_mono_1s.wav`,
  `video_with_audio.mp4`): `tests/fixtures/matching/` é um subdiretório
  novo e totalmente independente, já que as fixtures deste issue são
  arrays numpy construídos em código, não mídia decodificada.

## Referências

- `docs/matching-contract.md` — contrato interno do matcher (M2-01); este
  documento exercita, e não redefine, suas seções 1–5.
- `docs/matching-baseline.md` — implementação numérica e semântica de
  score/timestamp (M2-02); este documento exercita, e não redefine, suas
  seções 1–6.
- `src/audio_cue_locator/infrastructure/acoustic_matching/baseline.py` —
  implementação exercitada por este conjunto de evidências.
- `tests/test_matching_baseline.py` — precedente de estilo e de asserção
  de determinismo seguido por `tests/test_matching_regression.py`.
- `tests/fixtures/matching/manifest.json` — fixtures determinísticas
  descritas por este documento.
- `tests/test_matching_regression.py` — suite de regressão que afirma os
  valores esperados registrados neste documento.
- `issues/M2/M2-03/formal-issue.json` — especificação formal desta issue.
