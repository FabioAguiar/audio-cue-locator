# Matching Benchmark

## Finalidade

Este documento descreve o benchmark inicial de custo de execução do matcher
de cue única já existente e inalterado (M2-02, `match_cue`), sobre uma
entrada canônica muito mais longa do que qualquer fixture atualmente
existente no repositório, para sustentar — ou adiar, com justificativa
explícita — uma comparação futura entre busca direta e busca acelerada por
FFT (M2-06).

Ele **não redefine** `docs/matching-contract.md`, `docs/matching-baseline.md`,
`docs/matching-robustness.md` ou `docs/matching-acceptance.md`, e **não
modifica** `src/audio_cue_locator/infrastructure/acoustic_matching/baseline.py`
nem `acceptance.py`: mede o comportamento já fixado por esses documentos e
módulos, não o redefine.

Ferramenta: `benchmarks/matching_baseline.py`.
Issue formal: `issues/M2/M2-06/formal-issue.json`.

## 1. Escopo e Fronteiras

- Mede exclusivamente o custo de execução da chamada já existente a
  `match_cue`, com a `EffectiveConfiguration` padrão inalterada de
  `baseline.py` (`DEFAULT_CONFIGURATION`). Geração de entrada,
  canonicalização/normalização, construção de configuração e emissão de
  relatório ocorrem fora da janela cronometrada (seção 3).
- **Não** implementa, inclui ou depende de um método de busca acelerado por
  FFT. Caso a avaliação da seção 6 recomende investigar FFT, essa
  implementação pertence a uma issue futura separadamente escopada — nunca a
  este benchmark, que permanece apenas uma ferramenta de medição do método
  direto já existente.
- **Não** altera o identificador de método (`normalized_cross_correlation_v1`),
  os parâmetros de `DEFAULT_CONFIGURATION`, nem qualquer uma das quatro
  alternativas de resultado do contrato interno do matcher
  (`docs/matching-contract.md`, seção 5).
- Custo de execução é reportado **separadamente** da qualidade de detecção
  (outcome, score, timestamp). Um número de tempo nunca é usado para inferir
  ou alterar a interpretação do score, que continua sendo uma similaridade
  específica do método, não uma confidence calibrada
  (`docs/matching-contract.md`, seção 4).
- Nenhum threshold universal, calibração de confidence ou comparabilidade
  entre métodos é introduzida ou perturbada por este documento
  (`docs/matching-acceptance.md` permanece a única fonte da política de
  aceitação; este benchmark não a consulta nem depende dela).
- `benchmarks/` é um diretório de nível superior novo, isolado
  deliberadamente de `src/` (produção) e `tests/` (regressão/robustez
  automatizada): `benchmarks/matching_baseline.py` nunca é coletado ou
  executado por `pytest`, e não faz parte do pacote instalável definido em
  `pyproject.toml` (`[tool.setuptools.packages.find] where = ["src"]`).

### 1.1. Execução não realizada durante a autoria desta issue

A execução deste harness para produzir os números de tempo reais que os
critérios de aceite 1 e 2 da issue formal exigem é uma etapa autorizada
separadamente da autoria da ferramenta
(`intents/M2/M2-06/implementation-handoff.json`, `scope_notes`, gap G8): esta
issue autora o cenário, a metodologia e a estrutura do relatório; os
números efetivos de medição (seção 5) só são preenchidos após uma execução
autorizada por uma fase de controle/execução ASF subsequente, mediante uma
atualização controlada deste mesmo documento. Nenhuma alegação de conclusão
é feita antes de essa evidência medida existir.

Durante a autoria, apenas verificações estruturais privadas (importação do
módulo, geração determinística de arrays em escala reduzida, verificação de
formas/dtypes/hashes e do fluxo de aborto por orçamento de tempo) foram
realizadas para reduzir o risco de erros no harness — não uma execução do
cenário de 60 segundos autorado, e não um `pytest run`, seguindo o mesmo
precedente já registrado por `tests/fixtures/matching/representative-manifest.json`
("a private computation, not a pytest run").

