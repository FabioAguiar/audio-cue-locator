# Matching Acceptance Policy

## Finalidade

Este documento registra a política inicial de aceitação/no-match, baseada em
evidência, para o baseline de matching de cue única (M2-02), exigida por
`issues/M2/M2-05/formal-issue.json`: o valor de threshold justificado, sua
metodologia explícita de derivação, suas limitações de tipo de cue/tamanho de
corpus, o mecanismo de integração com o baseline existente, e o mecanismo de
configurabilidade interna.

Este documento **não redefine** o contrato interno já fixado por M2-01
(`docs/matching-contract.md`), a implementação e semântica já fixadas por
M2-02 (`docs/matching-baseline.md`), nem a evidência de regressão e robustez
já fixadas por M2-03 (`docs/matching-regression.md`) e M2-04
(`docs/matching-robustness.md`): as quatro alternativas de resultado, a
semântica de timestamp, a semântica de score e o método (correlação cruzada
normalizada) descritos ali são exercitados aqui, não alterados. Em caso de
qualquer aparente divergência, esses quatro documentos prevalecem; este
documento é o registro da política de decisão derivada sobre a evidência que
eles já produziram, não uma quinta fonte de verdade.

Implementação: `src/audio_cue_locator/infrastructure/acoustic_matching/acceptance.py`.
Testes: `tests/test_matching_acceptance.py`.
Baseline exercitado (não modificado): `src/audio_cue_locator/infrastructure/acoustic_matching/baseline.py`.

## 1. Mecanismo de Integração (Gaps G1 e G2)

`states/M2/M2-05/issue-operational-state.json` registrou deliberadamente como
não resolvidas duas questões arquiteturais (G1, G2). Ambas são resolvidas por
esta issue, conforme já fixado por
`intents/M2/M2-05/implementation-handoff.json`:

- **G1 — mecanismo de integração**: a política de aceitação integra-se
  fornecendo um novo valor de `EffectiveConfiguration`, explicitamente
  justificado por evidência, para a chamada já existente de `match_cue`
  (reuso paramétrico do próprio ramo de decisão `best_score >=
  configuration.acceptance_threshold` de `baseline.py`). Este módulo **não**
  implementa uma segunda camada de decisão posterior ao resultado do
  `match_cue`, e **não** inspeciona nem sobrescreve um `MatchResult.outcome`
  já produzido. Isso preserva a fronteira de `docs/matching-contract.md`
  seção 5.2: o `diagnostic_best_score` retido por um resultado `no_match` é
  evidência, nunca uma rota para reclassificar esse mesmo resultado como
  `found` posteriormente.
- **G2 — modificação do baseline**: `baseline.py`, incluindo sua própria
  `DEFAULT_CONFIGURATION` (`acceptance_threshold=0.75`), **não é modificado**
  por esta issue. `EVIDENCE_BASED_CONFIGURATION` (seção 3) é aditiva: um
  segundo valor de `EffectiveConfiguration`, nomeado e definido em
  `acceptance.py`, reutilizando a mesma dataclass que `baseline.py` já define
  e já aceita como parâmetro de `match_cue`.

Uso pretendido:

```python
from audio_cue_locator.infrastructure.acoustic_matching.acceptance import (
    EVIDENCE_BASED_CONFIGURATION,
)
from audio_cue_locator.infrastructure.acoustic_matching.baseline import match_cue

result = match_cue(source, cue, EVIDENCE_BASED_CONFIGURATION)
```

`found` vs. `no_match` continua sendo inteiramente a decisão de `baseline.py`
(`docs/matching-contract.md` seção 5, seção 5.2), aplicada aqui a um valor de
`acceptance_threshold` diferente e explicitamente justificado, não a uma
segunda fonte de verdade sobre aceitação/rejeição.

## 2. Metodologia de Derivação do Threshold (Gap G7)

O critério adotado é uma **regra de margem explícita, não calibrada**: o
threshold justificado é o ponto médio entre o maior score de diagnóstico
`no_match` já observado e o menor score `found` já observado, ambos já
registrados por `docs/matching-regression.md` (M2-03) e
`docs/matching-robustness.md` (M2-04):

