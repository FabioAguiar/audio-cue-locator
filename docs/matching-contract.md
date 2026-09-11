# Matching Contract

## Finalidade

Este documento define o contrato interno do matcher de cue única (single-cue
matcher) para o Audio Cue Locator: as pré-condições de entrada, a associação
com a configuração efetiva, a semântica do timestamp de melhor
correspondência, a semântica do score e as alternativas de resultado que
qualquer implementação do matcher deve respeitar.

Ele existe para que uma implementação numérica futura (M2-02) e uma política
de aceitação/no-match futura (M2-05) possam ser desenvolvidas de forma
independente, e para que o matcher possa ser substituído ou estendido sem
alterar o contrato observável por Application e Core (docs/architecture.md,
Acoustic Matching: "A arquitetura deve permitir substituir ou adicionar
métodos sem alterar o contrato externo de Analysis.").

Este documento não implementa matching numérico, não fixa método, janela,
estratégia de busca ou threshold, não calibra score como confidence e não
altera a representação canônica de áudio definida em M1. Essas decisões
pertencem a M2-02 (implementação numérica) e M2-05 (política de
aceitação baseada em evidência), conforme docs/milestones.md — M2, Escopo
Núcleo e Issues Previstas.

## Posição no Fluxo

```text
source canonicalizado (Infrastructure, M1)
+
cue canonicalizada (Infrastructure, M1)
+
configuração efetiva (método + parâmetros do método)
→ matcher (Acoustic Matching, Infrastructure)
→ resultado do matcher (Core)
```

O matcher pertence à área Acoustic Matching (Infrastructure). O tipo de
resultado que ele produz pertence à semântica central do Core
(docs/architecture.md, Componentes ou Áreas Principais — Core: "score";
"resultado da análise"). Application coordena a chamada ao matcher, mas não
implementa lógica numérica (docs/architecture.md, Componentes — Application:
"Application não deve implementar algoritmos numéricos nem lógica HTTP.").

Nenhum tipo do Core definido por este contrato depende de NumPy, SciPy,
FFmpeg, HTTP, Pydantic-como-transporte ou SQLite (docs/architecture.md,
Princípios e Restrições #1). Uma implementação substituta do matcher pode
satisfazer este contrato inteiramente dentro de Infrastructure, sem que Core
precise conhecer a técnica usada.

## 1. Pré-condições de Entrada

O matcher não canonicaliza áudio. Antes de ser invocado, tanto o áudio de
origem (source) quanto a cue de referência devem já satisfazer o contrato
canônico definido em M1
(`src/audio_cue_locator/infrastructure/media_processing/canonical_audio.py`,
`CANONICAL_AUDIO_SPEC`):

- **sample_rate_hz**: 48000 Hz;
- **channels**: 1 (mono);
- **sample_format**: `float32`;
- **normalization**: normalização de pico habilitada (`method="peak"`,
  `target_peak_amplitude=1.0`), com entrada silenciosa preservada sem
  divisão por zero.

Este contrato **preserva** esses valores; ele não os redefine e não exige
nenhuma mudança em `canonical_audio.py`. Se um dia esses valores mudarem,
essa mudança pertence a uma revisão explícita e evidenciada de M1
(`canonical_audio.py`, `PENDING_LIVE_PROBING_VERIFICATION`), não a este
contrato.

Pré-condições adicionais que o matcher deve poder assumir sobre sua entrada:

- a cue de referência é um único segmento de áudio canonicalizado, não vazio
  (duração maior que zero após canonicalização);
- o áudio de origem é um único segmento de áudio canonicalizado, cuja duração
  pode ser igual, maior ou menor que a da cue;
- source e cue compartilham a mesma taxa de amostragem, número de canais e
  formato de amostra, por já satisfazerem o mesmo `CANONICAL_AUDIO_SPEC`.

Se qualquer pré-condição não for satisfeita (por exemplo, um dos dois áudios
não está canonicalizado, ou a cue está vazia), o resultado esperado é
`invalid_input` (seção 5), não `no_match` e não `processing_failure`.

Múltiplas cues, múltiplas ocorrências da mesma cue, persistência, execução
assíncrona, API e WebUI estão fora do escopo deste contrato
(docs/milestones.md — M2, Fora de Escopo).

## 2. Configuração Efetiva

Todo resultado produzido pelo matcher deve estar associado, de forma
explícita e rastreável, à configuração que o produziu: qual método foi usado
e quais parâmetros desse método estavam em vigor
(docs/architecture.md, Princípios e Restrições #7: "Parâmetros como
canonicalização, método de matching e threshold devem ser explicitamente
associados à análise ou a uma configuração versionada.").

Este contrato não fixa:

- o método exato (por exemplo correlação normalizada direta, correlação via
  FFT, ou técnica equivalente derivada dela);
- a forma dos parâmetros desse método (janela, passo de busca, normalização
  interna, tolerância a ruído, etc.);
- valores de threshold ou qualquer outro parâmetro de decisão de aceitação.

Essas definições pertencem a M2-02 (implementação numérica do baseline) e,
quanto à política de aceitação, a M2-05.

O que este contrato exige é a **forma da obrigação**, não seu conteúdo:

- um identificador de método (texto estável, por exemplo um nome de método),
  presente em todo resultado do tipo `found`;
- uma referência à configuração efetiva usada (os parâmetros do método em
  vigor no momento do matching), rastreável junto ao resultado.

Um substituto de matcher que use um método diferente permanece compatível
com este contrato desde que continue expondo identificador de método e
configuração efetiva do mesmo modo.

## 3. Timestamp da Melhor Correspondência

Quando o matcher produz um resultado do tipo `found` (seção 5), ele deve
expor um único timestamp de melhor correspondência (best-candidate
timestamp), com a seguinte semântica:

- **Origem**: o instante `0` (zero) do áudio de origem já canonicalizado,
  isto é, o início do arquivo de origem após canonicalização — não o início
  do arquivo de mídia original antes de qualquer processamento, e não um
  offset relativo à cue.
- **Unidade**: segundos, como valor de ponto flutuante não-negativo.
- **Significado**: o instante, dentro do áudio de origem canonicalizado, em
  que a cue candidata de melhor pontuação (best candidate) começa.
- **Limites válidos**: o timestamp deve satisfazer
  `0 <= timestamp <= duração_do_source_canonicalizado`. Um timestamp fora
  desses limites, ou ausente quando o resultado é `found`, é uma violação de
  contrato do matcher, não um resultado válido.

Este contrato não define nem promete uma precisão temporal medida (essa é
uma obrigação de validação empírica de M2, não deste contrato) e não define
o comportamento de múltiplas ocorrências da mesma cue (fora de escopo,
docs/milestones.md — M2, Fora de Escopo).

## 4. Semântica de Score

Todo resultado do tipo `found` deve incluir um score numérico produzido pelo
método identificado na seção 2.

Obrigações do score:

- o score é uma **similaridade específica do método** (method-specific
  similarity) entre a cue e o candidato de melhor pontuação no áudio de
  origem — não uma probabilidade calibrada e não uma medida de confiança
  (docs/architecture.md, Princípios e Restrições #5: "O termo `confidence`
  não deve ser utilizado como sinônimo de score sem calibração que
  justifique essa interpretação.");
- o score **não é garantidamente comparável** entre métodos diferentes: um
  score de 0.8 produzido por um método não é necessariamente equivalente, em
  qualidade de correspondência, a um score de 0.8 produzido por outro
  método; comparações entre métodos exigem calibração ou evidência própria,
  fora do escopo deste contrato;
- a decisão de aceitar ou rejeitar um candidato como ocorrência válida
  (found vs. no_match) usa o score **e** uma configuração explícita de
  decisão (por exemplo um threshold), mas essa política de decisão pertence
  a M2-05, não a este contrato (docs/architecture.md, Score vs Decision:
  "Matcher produz score. A regra que aceita ou rejeita uma ocorrência
  utiliza score e configuração explícita.").

Este contrato não define a faixa numérica do score, sua fórmula ou seu
método de normalização interna — esses detalhes pertencem à implementação
de cada método (M2-02) e podem variar entre métodos futuros.

## 5. Alternativas de Resultado

O matcher deve produzir exatamente uma de quatro alternativas de resultado,
explicitamente distintas entre si. Nenhuma delas deve ser silenciosamente
convertida em outra (docs/architecture.md, Princípios e Restrições #11:
"Falhas não devem ser convertidas silenciosamente em detections vazias.").

### 5.1. `found`

O matcher processou a entrada com sucesso e identificou um candidato que
satisfaz a configuração de decisão em vigor (política de M2-05).

Deve incluir:

- o timestamp de melhor correspondência (seção 3);
- o score (seção 4);
- o identificador de método e a configuração efetiva (seção 2).

### 5.2. `no_match`

O matcher processou a entrada com sucesso — as pré-condições da seção 1
foram satisfeitas e o processamento terminou normalmente — mas nenhum
candidato satisfez a configuração de decisão em vigor.

`no_match` é um resultado válido e completo, não um erro. Ele deve ser
distinguível de `invalid_input` e de `processing_failure`: a ausência de
correspondência é uma conclusão do matcher, não uma falha do matcher
(docs/architecture.md, Princípios e Restrições #11).

Um resultado `no_match` pode, opcionalmente, reter o melhor candidato
observado e seu score para fins de diagnóstico, mas essa retenção não torna
o resultado equivalente a `found`: a aceitação continua dependendo da
configuração de decisão de M2-05.

### 5.3. `invalid_input`

Uma pré-condição da seção 1 não foi satisfeita antes de o matching
propriamente dito começar — por exemplo, source ou cue não estão
canonicalizados conforme `CANONICAL_AUDIO_SPEC`, ou a cue está vazia.

`invalid_input` é distinto de `no_match`: `no_match` significa que o
matching foi executado e não encontrou candidato aceitável; `invalid_input`
significa que o matching não pôde ser executado de forma válida.

### 5.4. `processing_failure`

Uma falha inesperada ocorreu durante o processamento do matching em si (por
exemplo uma falha interna do método numérico), depois que as pré-condições
da seção 1 foram satisfeitas.

`processing_failure` é distinto de `no_match` (que é uma conclusão válida do
processamento) e de `invalid_input` (que é uma rejeição anterior ao
processamento). Uma implementação deve preservar essa distinção, seguindo o
mesmo princípio de taxonomia de erro explícito já adotado por Infrastructure
em M1 (`src/audio_cue_locator/infrastructure/media_processing/errors.py`),
sem exigir que o matcher reutilize essas classes específicas de mídia, que
pertencem a uma preocupação diferente (probing/decodificação, não
matching).

## 6. Revisão de Fronteira — Exemplos

Os exemplos abaixo são ilustrativos da fronteira de responsabilidade; eles
não fixam valores numéricos de score, threshold ou duração, que permanecem
decisões de M2-02/M2-05.

### 6.1. Exemplo — `found`

Um áudio de origem canonicalizado de 30 segundos contém, entre os segundos
12 e 13, um trecho acusticamente idêntico a uma cue canonicalizada de 1
segundo. Ao ser invocado com essa cue, essa origem e uma configuração
efetiva válida, o matcher retorna `found`, com timestamp igual a 12.0
segundos (início do trecho, relativo ao início da origem canonicalizada),
um score alto o suficiente para satisfazer a configuração de decisão em
vigor, e o identificador do método usado.

### 6.2. Exemplo — `no_match` (ausente)

Um áudio de origem canonicalizado de 30 segundos não contém nenhum trecho
acusticamente semelhante à cue fornecida. O matcher processa a entrada por
completo, identifica um melhor candidato (por exemplo com score baixo), mas
esse candidato não satisfaz a configuração de decisão em vigor. O matcher
retorna `no_match`, não `found` e não um erro.

### 6.3. Exemplo — `invalid_input` (falha de pré-condição)

A cue fornecida está vazia (duração zero após canonicalização), ou o áudio
de origem não está no formato canônico esperado (por exemplo, ainda em
dois canais). O matcher retorna `invalid_input` antes de tentar qualquer
comparação acústica; ele não retorna `no_match` nem tenta prosseguir com uma
entrada que viola suas pré-condições.

### 6.4. Exemplo — `processing_failure` (falha durante o processamento)

As pré-condições da seção 1 foram satisfeitas e o matching começou, mas uma
falha inesperada interrompe o processamento antes de um resultado poder ser
produzido (por exemplo, um recurso interno do método numérico se esgota ou
falha de forma não prevista). O matcher retorna `processing_failure`, não
`no_match`: a ausência de resultado não deve ser interpretada como "cue não
encontrada".

## 7. Fora de Escopo Deste Contrato

- Implementação numérica do matcher, seleção de FFT, benchmarking
  (M2-02, docs/milestones.md — M2, Escopo Núcleo).
- Calibração de threshold, medição empírica de precisão/score, calibração
  de confidence (M2-02/M2-05).
- Política de aceitação/rejeição baseada em evidência (M2-05).
- Qualquer alteração na canonicalização de áudio ou em
  `CANONICAL_AUDIO_SPEC` definidos em M1.
- Múltiplas cues, suporte completo a múltiplas ocorrências, schemas
  públicos de Analysis, persistência, execução assíncrona, REST API e
  WebUI (docs/milestones.md — M2, Fora de Escopo).
- Verificação de probing/decodificação ao vivo (não mockada) de mídia
  representativa — pré-requisito herdado de M1, pendente e não resolvido
  por este documento.

## Referências

- `docs/vision.md`
- `docs/architecture.md` — Princípios e Restrições #1, #5, #7, #11;
  Componentes ou Áreas Principais (Core, Application, Acoustic Matching);
  Score vs Decision.
- `docs/milestones.md` — M1 (Fundação e Pipeline de Mídia Canônica); M2
  (Baseline de Matching Acústico).
- `src/audio_cue_locator/infrastructure/media_processing/canonical_audio.py`
  — `CanonicalAudioSpec`, `CANONICAL_AUDIO_SPEC`.
- `src/audio_cue_locator/infrastructure/media_processing/errors.py` —
  convenção existente de taxonomia de erro explícito (referência de estilo,
  não reutilização direta pelo matcher).
- `issues/M2/M2-01/formal-issue.json` — especificação formal desta issue.