## 2. Cenário do Benchmark

As únicas fixtures de mídia representativa hoje existentes
(`tests/fixtures/matching/representative-manifest.json`, M2-04) usam fontes
de 1920 amostras (40 ms a 48000 Hz) — curtas demais para produzir um sinal
de tempo significativo ou para exercitar um regime de duração em que uma
troca direto/FFT pudesse plausivelmente importar. Este benchmark constrói,
portanto, um cenário sintético dedicado, deliberadamente distinto das
fixtures de qualidade de M2-03/M2-04 e não reutilizando seus arrays:

| Parâmetro | Valor |
|---|---|
| Taxa de amostragem | 48000 Hz (`CANONICAL_AUDIO_SPEC.sample_rate_hz`, inalterada) |
| Duração do source | 60,000 segundos (2.880.000 amostras) |
| Duração da cue | 0,500 segundos (24.000 amostras) |
| Posição de inserção da cue | 30,000 segundos (índice de amostra 1.440.000) |
| Seed do gerador | `206` (fixo) |
| Gerador | `numpy.random.default_rng(seed).uniform(-1.0, 1.0, size=n)` |

Cue e source de fundo (`background`) são gerados por sorteios
independentes de ruído branco de banda larga (broadband), não pela
construção harmônica com envelope de Hann usada por M2-04 — este cenário
representa duração/custo computacional, não qualidade de detecção em mídia
representativa. A cue é gerada primeiro e normalizada por pico de forma
independente (`CANONICAL_AUDIO_SPEC.normalization`: `method="peak"`,
`target_peak_amplitude=1.0`), exatamente como M1 canonicalizaria um ativo de
cue decodificado isoladamente. Ela é então embutida verbatim no source de
fundo, no índice de amostra correspondente a 30 segundos, e o array
combinado resultante é ele próprio normalizado por pico da mesma forma,
exatamente como M1 canonicalizaria um ativo de source decodificado
isoladamente. Como a amostra de pico da cue já normalizada (exatamente
`1.0`) é, com probabilidade esmagadora, o pico global do array combinado, o
fator de escala da normalização do source é esperado ser exatamente `1.0`
na prática — mas isso é calculado e reportado pelo script
(`source_peak_scale_factor` no relatório JSON), nunca assumido.

Toda a geração acima ocorre fora da janela cronometrada (seção 3). Os
hashes SHA-256 dos arrays canônicos finais (`source_sha256`, `cue_sha256`
no relatório) permitem verificar, byte a byte, que uma reexecução usa
exatamente os mesmos dados de entrada.

### 2.1. Estimativa estática de custo (não é uma medição)

A correlação direta que `baseline.py` usa deliberadamente
(`numpy.correlate(..., mode="valid")`) tem custo
`O((len(source) - len(cue) + 1) * len(cue))` multiplicações-acumulações.
Para este cenário, isso é:

```text
(2.880.000 - 24.000 + 1) * 24.000 = 68.544.024.000
```

≈ 6,85 × 10¹⁰ operações de multiplicação-acumulação por chamada de
`match_cue`, além do custo `O(N)` do somatório de prefixos para a energia
de janela. Este número é uma estimativa analítica de complexidade,
calculável sem executar o benchmark; ele **não é** uma medição de tempo de
parede e não substitui a seção 5.

## 3. Metodologia de Medição

- **Escopo cronometrado**: apenas a chamada
  `match_cue(source, cue, DEFAULT_CONFIGURATION)` em si, incluindo todo o
  seu processamento interno. Importações, geração de entrada,
  canonicalização/normalização, construção de configuração e emissão de
  relatório ficam fora da janela cronometrada.
- **Cronômetro**: `time.perf_counter_ns()`.
- **Repetições**: uma repetição de aquecimento (warm-up) não cronometrada,
  seguida de cinco repetições cronometradas sequenciais sobre os mesmos
  arrays (mesma referência de objeto, sem regeneração entre repetições).