```text
highest_observed_no_match_diagnostic_score = 0.47327783604930573
  (docs/matching-robustness.md, caso no_match_representative_absent_cue)

lowest_observed_found_score = 0.9380619505661985
  (docs/matching-robustness.md, caso found_representative_noise_high_snr)

acceptance_threshold = (highest_observed_no_match_diagnostic_score
                         + lowest_observed_found_score) / 2
                      ≈ 0.7056698933077521
```

Este **não** é um valor inventado nem uma calibração estatística: é uma
margem geométrica simples, equidistante das duas observações de fronteira já
em evidência, implementada em `acceptance.py`,
`derive_acceptance_threshold()`. Nenhuma probabilidade, intervalo de
confiança ou calibração é reivindicada por esse cálculo — apenas que o
threshold escolhido mantém uma margem explícita e igual em relação a ambas as
observações.

Por que essas duas observações específicas: `docs/matching-regression.md`
seção 4 e `docs/matching-robustness.md` seção 6 já retêm os scores de
diagnóstico `no_match` explicitamente "como evidência" para a futura política
de M2-05; `docs/matching-robustness.md` seção 5 já enquadra o score de
discriminação `no_match` e os scores `found` sob ruído como as duas
observações de fronteira que um threshold deve separar. Os seis casos `found`
de M2-03 (score exatamente `1.0`) e os três casos `found` de M2-04 sob
perturbação de amplitude (score exatamente `1.0`, por invariância à escala
positiva) não são o score `found` mais baixo observado; o caso mais próximo
da fronteira, e portanto o unicamente relevante para a margem, é
`found_representative_noise_high_snr` (`0.9380619505661985`). Da mesma forma,
os três casos degenerados `no_match` de M2-03 (score exatamente `0.0`) não
são o score de diagnóstico mais alto observado; o caso relevante é
`no_match_representative_absent_cue` (`≈0.47327783604930573`), a única
verificação de discriminação genuína (não uma construção de soma-zero) já
produzida.

### Comparação com o valor provisório existente

