# Matching Robustness Evidence

## Finalidade

Este documento registra a evidência reduzida de robustez e pré-processamento
para o baseline de matching de cue única (M2-02), exigida por
`issues/M2/M2-04/formal-issue.json`: um conjunto mínimo e curado de
evidência representativa, as condições de perturbação de amplitude e ruído
selecionadas (com as condições omitidas explicitamente listadas), o
comportamento de score/timestamp observado por caso, e o status da
verificação de probing/decodificação ao vivo (FFmpeg) herdada de M1, com
seu impacto de bloqueio explicitamente registrado.

Este documento **não redefine** o contrato interno já fixado por M2-01
(`docs/matching-contract.md`), a implementação e semântica já fixadas por
M2-02 (`docs/matching-baseline.md`), nem a evidência de regressão sintética
e o critério de precisão já fixados por M2-03 (`docs/matching-regression.md`):
as quatro alternativas de resultado, a semântica de timestamp, a semântica
de score, o método (correlação cruzada normalizada) e o critério de
precisão de um período de amostra descritos ali são exercitados aqui, não
alterados. Em caso de qualquer aparente divergência, `docs/matching-contract.md`,
`docs/matching-baseline.md` e `docs/matching-regression.md` prevalecem; este
documento é o registro da evidência de robustez coletada sobre eles, não uma
quarta fonte de verdade.

Fixtures: `tests/fixtures/matching/representative-manifest.json`.
Suite de robustez: `tests/test_matching_robustness.py`.
Implementação exercitada: `src/audio_cue_locator/infrastructure/acoustic_matching/baseline.py`.

## 1. Distinção em Relação a M2-03

M2-03 (`docs/matching-regression.md`) usa fixtures puramente sintéticas e
arbitrárias (arrays numéricos curtos construídos apenas para exercitar
timestamp/score/degeneração de forma determinística). Este documento avalia
um conjunto distinto, com forma espectral/temporal mais próxima de uma cue
acústica real: uma rajada harmônica (440 Hz + 880 Hz, com envelope de Hann,
240 amostras) em vez de sequências numéricas arbitrárias curtas — mais
representativa no sentido pretendido pela issue formal, embora ainda
inteiramente sintética (seção 6 trata a razão dessa escolha e seus limites).

## 2. Status da Verificação de Probing/Decodificação ao Vivo (FFmpeg)

`ffmpeg` e `ffprobe` foram reconfirmados **indisponíveis** neste ambiente de
implementação (`which ffmpeg ffprobe` não retornou caminho para nenhum dos
dois), consistente com `canonical_audio.py`'s própria
`PENDING_LIVE_PROBING_VERIFICATION` e com o gap G2 já registrado por
`states/M2/M2-04/issue-operational-state.json`.

Impacto de bloqueio, registrado explicitamente (critério de aceite 4):

- Nenhum ativo de mídia real codificada (áudio/vídeo decodificado) pôde ser
  usado nesta avaliação; todo sinal em
  `tests/fixtures/matching/representative-manifest.json` é um array
  sintético já em forma canônica (mesma convenção de M2-01/M2-02/M2-03), não
  um ativo de mídia real decodificado.
- Nenhuma etapa de pré-processamento de canonicalização/decodificação é
  exercitada por esta avaliação. Os efeitos de pré-processamento são,
  portanto, explicitamente **ausentes** desta avaliação — não "resolvidos",
  não "aprovados", apenas ausentes — e nunca são confundidos com os efeitos
  do matcher (comportamento do `match_cue` sob perturbação de amplitude e
  ruído), que são o que este documento efetivamente mede.
- A perturbação de "compressão" (seção 3) não pôde ser avaliada de forma
  real, tanto pela indisponibilidade do `ffmpeg`/`ffprobe` quanto porque o
  `FFmpegMediaAdapter` existente (`src/audio_cue_locator/infrastructure/media_processing/ffmpeg_adapter.py`)
  expõe apenas `probe()` e `extract_audio()` (extração para WAV), não uma
  operação de codificação com perda; estender o adapter está fora do escopo
  desta issue.

Este status **não é resolvido** por esta implementação; ele é registrado
explicitamente, conforme o critério de aceite 4 exige, para que uma futura
etapa (com `ffmpeg`/`ffprobe` disponíveis e, se necessário, uma extensão
justificada do adapter) possa revisitar a perturbação de compressão e a
verificação de decodificação ao vivo sem depender de suposições silenciosas.

## 3. Condições de Perturbação Selecionadas

### 3.1. Incluídas

