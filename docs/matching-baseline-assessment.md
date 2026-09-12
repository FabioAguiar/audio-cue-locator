# Matching Baseline Assessment

## Finalidade

Este documento consolida a evidência já produzida por M2-01 a M2-06 em uma
única decisão técnica de adequação do baseline de matching de cue única e
uma recomendação rastreável de continuidade para M3, exigida por
`issues/M2/M2-07/formal-issue.json`: a ligação de cada item de Definition of
Done / Evidência Mínima de M2 (`docs/milestones.md`) a um artefato de
evidência específico de M2-01..M2-06 ou a uma lacuna explicitamente
registrada, um resumo do método, parâmetros, precisão de timing, score,
no-match, robustez e custo computacional com suas limitações já divulgadas,
e uma decisão explícita de adequação/inadequação/adequação-condicional do
baseline para sustentar M3.

Este documento **não redefine** nenhuma decisão já fixada por
`docs/matching-contract.md` (M2-01), `docs/matching-baseline.md` (M2-02),
`docs/matching-regression.md` (M2-03), `docs/matching-robustness.md`
(M2-04), `docs/matching-acceptance.md` (M2-05) ou `docs/matching-benchmark.md`
(M2-06): a taxonomia de quatro resultados, a semântica de timestamp, a
semântica de score, o método (correlação cruzada normalizada), o critério de
precisão, o threshold derivado e o escopo do benchmark descritos ali são
consolidados e referenciados aqui, não alterados. Em caso de qualquer
aparente divergência, esses seis documentos prevalecem; este documento é o
registro da decisão de adequação e continuidade construída sobre eles, não
uma sétima fonte de verdade técnica sobre o método.

Este documento **não implementa** nenhuma parte de M3, **não fecha nem
reabre** `docs/project-status/milestone-state.json`, e **não inventa**
nenhum resultado de benchmark ou de teste automatizado não efetivamente
produzido. Nenhum Implementation Map é criado por este documento, conforme
`docs/milestones.md` — M2, Documentação da Implementação
(`milestones-only`).

## 1. Visão Geral dos Documentos Consolidados

| Issue | Documento | Contribuição |
|---|---|---|
| M2-01 | `docs/matching-contract.md` | Contrato interno: pré-condições, configuração efetiva, timestamp, score, quatro alternativas de resultado. |
| M2-02 | `docs/matching-baseline.md` | Implementação numérica (correlação cruzada normalizada, `normalized_cross_correlation_v1`), faixa/direção de score, tratamento determinístico de entradas degeneradas. |
| M2-03 | `docs/matching-regression.md` | Evidência de regressão sintética determinística, critério de precisão de timing (um período de amostra). |
| M2-04 | `docs/matching-robustness.md` | Evidência de robustez sobre mídia representativa sintética (amplitude, ruído, discriminação no_match), status da verificação FFmpeg/FFprobe. |
| M2-05 | `docs/matching-acceptance.md` | Política de aceitação baseada em evidência: threshold derivado (`≈0.7056698933077521`), casos de fronteira. |
| M2-06 | `docs/matching-benchmark.md` | Cenário e metodologia de benchmark de custo computacional; harness autorado, resultados ainda **NÃO MEDIDOS**. |

Todos os seis documentos foram reconfirmados existentes e byte-a-byte
inalterados em relação aos hashes SHA-256 já registrados por
`states/M2/M2-07/issue-operational-state.json` e por
`context-packs/M2/M2-07/implementation-context-pack.json` no momento da
autoria deste documento, assim como `docs/vision.md`, `docs/architecture.md`,
`docs/milestones.md` e
`src/audio_cue_locator/infrastructure/media_processing/canonical_audio.py`.

## 2. Rastreabilidade — Definition of Done de M2 (`docs/milestones.md`)

Cada item abaixo é o texto literal de `docs/milestones.md` — M2, Definition
of Done.