O valor derivado (`≈0.7057`) é **menor** que o corte provisório e não
calibrado que `baseline.py` já documenta (`DEFAULT_CONFIGURATION.acceptance_threshold
= 0.75`, `docs/matching-baseline.md` seção 2, explicitamente "sujeito a
revisão por M2-05"). Isso não é uma coincidência buscada nem um
reposicionamento arbitrário: `0.75` nunca foi derivado da evidência de
M2-03/M2-04 (antecede inteiramente a evidência de M2-04) e é documentado como
um placeholder técnico, não uma política de aceitação. O valor aqui derivado
permanece, com folga confortável, acima do maior score de diagnóstico
`no_match` observado e abaixo do menor score `found` observado — portanto não
altera o resultado (`found`/`no_match`) de nenhum caso já registrado por
`docs/matching-regression.md` ou `docs/matching-robustness.md`: todo caso
`found` já registrado tem score `>= 0.9380619505661985 > 0.7057`, e todo caso
`no_match`/diagnóstico já registrado tem score `<= 0.47327783604930573 <
0.7057`.

## 3. Configuração Efetiva e Configurabilidade Interna (Gap G8)

```text
EVIDENCE_BASED_CONFIGURATION = EffectiveConfiguration(
    method="normalized_cross_correlation_v1",
    acceptance_threshold=0.7056698933077521,  # derivado, seção 2
)
```

A configurabilidade interna é exposta exatamente da mesma forma que
`baseline.py` já expõe `DEFAULT_CONFIGURATION`: uma constante nomeada, em
nível de módulo, rastreável e inspecionável, da mesma forma de dataclass já
existente (`method` + `acceptance_threshold`). Nenhum novo tipo de
configuração, nenhuma nova superfície de API pública, e nenhum campo adicional
foi introduzido — a "forma da obrigação" que `docs/matching-contract.md`
seção 2 exige (identificador de método estável + configuração efetiva
rastreável) já é satisfeita pela dataclass existente, e este módulo reutiliza
essa forma sem a estender.

## 4. Comportamento de Fronteira de Decisão (Equalidade)

O ramo de decisão reutilizado, sem modificação, de `baseline.py` é:

```python
if best_score >= configuration.acceptance_threshold:
    ...  # found
```

Um score **exatamente igual** ao threshold em vigor resolve para `found`
(comparação `>=`), não para `no_match`. `tests/test_matching_acceptance.py`
verifica esse comportamento de igualdade de forma analítica e exata —
`test_score_exactly_equal_to_threshold_is_found` constrói uma janela cujo
score de correlação normalizada é exatamente `0.6` (razão pitagórica 3-4-5,
verificável por aritmética inteira exata), contra uma configuração de teste
dedicada com `acceptance_threshold=0.6`, confirmando `found`; um score
imediatamente abaixo confirma `no_match`. O valor `0.6` é usado nesse teste
específico, em vez do próprio `EVIDENCE_BASED_ACCEPTANCE_THRESHOLD`
(`≈0.7056698933077521`), porque esse último é um valor de ponto flutuante
derivado de uma média cujo bit exato não pode ser reproduzido de forma
confiável através da conversão para `float32`; o comportamento de igualdade
verificado é, de todo modo, exatamente o mesmo ramo `>=` de `baseline.py`,
independentemente do valor numérico do threshold em vigor.

## 5. Regressões de Fronteira (Casos Borderline) — Gap G6

Nenhuma fixture borderline (próxima ao threshold) existia em
`tests/fixtures/matching/manifest.json` (M2-03) ou
`tests/fixtures/matching/representative-manifest.json` (M2-04) antes desta
issue. `tests/test_matching_acceptance.py` autora dois novos casos, embutidos
inline (sem novo arquivo de manifesto, já que a issue formal não autoriza um
novo caminho de fixture), exercitando ambos os lados do threshold
justificado (`≈0.7056698933077521`):

| Caso | Score alvo | Resultado |
|---|---|---|
| `test_borderline_case_just_above_the_evidence_based_threshold_is_found` | `≈0.7256698933` (threshold + 0.02) | `found` |
| `test_borderline_case_just_below_the_evidence_based_threshold_is_no_match` | `≈0.6856698933` (threshold − 0.02) | `no_match`, com `diagnostic_best_score` retido |

Ambos os casos usam a mesma construção de decomposição ortogonal descrita no
docstring do módulo de teste: uma fonte de janela única (comprimento igual ao
da cue, portanto sem janela candidata concorrente) cujo score de correlação
normalizada contra a cue de referência é analiticamente previsível.

## 6. Limitações de Tipo de Cue e Tamanho de Corpus

Esta derivação está limitada pela evidência disponível, que permanece
pequena e curada, não um programa de dataset amplo:

- Seis casos sintéticos determinísticos de M2-03
  (`tests/fixtures/matching/manifest.json`): uma única morfologia de cue
  (sequências numéricas curtas arbitrárias).
- Seis casos de mídia representativa de M2-04
  (`tests/fixtures/matching/representative-manifest.json`): uma única
  morfologia de cue (rajada harmônica 440/880 Hz com envelope de Hann),
  três fatores de escala de amplitude, dois níveis de SNR de ruído aditivo, e
  um caso de discriminação de ausência de cue.
- Nenhuma perturbação de compressão com perda foi avaliada (`ffmpeg`/
  `ffprobe` indisponíveis; `docs/matching-robustness.md` seção 3.2).
- Nenhuma mídia real decodificada foi usada em qualquer etapa anterior; toda
  a evidência é sintética.

O threshold derivado **não** é reivindicado como generalizável além do
método de correlação cruzada normalizada, além dos tipos de cue já
evidenciados, ou como uma calibração estatística de confiança. Um corpus
maior, com tipos de cue e condições de perturbação adicionais (incluindo
compressão com perda, quando `ffmpeg`/`ffprobe` estiverem disponíveis),
poderia justificar um valor diferente; esta issue não certifica suficiência
geral, apenas deriva um valor explícito e rastreável a partir da evidência
disponível.

## 7. Score Não É Confiança Calibrada; Não É Comparável Entre Métodos

Todo score envolvido nesta política é reproduzido exatamente como produzido
por `match_cue` — uma similaridade específica do método de correlação
cruzada normalizada (`docs/matching-contract.md` seção 4) — e nunca é
reescalado, arredondado para uma faixa `[0, 1]` com semântica de
probabilidade, ou apresentado como uma probabilidade calibrada de detecção
correta. Esta política é explicitamente escopada ao método
`"normalized_cross_correlation_v1"` apenas; nenhuma comparabilidade entre
métodos é reivindicada ou pode ser assumida por um método futuro diferente.
Nenhum threshold universal entre métodos futuros é definido ou implicado por
este documento.

## 8. `no_match` Permanece Distinto de `invalid_input` e `processing_failure`

Este módulo não introduz, redefine ou toca o tratamento de `invalid_input`
ou `processing_failure`. `tests/test_matching_acceptance.py` verifica
explicitamente que ambos permanecem distintos de `no_match` sob a nova
configuração:

- `test_invalid_input_is_not_reclassified_as_no_match_under_the_evidence_based_configuration`
  — uma cue vazia continua produzindo `invalid_input`, não `no_match`.
- `test_processing_failure_is_not_reclassified_as_no_match_under_the_evidence_based_configuration`
  — uma falha do `candidate_locator` continua produzindo `processing_failure`,
  não `no_match`.

Nenhum dos dois é afetado pelo valor de `acceptance_threshold` em vigor, já
que ambos ocorrem antes (`invalid_input`) ou durante uma falha interna
(`processing_failure`) do próprio cálculo de correlação, nunca no ramo de
comparação de threshold.

## 9. Determinismo

Os novos casos borderline são exercitados duas vezes por
`tests/test_matching_acceptance.py`
(`test_borderline_cases_are_deterministic_across_repeated_calls`), afirmando
igualdade estrita entre chamadas repetidas de `match_cue` com as mesmas
entradas e configuração — consistente com o precedente já estabelecido por
`tests/test_matching_baseline.py`, `tests/test_matching_regression.py` e
`tests/test_matching_robustness.py`.

## 10. Fora de Escopo Deste Documento

- Calibração de confiança estatística, threshold universal entre métodos
  futuros, e configuração via API pública.
- Redefinição da taxonomia de quatro resultados, da semântica de score, da
  convenção de timestamp, ou do critério de precisão já fixados por
  M2-01/M2-02/M2-03/M2-04.
- Qualquer alteração em
  `src/audio_cue_locator/infrastructure/acoustic_matching/baseline.py`,
  `docs/matching-contract.md`, `docs/matching-baseline.md`,
  `docs/matching-regression.md`, `docs/matching-robustness.md`,
  `tests/test_matching_baseline.py`, `tests/test_matching_regression.py`,
  `tests/test_matching_robustness.py`,
  `tests/fixtures/matching/manifest.json`, ou
  `tests/fixtures/matching/representative-manifest.json`: este documento e a
  política que ele descreve exercitam a implementação e a evidência
  existentes, não as modificam.
- Um programa de avaliação independente e novo; esta política é derivada
  exclusivamente da evidência já produzida por M2-03/M2-04, não de uma nova
  coleta de dados.
- Múltiplas cues, suporte completo a múltiplas ocorrências, Analysis
  persistence, SQLite, execução assíncrona, REST API, WebUI, waveform,
  calibração de confiança, aprendizado de máquina e processamento
  distribuído.

## Referências

- `docs/matching-contract.md` — contrato interno do matcher (M2-01); este
  documento exercita, e não redefine, suas seções 1–5, em particular a
  fronteira Score vs. Decision (seção 4) que reserva esta política a M2-05.
- `docs/matching-baseline.md` — implementação numérica e semântica de
  score/timestamp (M2-02); documenta `DEFAULT_CONFIGURATION.acceptance_threshold=0.75`
  como provisório e "sujeito a revisão por M2-05".
- `docs/matching-regression.md` — evidência de regressão sintética (M2-03);
  fonte dos scores `found`/`no_match` exatos usados na seção 2.
- `docs/matching-robustness.md` — evidência de robustez de mídia
  representativa (M2-04); fonte primária dos scores de fronteira usados na
  seção 2 (`0.47327783604930573`, `0.9380619505661985`).
- `src/audio_cue_locator/infrastructure/acoustic_matching/baseline.py` —
  implementação exercitada, não modificada, por esta política.
- `src/audio_cue_locator/infrastructure/acoustic_matching/acceptance.py` —
  implementação desta política.
- `tests/test_matching_acceptance.py` — suíte que afirma o comportamento
  descrito neste documento.
- `issues/M2/M2-05/formal-issue.json` — especificação formal desta issue.
- `intents/M2/M2-05/implementation-handoff.json` — handoff que autorizou e
  restringiu o escopo desta implementação, incluindo a resolução dos gaps
  G1, G2, G7 e G8 descrita nas seções 1–3 acima.