- **Tratamento estatístico**: contagem, mediana, mínimo e máximo das cinco
  durações medidas (nanossegundos e milissegundos).
- **Consistência entre repetições**: como `match_cue` é uma função pura de
  suas entradas, outcome, score e timestamp (ou diagnostic_best_score /
  diagnostic_best_timestamp_seconds, no caso `no_match`) são verificados
  como idênticos entre as cinco repetições; essa verificação é reportada
  separadamente do custo de tempo (seção 1).
- **Orçamento de aborto**: um orçamento configurável de tempo total
  acumulado (`--max-total-seconds`, padrão 600s) é verificado entre
  repetições inteiras (uma chamada individual não pode ser interrompida em
  andamento). Se excedido, a execução é interrompida imediatamente e
  reportada como `status: "incomplete"`, com o motivo explícito e apenas as
  repetições que de fato completaram — nenhum valor é fabricado, estimado
  ou usado para substituir silenciosamente uma carga de trabalho menor.

## 4. Metadados de Ambiente Registrados

Cada execução registra, no relatório JSON emitido:

- horário UTC de início da execução;
- revisão do repositório (HEAD, branch, se a árvore de trabalho está suja),
  obtida via `git`; marcada `"unavailable"` quando `git` não está disponível
  ou o diretório não é uma árvore de trabalho git;
- hash SHA-256 do próprio script do benchmark e dos arrays de entrada
  canônicos (source e cue);
- sistema operacional (nome, release, versão) e arquitetura (`platform`);
- modelo de CPU, quando disponível (`platform.processor()` ou, no Linux,
  `/proc/cpuinfo`); contagem de CPUs lógicas (`os.cpu_count()`);
- versão do Python, do NumPy e do SciPy;
- nome do cronômetro usado e número de repetições;
- configuração efetiva completa (`method`, `acceptance_threshold`) e
  `CANONICAL_AUDIO_SPEC` completo;
- variáveis de ambiente de threading de bibliotecas numéricas
  (`OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS`,
  `NUMEXPR_NUM_THREADS`, `VECLIB_MAXIMUM_THREADS`), com uma nota explícita
  de que a correlação direta usada por `match_cue` é um laço C escalar, não
  uma operação baseada em BLAS ou multi-thread — essas variáveis são
  registradas por completude, não porque se espere que afetem esta chamada
  específica;
- uma nota explícita de que nenhum controle de afinidade de CPU, prioridade
  de processo ou uso exclusivo de máquina foi aplicado, e que carga de fundo
  no host de execução não é controlada nem medida por este script.

Nenhum metadado indisponível é omitido silenciosamente; campos
indisponíveis são marcados como `"unavailable"` explicitamente.

## 5. Resultados

**Status: NÃO MEDIDO.**

Esta seção é intencionalmente um placeholder. Conforme a seção 1.1, a
execução autorizada de `benchmarks/matching_baseline.py` pertence a uma
fase de controle/execução ASF subsequente à implementação desta issue, não
à autoria desta issue. Após essa execução autorizada, esta seção deve ser
substituída por uma atualização controlada deste mesmo documento (mesmo
caminho, `docs/matching-benchmark.md`), contendo no mínimo:

- os metadados de ambiente da seção 4, preenchidos pela execução real;
- a tabela de durações por repetição (índice, nanossegundos,
  milissegundos) e as estatísticas (contagem, mediana, mínimo, máximo);
- `status` da execução (`"complete"` ou `"incomplete"`, com motivo, se
  aplicável — seção 3);
- o resultado de detecção (`outcome`, `score`, `timestamp_seconds` ou os
  campos diagnósticos de `no_match`), reportado separadamente das
  durações;
- a confirmação de `result_consistent_across_repetitions`.

Até que essa atualização exista, nenhuma alegação de custo medido, de
escala não medida, ou de adequação/inadequação do método direto pode ser
feita a partir deste documento.