### 2.1. "uma cue conhecida é localizada em fixtures controladas dentro da precisão aceita para o baseline"

**Status: satisfeito, com verificação analítica — não por execução pytest.**

- `docs/matching-regression.md` §1 define o critério de precisão
  (`1 / 48000 ≈ 2.0833333333333333e-05` segundos, derivado do próprio
  algoritmo, não inventado) e §3 registra dois casos `found_offset_near_*`
  com erro absoluto de timing esperado de `0.0` segundos.
- `docs/matching-robustness.md` §5 registra três casos `found_representative_*`
  adicionais, todos com timestamp exato no offset conhecido (amostra 800).
- Ambos os conjuntos foram verificados por inspeção matemática direta
  (M2-03) e por invocação direta do interpretador em tempo de autoria
  (M2-04), não por uma execução da suíte `pytest`. Ver item 6 abaixo e a
  lacuna de "testes automatizados passando" (seção 3.6).

### 2.2. "o matcher retorna score com semântica explicitamente documentada"

**Status: satisfeito.**

- `docs/matching-contract.md` §4 fixa a semântica (similaridade
  method-specific, nunca confidence calibrada, não comparável entre
  métodos).
- `docs/matching-baseline.md` §4 fixa a faixa e direção concretas
  (`[-1.0, 1.0]`) para `normalized_cross_correlation_v1`.

### 2.3. "no-match é representado de forma inequívoca"

**Status: satisfeito.**

- `docs/matching-contract.md` §5.2 distingue `no_match` de `invalid_input` e
  `processing_failure`.
- `docs/matching-baseline.md` §5 (tabela) fixa o tratamento determinístico de
  entradas degeneradas.
- `docs/matching-regression.md` §3 e `docs/matching-robustness.md` §5
  registram casos `no_match` verificados, incluindo discriminação genuína
  (não apenas construção de soma-zero).

### 2.4. "existe uma política inicial de threshold ou decisão equivalente"

**Status: satisfeito.**

- `docs/matching-acceptance.md` §2 deriva `acceptance_threshold ≈
  0.7056698933077521` por uma regra de margem explícita (ponto médio entre o
  maior score `no_match` e o menor score `found` já observados), não uma
  calibração estatística. §4 verifica o comportamento de igualdade na
  fronteira.

### 2.5. "desempenho é medido em pelo menos um cenário representativo"

**Status: não satisfeito — lacuna aberta (G1).**

- `docs/matching-benchmark.md` define um cenário representativo completo
  (source de 60 s, cue de 0,5 s, 48000 Hz) e uma metodologia de medição
  detalhada (§§2–4), mas sua seção 5 ("Resultados") é um placeholder
  explícito: **"Status: NÃO MEDIDO."** A execução autorizada do harness
  (`benchmarks/matching_baseline.py`) pertence a uma fase de
  controle/execução ASF subsequente, ainda não realizada.
- Apenas uma estimativa estática de custo analítico existe hoje
  (`docs/matching-benchmark.md` §2.1: aproximadamente `6,8544024 × 10¹⁰`
  operações de multiplicação-acumulação por chamada), e ela **não é** uma
  medição de tempo de parede.
- Este item permanece uma lacuna explícita deste documento (ver seção 5),
  não uma conclusão satisfeita por default.

### 2.6. "testes de regressão cobrem correspondência positiva, negativa e casos limítrofes selecionados"

**Status: cobertura autorada e analiticamente verificada — execução pytest não confirmada.**

- Cobertura positiva: `found_offset_near_start`/`found_offset_near_end`
  (M2-03), `found_representative_amplitude_*`/`found_representative_noise_*`
  (M2-04).
- Cobertura negativa: `no_match_absent_cue`, `no_match_silent_cue`,
  `no_match_silent_source`, `no_match_source_shorter_than_cue` (M2-03),
  `no_match_representative_absent_cue` (M2-04).