| condition_id | categoria | descrição |
|---|---|---|
| `amplitude_scale_1_0` | amplitude | Ocorrência com forma verbatim, amplitude original (sem escala). |
| `amplitude_scale_0_5` | amplitude | Ocorrência com forma verbatim, atenuada para meia amplitude (-6.02 dB). |
| `amplitude_scale_0_25` | amplitude | Ocorrência com forma verbatim, atenuada para um quarto da amplitude (-12.04 dB). |
| `additive_gaussian_noise_seed_20260904_sigma_0_05` | noise | Ruído gaussiano aditivo (NumPy `Generator(PCG64)`, seed 20260904, sigma 0.05) aplicado apenas na janela da ocorrência; SNR medido ≈ 18.30 dB. |
| `additive_gaussian_noise_seed_20260905_sigma_0_15` | noise | Ruído gaussiano aditivo (seed 20260905, sigma 0.15) aplicado apenas na janela da ocorrência; SNR medido ≈ 8.73 dB. |
| `absent_cue_discrimination` | discrimination_no_match | Cue completamente ausente de uma fonte com tom contínuo de forma diferente, verificando discriminação genuína (não uma construção artificial de soma zero). |

### 3.2. Omitidas (explicitamente, não implícitas)

| condition_id | categoria | razão |
|---|---|---|
| `lossy_compression_roundtrip` | compression | Não avaliada nesta issue. `ffmpeg`/`ffprobe` indisponíveis neste ambiente (seção 2); independentemente disso, o `FFmpegMediaAdapter` existente não expõe uma operação de codificação com perda, apenas `probe()`/`extract_audio()`, e estendê-lo está fora do escopo/é um caminho proibido para esta issue. |

Nenhuma outra condição de amplitude/compressão/ruído além das listadas acima
foi avaliada; a ausência de qualquer condição não listada aqui não deve ser
interpretada como equivalente a uma condição aprovada.

## 4. Conjunto de Fixtures

Todas as fixtures são arrays sintéticos construídos em memória (não mídia
decodificada), já em `float32` mono, satisfazendo os requisitos de forma de
`CANONICAL_AUDIO_SPEC` (48000 Hz, mono, float32) por construção — o
`match_cue` não valida normalização de pico (apenas dtype/forma/finitude/
não-vacuidade da cue, conforme `baseline.py`), então este conjunto não
precisa reafirmar explicitamente o pico de 1.0 em cada array, seguindo a
mesma liberdade já exercida por `tests/fixtures/matching/manifest.json`
(M2-03).

A cue de referência é uma rajada harmônica determinística: `0.6*sin(2π·440·t) +
0.4*sin(2π·880·t + 0.3)`, moldada por um envelope de Hann ao longo de 240
amostras (5 ms a 48000 Hz) e normalizada ao próprio pico de amplitude 1.0.

| case_id | categoria | len(source) | len(cue) | offset conhecido |
|---|---|---|---|---|
| `found_representative_amplitude_full_scale` | found_representative_clean | 1920 | 240 | amostra 800 |
| `found_representative_amplitude_attenuated_half` | amplitude_perturbation | 1920 | 240 | amostra 800 |
| `found_representative_amplitude_attenuated_quarter` | amplitude_perturbation | 1920 | 240 | amostra 800 |
| `no_match_representative_absent_cue` | no_match_absent_cue | 1920 | 240 | n/a |
| `found_representative_noise_low_snr` | noise_perturbation | 1920 | 240 | amostra 800 |
| `found_representative_noise_high_snr` | noise_perturbation | 1920 | 240 | amostra 800 |

## 5. Resultados Esperados vs. Observados por Caso

Os valores "esperados" registrados no manifesto (e reproduzidos abaixo) foram
obtidos invocando diretamente o `match_cue` existente e não modificado
durante a autoria deste manifesto — uma computação privada de autoria (não
uma execução da suíte pytest) necessária porque, ao contrário dos arrays
curtos e arbitrários de M2-03 (verificáveis por inspeção matemática direta),
os arrays harmônicos e perturbados por ruído deste conjunto não são
praticamente verificáveis à mão com exatidão. `tests/test_matching_robustness.py`
reinvoca o `match_cue` de forma independente sobre os mesmos arrays em tempo
de teste e afirma os valores abaixo; nenhuma suíte de teste foi executada
durante a autoria deste documento ou do manifesto.

| case_id | outcome esperado | timestamp esperado (s) | score esperado | score de diagnóstico esperado |
|---|---|---|---|---|
| `found_representative_amplitude_full_scale` | `found` | `0.016666666666666666` (800/48000) | `1.0` (tolerância ±1e-6) | n/a |
| `found_representative_amplitude_attenuated_half` | `found` | `0.016666666666666666` | `1.0` (tolerância ±1e-6) | n/a |
| `found_representative_amplitude_attenuated_quarter` | `found` | `0.016666666666666666` | `1.0` (tolerância ±1e-6) | n/a |
| `no_match_representative_absent_cue` | `no_match` | n/a | n/a | `0.47327783604930573` (tolerância ±1e-6), na amostra `216` |
| `found_representative_noise_low_snr` | `found` | `0.016666666666666666` | `0.9926253634032015` (tolerância ±1e-6) | n/a |
| `found_representative_noise_high_snr` | `found` | `0.016666666666666666` | `0.9380619505661985` (tolerância ±1e-6) | n/a |