## 6. Avaliação Busca Direta vs. FFT

**Status: adiada, pendente da evidência medida da seção 5** (issue formal
M2-06, seção 4, "Não inclui": não há implementações duplas obrigatórias;
`intents/M2/M2-06/implementation-handoff.json`, gap G2: usar a latência
realmente medida para justificar o adiamento ou recomendar uma comparação
FFT separadamente escopada).

Nenhuma implementação de busca acelerada por FFT foi autorada por esta
issue (seção 1). O que esta seção define agora é o **critério** pelo qual a
decisão será tomada assim que a seção 5 for preenchida, não a decisão em
si:

- Se a mediana medida do custo de `match_cue` sobre o cenário da seção 2
  for pequena o suficiente para os usos previstos de Analysis (ainda não
  definidos numericamente por nenhuma issue de M2), documentar aqui o
  adiamento explícito de uma implementação FFT, com a mediana medida como
  evidência.
- Se a mediana medida indicar um custo que comprometeria plausivelmente o
  uso pretendido em fontes desta ordem de duração, recomendar aqui uma
  issue futura, separadamente escopada, para avaliar uma busca acelerada
  por FFT — nunca implementá-la dentro deste benchmark ou como um segundo
  `EffectiveConfiguration.method` de produção não formalmente adotado
  (`intents/M2/M2-06/implementation-handoff.json`, risco de arquitetura
  registrado para uma eventual implementação FFT).
- Em nenhum dos dois casos esta seção pode alegar uma aceleração de FFT não
  medida, uma SLA universal, ou a adequação do método direto para uma
  escala além da efetivamente medida (issue formal M2-06, seção 8, critério
  "Results declare limitations and do not promise unmeasured scale").

## 7. Limitações

- O cenário é sintético (ruído branco de banda larga), não mídia
  representativa real decodificada por FFmpeg: `canonical_audio.py`
  (`PENDING_LIVE_PROBING_VERIFICATION`) já registra que a verificação de
  probing/decodificação ao vivo permanece pendente e herdada de M1; este
  benchmark não a resolve e não certifica custo de decodificação real,
  apenas o custo do `match_cue` já canonicalizado.
- O benchmark mede uma única combinação de duração de source (60s) e de cue
  (0,5s); não generaliza para outras combinações sem nova medição.
- A carga do host de execução (outros processos, throttling térmico,
  virtualização) não é controlada nem medida; os valores de mínimo/mediana
  reportados refletem apenas as cinco repetições sequenciais efetivamente
  executadas na máquina e no momento registrados nos metadados de ambiente
  (seção 4), não uma garantia de desempenho em qualquer outro ambiente.
- Nenhuma conclusão de escala não medida (fontes muito mais longas, muitas
  cues simultâneas, execução concorrente) pode ser inferida deste
  benchmark; essas condições permanecem fora de escopo (issue formal M2-06,
  seção 4, "Não inclui").

## Referências

- `docs/matching-contract.md` — contrato interno do matcher (M2-01); este
  documento mede, e não redefine, seu comportamento observável.
- `docs/matching-baseline.md` — implementação numérica de referência
  (M2-02) cujo custo este documento mede.
- `docs/matching-robustness.md`, `docs/matching-acceptance.md` — evidência
  de qualidade/aceitação (M2-04/M2-05), deliberadamente não consultada por
  este benchmark de custo.
- `src/audio_cue_locator/infrastructure/acoustic_matching/baseline.py` —
  `match_cue`, `DEFAULT_CONFIGURATION` (inalterados).
- `src/audio_cue_locator/infrastructure/media_processing/canonical_audio.py`
  — `CANONICAL_AUDIO_SPEC` (inalterado).
- `benchmarks/matching_baseline.py` — implementação do harness descrito
  aqui.
- `issues/M2/M2-06/formal-issue.json`,
  `intents/M2/M2-06/implementation-handoff.json` — especificação formal e
  handoff de implementação desta issue.