- Cobertura de casos limítrofes: `test_borderline_case_just_above_the_evidence_based_threshold_is_found`
  e `test_borderline_case_just_below_the_evidence_based_threshold_is_no_match`
  (M2-05 §5), construídos por decomposição ortogonal analiticamente
  previsível.
- As quatro suítes (`tests/test_matching_baseline.py`,
  `tests/test_matching_regression.py`, `tests/test_matching_robustness.py`,
  `tests/test_matching_acceptance.py`) existem e, por inspeção de código,
  estruturalmente cobrem essas três categorias. Nenhuma delas, porém, foi
  confirmada passando por uma execução `pytest` real neste repositório até a
  autoria deste documento (ver seção 3.6 e seção 5, lacuna correspondente a
  "testes automatizados passando").

### 2.7. "limitações relevantes estão registradas"

**Status: satisfeito.**

- Cada um dos seis documentos M2-01..M2-06 mantém uma seção própria "Fora de
  Escopo"/"Limitações Reconhecidas" (por exemplo
  `docs/matching-robustness.md` §10, `docs/matching-benchmark.md` §7). A
  seção 6 deste documento consolida essas limitações.

### 2.8. "o baseline é considerado adequado para sustentar o modelo de Analysis ou sua inadequação gera decisão arquitetural explícita antes de prosseguir"

**Status: decidido por este documento — ver seção 8.**

Este é o critério que este próprio documento existe para satisfazer; ele não
pode ser marcado como satisfeito por nenhum artefato de M2-01..M2-06
individualmente, já que nenhum deles produz uma decisão de adequação de
escopo de milestone.

## 3. Rastreabilidade — Evidência Mínima de M2 (`docs/milestones.md`)

Cada item abaixo é o texto literal de `docs/milestones.md` — M2, Evidência
Mínima.

### 3.1. "resultados automatizados de fixtures com timestamps conhecidos"

**Status: satisfeito, com a mesma ressalva de verificação da seção 2.1.**
Fonte: `docs/matching-regression.md` (`tests/fixtures/matching/manifest.json`),
`docs/matching-robustness.md`
(`tests/fixtures/matching/representative-manifest.json`).

### 3.2. "benchmark inicial"

**Status: parcialmente satisfeito — ferramenta e metodologia existem; medição não existe (G1, mesma lacuna da seção 2.5).**
Fonte: `docs/matching-benchmark.md`, `benchmarks/matching_baseline.py`.

### 3.3. "comparação entre casos positivos e negativos"

**Status: satisfeito.**
Fonte: `docs/matching-regression.md` §3, `docs/matching-robustness.md` §5.

### 3.4. "evidência de comportamento sob pelo menos algumas perturbações representativas"

**Status: satisfeito, com a perturbação de compressão explicitamente omitida.**
Fonte: `docs/matching-robustness.md` §3 (amplitude 1.0/0.5/0.25; ruído gaussiano
aditivo em dois níveis de SNR); §3.2 registra a perturbação de compressão
como não avaliada (`ffmpeg`/`ffprobe` indisponíveis).

### 3.5. "registro do método, parâmetros e limitações"

**Status: satisfeito.**
Fonte: `docs/matching-baseline.md` §§1–2 (método, configuração efetiva),
seções "Fora de Escopo"/"Limitações" de todos os seis documentos
(consolidadas na seção 6 deste documento).

### 3.6. "testes automatizados passando"

**Status: não satisfeito — lacuna aberta (G2, distinta e mais fraca que a verificação já alcançada).**

- Nenhuma das quatro suítes de teste de M2
  (`tests/test_matching_baseline.py`, `tests/test_matching_regression.py`,
  `tests/test_matching_robustness.py`, `tests/test_matching_acceptance.py`)
  foi executada via `pytest` por nenhuma implementação registrada pelo ASF
  até a autoria deste documento.