Justificativa analítica por categoria:

- **Perturbação de amplitude (`amplitude_perturbation`, incluindo o caso
  `found_representative_clean` em escala 1.0)**: a correlação cruzada
  normalizada é matematicamente invariante à escala positiva de um sinal:
  para uma janela `w = k·cue` (k > 0), `score = dot(k·cue, cue) / (||k·cue|| · ||cue||)
  = k·dot(cue,cue) / (k·||cue|| · ||cue||) = 1.0`, pelo limite de igualdade
  de Cauchy-Schwarz. Isso é confirmado empiricamente pelos três fatores de
  escala avaliados (1.0, 0.5, 0.25): o score permanece exatamente `1.0` em
  todos os três, demonstrando robustez à variação de amplitude por
  construção do próprio método, não por ajuste específico da fixture.
- **Perturbação de ruído (`noise_perturbation`)**: ruído gaussiano aditivo
  quebra a proporcionalidade escalar exata entre a janela e a cue, então o
  score cai abaixo de `1.0`. Sob a aproximação de ruído independente de
  média zero, o score esperado é aproximadamente
  `1 / sqrt(1 + energia_ruído/energia_cue)`; para os dois níveis avaliados,
  essa aproximação analítica (`0.992691` e `0.939079`, respectivamente)
  ficou muito próxima do valor efetivamente produzido pelo `match_cue`
  (`0.9926253634032015` e `0.9380619505661985`), uma verificação cruzada
  adicional de que os valores registrados são plausíveis, não apenas
  copiados de uma única fonte de cálculo.
- **`no_match_representative_absent_cue`**: a fonte é um tom contínuo de
  300 Hz, sem qualquer ocorrência da rajada harmônica de 440/880 Hz com
  envelope de Hann. Isso verifica discriminação genuína (diferente da
  construção de soma-zero de M2-03): o melhor candidato observado
  (`diagnostic_best_score ≈ 0.473`) fica claramente abaixo do
  `acceptance_threshold=0.75` documentado em `docs/matching-baseline.md`
  §2, sem depender de uma coincidência matemática trivial.

## 6. Evidência de Diagnóstico de Cue Ausente (para M2-05)

O valor `diagnostic_best_score` registrado no caso `no_match` acima
(`≈0.473`, na amostra `216`) é retido **apenas como evidência de robustez**
para a futura política de aceitação de M2-05 (`docs/matching-contract.md`
§4, §5.2). Este documento e a suíte de testes que o acompanha **não**
tratam esse valor como uma decisão prematura de `found`/`no_match`: a
fronteira Score vs. Decision já estabelecida por M2-01/M2-02 é preservada
integralmente.

## 7. Score Não É Probabilidade

Todo score registrado neste documento é reproduzido exatamente como
produzido por `match_cue` — uma similaridade específica do método de
correlação cruzada normalizada (`docs/matching-contract.md` §4) — e nunca é
reescalado, arredondado para uma faixa `[0, 1]` com semântica de
probabilidade, ou apresentado como uma probabilidade calibrada de detecção
correta. Um score de `0.938` sob ruído, por exemplo, não significa "93.8% de
chance de ser a cue correta"; significa apenas que a correlação normalizada
entre a janela observada e a cue, sob este método específico, foi `0.938`.

## 8. Provenance e Direitos de Reuso

Nenhum ativo de mídia representativa externo (nenhum arquivo de áudio/vídeo
baixado, gravado ou de terceiros) é usado neste conjunto. Todo array de cue
e de fonte em `tests/fixtures/matching/representative-manifest.json` é
gerado sinteticamente por uma fórmula determinística documentada (rajada
harmônica com envelope de Hann; tom contínuo único; ruído gaussiano aditivo
a partir de uma seed fixa e documentada) e embutido diretamente como dados
de amostra em lista simples — a mesma convenção já usada por
`tests/fixtures/matching/manifest.json` (M2-03). Nenhuma obrigação de
direitos de reuso, licenciamento ou atribuição de terceiros se aplica a
qualquer ativo deste conjunto.

Decisão sobre as fixtures de mídia M1-era existentes em `tests/fixtures/`
(`not_media.txt`, `no_audio_stream.mp4`, `sine_440hz_mono_1s.wav`,
`video_with_audio.mp4`): **não reutilizadas**. O próprio docstring de módulo
de `tests/test_canonicalization_fixtures.py` já declara que essas fixtures
não representam material de matching real-world/representativo — elas
existem para exercitar o tratamento de erro de canonicalização/probing de
M1, não para servir como cues acústicas com posição de referência conhecida
ou rótulo de ausência documentado. Reutilizá-las aqui exigiria inventar uma
semântica de matching para ativos projetados para outro propósito. Esta
decisão resolve o gap de estado G6.

## 9. Determinismo

Cada caso do manifesto é exercitado duas vezes por
`tests/test_matching_robustness.py`
(`test_matching_robustness_case_is_deterministic_across_repeated_calls`),
afirmando igualdade estrita entre chamadas repetidas de `match_cue` com as
mesmas entradas e configuração — consistente com o precedente já
estabelecido por `tests/test_matching_baseline.py` e
`tests/test_matching_regression.py`. Todos os arrays de ruído são embutidos
como dados fixos no manifesto (gerados uma única vez em tempo de autoria via
`numpy.random.default_rng` com seed documentada), não regenerados em tempo
de teste, eliminando qualquer dependência de reprodutibilidade de gerador de
números pseudoaleatórios na suíte de teste em si.

## 10. Limitações Reconhecidas

- Este conjunto permanece sintético, não mídia real decodificada; ele é a
  aproximação mais próxima autorizada por
  `intents/M2/M2-04/implementation-handoff.json` (que restringe conteúdo
  representativo novo a ser embutido no próprio manifesto, não a um novo
  arquivo binário não nomeado), dada a indisponibilidade de `ffmpeg`/
  `ffprobe` e a ausência de qualquer ativo candidato nomeado pela issue
  formal ou por qualquer artefato anterior (gap G7).
- A perturbação de compressão permanece não avaliada (seção 2, seção 3.2).
- O tamanho da amostra avaliada é pequeno e curado, não um programa de
  dataset amplo (explicitamente fora de escopo desta issue).
- Este documento não seleciona o threshold de aceitação final nem a
  política de aceitação/rejeição baseada em evidência; ambos permanecem
  reservados a M2-05.

## 11. Fora de Escopo Deste Documento

- Seleção do threshold de aceitação final e a política de aceitação/
  rejeição baseada em evidência (M2-05).
- Redefinição da taxonomia de quatro resultados, da semântica de score, da
  convenção de timestamp ou do critério de precisão de um período de
  amostra já fixados por M2-01/M2-02/M2-03.
- Qualquer alteração em
  `src/audio_cue_locator/infrastructure/acoustic_matching/baseline.py`,
  `docs/matching-contract.md`, `docs/matching-baseline.md`,
  `docs/matching-regression.md`, `tests/test_matching_regression.py`,
  `tests/fixtures/matching/manifest.json`,
  `src/audio_cue_locator/infrastructure/media_processing/canonical_audio.py`
  ou `src/audio_cue_locator/infrastructure/media_processing/ffmpeg_adapter.py`:
  este documento exercita a implementação existente, não a modifica.
- Resolução da verificação de probing/decodificação ao vivo de M1 em si
  (seção 2); apenas seu status e impacto de bloqueio são registrados aqui.
- Reuso das fixtures de mídia M1-era existentes em `tests/fixtures/`
  (seção 8).
- Famílias de matching avançadas, garantias de robustez não medidas,
  mudanças silenciosas de canonicalização e um programa de dataset amplo.

## Referências

- `docs/matching-contract.md` — contrato interno do matcher (M2-01); este
  documento exercita, e não redefine, suas seções 1–5.
- `docs/matching-baseline.md` — implementação numérica e semântica de
  score/timestamp (M2-02); este documento exercita, e não redefine, suas
  seções 1–7.
- `docs/matching-regression.md` — evidência de regressão sintética e
  critério de precisão de um período de amostra (M2-03); este documento
  exercita, e não redefine, seu critério de precisão.
- `src/audio_cue_locator/infrastructure/acoustic_matching/baseline.py` —
  implementação exercitada por este conjunto de evidências.
- `src/audio_cue_locator/infrastructure/media_processing/canonical_audio.py`
  — `CANONICAL_AUDIO_SPEC`, `PENDING_LIVE_PROBING_VERIFICATION`.
- `src/audio_cue_locator/infrastructure/media_processing/ffmpeg_adapter.py`
  — único adaptador autorizado para `ffmpeg`/`ffprobe`; não modificado nem
  estendido por este documento.
- `tests/test_canonicalization_fixtures.py` — precedente de documentação de
  proveniência de fixture e da convenção skip-if-unavailable.
- `tests/fixtures/matching/representative-manifest.json` — fixtures
  descritas por este documento.
- `tests/test_matching_robustness.py` — suíte de robustez que afirma os
  valores esperados registrados neste documento.
- `issues/M2/M2-04/formal-issue.json` — especificação formal desta issue.
- `intents/M2/M2-04/implementation-handoff.json` — handoff que autorizou e
  restringiu o escopo desta implementação.