- Todo valor esperado registrado pelos documentos M2-02 a M2-05 foi
  verificado apenas por **invocação direta do interpretador em tempo de
  autoria** ou por **inspeção matemática/analítica direta** das fixtures —
  um nível de evidência real, mas distinto e mais fraco do que "passando sob
  uma execução `pytest` real", que é o que `docs/milestones.md` literalmente
  exige.
- Este documento trata essas duas categorias de evidência como
  **explicitamente distintas** e não as conflacia: "valores esperados
  verificados por invocação direta do interpretador/análise em tempo de
  autoria" (alcançado, por M2-02..M2-05) versus "testes automatizados
  passando sob execução `pytest`" (não alcançado; lacuna G2 desta seção).

## 4. Resumo por Dimensão

| Dimensão | Estado | Fonte primária |
|---|---|---|
| Método | Correlação cruzada normalizada (`normalized_cross_correlation_v1`), sem FFT, O((len(source)-len(cue)+1)*len(cue)) multiplicações-acumulações. | `docs/matching-baseline.md` §1 |
| Parâmetros | `DEFAULT_CONFIGURATION.acceptance_threshold=0.75` (provisório, não calibrado); `EVIDENCE_BASED_CONFIGURATION.acceptance_threshold≈0.7056698933077521` (derivado por margem explícita). | `docs/matching-baseline.md` §2, `docs/matching-acceptance.md` §§2–3 |
| Precisão de timing | Erro absoluto de timing esperado `0.0` s nos casos `found` avaliados; critério de aceitação de um período de amostra (`≈2.08e-5` s), derivado do próprio algoritmo, não medido empiricamente em mídia real. | `docs/matching-regression.md` §1 |
| Score | Similaridade específica do método em `[-1.0, 1.0]`; nunca confidence calibrada; não comparável entre métodos. | `docs/matching-contract.md` §4, `docs/matching-baseline.md` §4 |
| No-match | Distinto de `invalid_input`/`processing_failure`; discriminação genuína verificada (score de diagnóstico `≈0.473` vs. threshold `≈0.706`). | `docs/matching-contract.md` §5.2, `docs/matching-robustness.md` §5 |
| Robustez | Invariante a escala de amplitude (1.0/0.5/0.25, score `1.0` em todos); degrada de forma previsível sob ruído gaussiano aditivo (`0.993`/`0.938` nos dois níveis de SNR avaliados); perturbação de compressão não avaliada. | `docs/matching-robustness.md` §§3, 5 |
| Custo computacional | Estimativa estática: `≈6,85 × 10¹⁰` operações por chamada no cenário de 60 s; **nenhuma medição de tempo de parede existe** (Status: NÃO MEDIDO). | `docs/matching-benchmark.md` §§2.1, 5 |

## 5. Lacunas Abertas e Não Resolvidas

### G1 — Custo computacional não medido

`docs/matching-benchmark.md` autora o cenário, a metodologia e a estrutura
do relatório, mas sua seção 5 permanece um placeholder explícito ("NÃO
MEDIDO"); a execução do harness (`benchmarks/matching_baseline.py`) que
produziria os números reais pertence a uma fase de controle/execução ASF
subsequente (`intents/M2/M2-06/implementation-handoff.json`, gap G8),
ainda não realizada. Por decisão explícita deste documento
(`intents/M2/M2-07/implementation-handoff.json`, scope_notes G1), esta
lacuna **não bloqueia** a decisão de adequação da seção 8: ela é registrada
aqui como um gap aberto, e a estimativa estática de `docs/matching-benchmark.md`
§2.1 **nunca** substitui uma conclusão de custo medido.

### G2 — Nenhuma execução `pytest` real das quatro suítes de teste de M2

Nenhuma das quatro suítes de teste de M2 foi executada via `pytest` por
nenhuma implementação registrada pelo ASF até a autoria deste documento.
Todo valor esperado foi verificado apenas por invocação direta do
interpretador/análise matemática em tempo de autoria (ver seção 3.6). Por
decisão explícita deste documento
(`intents/M2/M2-07/implementation-handoff.json`, scope_notes G3), esta
lacuna também **não bloqueia** a decisão de adequação da seção 8, mas é
registrada como gap aberto: "testes automatizados passando" (Evidência
Mínima de M2) permanece, estritamente, não satisfeito até uma execução
`pytest` real ocorrer.

Ambas as lacunas G1 e G2 exigem uma fase de controle/execução ASF futura e
separadamente autorizada para serem encerradas; nenhuma delas é encerrada
por este documento.

## 6. Limitações Já Registradas — Consolidação M2-01 a M2-06

- **M2-01** (`docs/matching-contract.md` §7): verificação de
  probing/decodificação ao vivo (ffmpeg/ffprobe) de mídia representativa
  permanece pendente, herdada de M1; múltiplas cues, múltiplas ocorrências
  completas, persistência, execução assíncrona, API e WebUI fora de escopo.
- **M2-02** (`docs/matching-baseline.md` §7): `acceptance_threshold=0.75`
  é um corte técnico provisório, não calibrado, sujeito a revisão (revisado
  por M2-05); nenhuma otimização de performance (FFT, paralelização) sem
  necessidade medida.
- **M2-03** (`docs/matching-regression.md` §6): fixtures puramente
  sintéticas e arbitrárias, não mídia real decodificada; critério de
  precisão derivado da resolução do próprio algoritmo, não de uma medição
  empírica independente em mídia representativa.
- **M2-04** (`docs/matching-robustness.md` §10): conjunto ainda sintético
  (rajada harmônica), não mídia real decodificada; perturbação de
  compressão não avaliada (ffmpeg/ffprobe indisponíveis, e o adaptador
  existente não expõe codificação com perda); amostra pequena e curada, não
  um programa de dataset amplo.
- **M2-05** (`docs/matching-acceptance.md` §6): threshold derivado de
  evidência pequena e curada (seis casos sintéticos + seis casos
  representativos); não generalizável além do método
  `normalized_cross_correlation_v1` nem além dos tipos de cue já
  evidenciados; nenhuma calibração estatística de confiança é reivindicada.
- **M2-06** (`docs/matching-benchmark.md` §7): cenário sintético (ruído
  branco de banda larga), não mídia representativa real; mede apenas uma
  combinação de duração (60 s de source, 0,5 s de cue); carga do host de
  execução não controlada nem medida; nenhuma conclusão de escala não
  medida é autorizada.
- **Cross-cutting**: `ffmpeg` e `ffprobe` permaneceram confirmados
  indisponíveis em todo o ambiente de implementação de M1 a M2-07; nenhuma
  evidência de M2-01 a M2-06 jamais exercitou mídia real decodificada —
  toda ela é construída a partir de arrays sintéticos já em forma canônica.
  Isso é o pré-requisito herdado de M1
  (`canonical_audio.py`, `PENDING_LIVE_PROBING_VERIFICATION`) que permanece
  não resolvido por nenhum issue de M2, incluindo este.

## 7. Escopo da Evidência — Apenas Sintética

Toda conclusão deste documento, incluindo a decisão de adequação da seção 8
e a recomendação de continuidade da seção 9, é **explicitamente escopada à
evidência sintética já produzida por M2-01 a M2-06**, consistente com
`canonical_audio.py`'s própria `PENDING_LIVE_PROBING_VERIFICATION` e com a
divulgação equivalente já presente em cada um dos seis documentos
predecessores (seção 6). Nenhuma conclusão deste documento é generalizada
para desempenho ou precisão em mídia real decodificada; essa generalização
exigiria a verificação de probing/decodificação ao vivo ainda pendente, não
resolvida por este documento.

## 8. Decisão de Adequação do Baseline M2

**Decisão: adequação condicional, pendente de evidência (conditionally
adequate, pending evidence).**

O contrato interno (M2-01), a implementação numérica e a semântica de
score/timestamp (M2-02), a evidência de regressão determinística (M2-03), a
evidência de robustez sob perturbação de amplitude e ruído (M2-04), e a
política de aceitação baseada em evidência (M2-05) formam, juntos, um
conjunto coerente e rastreável que satisfaz seis dos oito itens de
Definition of Done de M2 e quatro dos seis itens de Evidência Mínima de M2
(seções 2 e 3), dentro do escopo explicitamente sintético registrado na
seção 7.

Esta adequação é **condicional**, não plena, porque dois itens
permanecem abertos e não podem ser marcados satisfeitos sem inventar
evidência que não existe (seções 2.5, 2.6, 3.2, 3.6; gaps G1 e G2, seção 5):

- o custo computacional em um cenário representativo não foi efetivamente
  medido (apenas estimado analiticamente);
- nenhuma das quatro suítes de teste de M2 foi confirmada passando sob uma
  execução `pytest` real.

Nenhum dos dois gaps indica, pela evidência disponível, um problema de
**qualidade** do método (precisão de timing, score, discriminação no-match e
robustez a amplitude/ruído já estão evidenciados e são consistentes com as
expectativas analíticas da seção 4). Eles são, em vez disso, lacunas de
**evidência de execução** ainda não produzida: a estimativa estática de
custo (`docs/matching-benchmark.md` §2.1) não indica, por si, nenhum
indício de custo proibitivo para os usos de Analysis ainda não definidos
numericamente por M3; e a verificação analítica/interpretador já realizada
para os valores esperados de M2-02 a M2-05 é consistente entre si e com as
relações matemáticas esperadas (por exemplo, a aproximação analítica de
degradação de score sob ruído em `docs/matching-robustness.md` §5 confere
com os valores registrados com discrepância inferior a `0.001`).

Por isso, este documento **não** conclui inadequação do baseline nem exige
uma revisão arquitetural antes de M3 (`docs/milestones.md` — M2, Notas de
Continuidade, caso negativo). Este documento também **não** declara
adequação plena e incondicional, para não presentear como satisfeitos dois
itens de Definition of Done / Evidência Mínima cuja evidência real ainda não
existe.

## 9. Recomendação de Continuidade para M3

**Recomendação: continuidade é justificada agora, com os gaps G1 e G2
explicitamente herdados como pré-condições de fechamento, não como
bloqueios à abertura de M3.**

`docs/milestones.md` — M3, Dependências, declara literalmente: "M2 concluída
com matcher considerado adequado para o baseline." Este documento é o
artefato que, pela primeira vez, examina essa condição explicitamente; ele a
satisfaz **de forma condicional**, conforme a seção 8, não de forma
incondicional.

Implicações concretas para M3:

- M3 pode consumir o comportamento já validado do matcher (método,
  configuração efetiva, semântica de score/timestamp/no-match) sem
  redefinir sua matemática, exatamente como `docs/milestones.md` — M2,
  Notas de Continuidade já prescreve.
- M3 **não deve** assumir, sem verificação adicional, que o custo
  computacional do matcher é adequado para qualquer volume ou concorrência
  de uso que M3 venha a introduzir: essa suposição permanece não
  evidenciada (gap G1) até que `docs/matching-benchmark.md` §5 seja
  atualizado com uma medição real.
- M3 **não deve** apresentar as quatro suítes de teste de M2 como
  "passando" em qualquer relatório de evidência derivado, até que uma
  execução `pytest` real as confirme (gap G2).
- Nenhuma decisão de M3 deve generalizar as conclusões deste documento para
  mídia real decodificada (seção 7); isso permanece uma verificação herdada
  de M1, ainda pendente.
- Este documento não cria, nem autoriza M3 a criar, nenhum Implementation
  Map; a estratégia `milestones-only` permanece retida conforme
  `docs/milestones.md` — M2 e M3, Documentação da Implementação.

Fechamento recomendado dos gaps G1/G2, para uma futura fase de
controle/execução ASF, antes de qualquer issue de M3 depender
numericamente do custo ou da cobertura de teste do matcher:

1. Executar `benchmarks/matching_baseline.py` sob autorização explícita e
   atualizar `docs/matching-benchmark.md` §5 com os resultados medidos.
2. Executar `pytest` sobre as quatro suítes de teste de M2 sob autorização
   explícita e registrar o resultado real (passando ou não) em um artefato
   de evidência ASF subsequente.

## 10. Fora de Escopo Deste Documento

- Implementação de qualquer parte de M3.
- Fechamento ou reabertura automática de
  `docs/project-status/milestone-state.json`.
- Execução do benchmark (`benchmarks/matching_baseline.py`) ou de qualquer
  suíte de teste de M2; nenhum valor de custo medido ou de resultado de
  teste é produzido por este documento (seção 5).
- Redefinição da taxonomia de quatro resultados, da semântica de score, da
  convenção de timestamp, do método, da configuração efetiva ou da política
  de aceitação já fixados por M2-01 a M2-05.
- Qualquer alteração em
  `src/audio_cue_locator/infrastructure/acoustic_matching/baseline.py`,
  `src/audio_cue_locator/infrastructure/acoustic_matching/acceptance.py`,
  `benchmarks/matching_baseline.py`,
  `src/audio_cue_locator/infrastructure/media_processing/canonical_audio.py`
  ou em qualquer um dos seis documentos `docs/matching-*.md` predecessores:
  este documento consolida a evidência existente, não a modifica.
- Criação de um Implementation Map; a estratégia `milestones-only`
  permanece retida (seção 9).
- Resolução da verificação de probing/decodificação ao vivo de M1 em si
  (seção 7); apenas seu impacto de escopo é registrado aqui.
- Múltiplas cues, suporte completo a múltiplas ocorrências, Analysis
  persistence, SQLite, execução assíncrona, REST API, WebUI, waveform,
  calibração de confiança, aprendizado de máquina e processamento
  distribuído.

## Referências

- `docs/matching-contract.md` — contrato interno do matcher (M2-01).
- `docs/matching-baseline.md` — implementação numérica de referência (M2-02).
- `docs/matching-regression.md` — evidência de regressão sintética e
  critério de precisão (M2-03).
- `docs/matching-robustness.md` — evidência de robustez sobre mídia
  representativa sintética (M2-04).
- `docs/matching-acceptance.md` — política de aceitação baseada em
  evidência (M2-05).
- `docs/matching-benchmark.md` — cenário, metodologia e status do benchmark
  de custo computacional (M2-06).
- `docs/milestones.md` — M2 (Baseline de Matching Acústico), Definition of
  Done, Evidência Mínima, Notas de Continuidade; M3 (Modelo de Analysis e
  Resultados Estruturados), Dependências.
- `docs/architecture.md`, `docs/vision.md` — restrições arquiteturais e de
  visão preservadas por este documento.
- `src/audio_cue_locator/infrastructure/media_processing/canonical_audio.py`
  — `CANONICAL_AUDIO_SPEC`, `PENDING_LIVE_PROBING_VERIFICATION`.
- `controls/M2/M2-06/issue-iteration-completion.json` — confirma, no nível
  de estado do workflow ASF, que a medição do benchmark de M2-06 permanece
  pendente mesmo com a iteração de M2-06 encerrada.
- `issues/M2/M2-07/formal-issue.json` — especificação formal desta issue.
- `intents/M2/M2-07/implementation-handoff.json` — handoff que autorizou e
  restringiu o escopo desta implementação, incluindo a resolução das
  lacunas G1–G6 descritas em seu próprio `scope_notes`.
