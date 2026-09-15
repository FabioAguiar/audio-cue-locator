# Analysis Effective Configuration

## Finalidade

Este documento dá forma concreta ao campo `Analysis.effective_configuration`
já reservado por `docs/analysis-core-contracts.md` (M3-01) e completa a
rastreabilidade de configuração que aquele documento explicitamente deixou
para esta issue (M3-02): "rastreabilidade de configuração completa é escopo
de M3-02"). Ele define o snapshot de configuração efetiva anexado a cada
`Analysis`, combinando três entradas já existentes e já fixadas —

- os parâmetros de canonicalização do M1 (`CanonicalAudioSpec`);
- a identidade e os parâmetros do método de matching do M2
  (`EffectiveConfiguration`); e
- a política de aceitação/no-match em vigor (M2-05, `EVIDENCE_BASED_CONFIGURATION`,
  ou a política provisória de `baseline.py`, `DEFAULT_CONFIGURATION`) —

em uma única estrutura traçável por Analysis, e explicita como essa estrutura
é referenciada pela Analysis correspondente.

Este documento não introduz um novo tipo de Core além de estender o já
reservado `effective_configuration`; não re-deriva nem altera a decisão de
canonicalização do M1 (`src/audio_cue_locator/infrastructure/media_processing/canonical_audio.py`)
nem a política de aceitação do M2 (`src/audio_cue_locator/infrastructure/acoustic_matching/baseline.py`,
`src/audio_cue_locator/infrastructure/acoustic_matching/acceptance.py`); não
implementa persistência de histórico de configuração entre execuções de
Analysis nem uma API de configuração arbitrária; S0010 expõe somente o
override REST restrito `minimum_similarity_score`; e não implementa
orquestração multi-cue, política de múltiplas occurrences, o Analysis Result
versionado, API REST, WebUI ou execução assíncrona (M3-03 a M3-06, fora de
escopo).

## Extensão do Campo Já Reservado

`docs/analysis-core-contracts.md`, seção 1, descreve `effective_configuration`
como "o método de matching e seus parâmetros efetivamente usados para esta
Analysis ... sem fixar sua forma numérica interna" e adia explicitamente a
"rastreabilidade de configuração completa" para esta issue. Este documento
estende esse campo, sem contradizer a descrição original: `effective_configuration`
passa a denotar o snapshot completo (canonicalização + matching/aceitação),
não apenas a faceta de matching. `Analysis.source_asset` continua a ser uma
referência de identidade ao asset já canonicalizado (M1); `effective_configuration.canonicalization`
(seção 2 abaixo) é complementar, não substitui `source_asset` — ela registra
quais parâmetros de canonicalização se aplicavam no momento em que esta
Analysis específica foi processada, permitindo que uma futura revisão de
`CANONICAL_AUDIO_SPEC` não invalide silenciosamente a interpretação de
Analyses já produzidas. `Analysis.method_or_configuration` (já reservado por
M3-01) permanece o identificador do método, agora explicitamente igual a
`effective_configuration.matching.method` (seção 2).

## 1. Posição no Fluxo

```text
Core (docs/analysis-core-contracts.md, M3-01)
  Analysis --- effective_configuration: EffectiveConfigurationSnapshot (este documento)
  Analysis --- method_or_configuration: str  (== effective_configuration.matching.method)
      |
      | valor capturado e anexado no ponto de uso (seção 4), nunca lido
      | novamente de um default de módulo em tempo de interpretação
      v
Infrastructure — Acoustic Matching (M2-02, M2-05)
  EffectiveConfiguration(method, acceptance_threshold)
    DEFAULT_CONFIGURATION            (baseline.py, provisório)
    EVIDENCE_BASED_CONFIGURATION     (acceptance.py, evidence-based)
      |
      v
Infrastructure — Media Processing (M1-03)
  CANONICAL_AUDIO_SPEC (sample_rate_hz, channels, sample_format, normalization)
```

Como já estabelecido por `docs/analysis-core-contracts.md`, seção 5, o Core
não importa `EffectiveConfiguration`, `CanonicalAudioSpec` ou qualquer tipo de
Infrastructure diretamente. O snapshot definido abaixo é uma estrutura própria
do Core, populável a partir dessas formas de Infrastructure por uma camada de
conversão fora do Core (Application, futura), sem que o Core precise importar
`numpy`, FFmpeg, SQLite, filesystem, FastAPI ou Pydantic-como-transporte.

## 2. O Snapshot: `EffectiveConfigurationSnapshot`

Campo `Analysis.effective_configuration`, do tipo `EffectiveConfigurationSnapshot`,
com exatamente três campos conceituais:

| Campo | Significado |
|---|---|
| `canonicalization` | Cópia dos parâmetros de canonicalização (M1) em vigor para esta Analysis: `sample_rate_hz`, `channels`, `sample_format`, e `normalization` (`enabled`, `method`, `target_peak_amplitude`). Estruturalmente compatível com `CanonicalAudioSpec` (`canonical_audio.py`), sem importar esse tipo. |
| `matching` | Cópia do método de matching e seus parâmetros (M2) efetivamente usados para esta Analysis: `method` (ex.: `"normalized_cross_correlation_v1"`) e `acceptance_threshold`. Estruturalmente compatível com `EffectiveConfiguration` (`baseline.py`), sem importar esse tipo. `Analysis.method_or_configuration` é igual a `matching.method`. |
| `configuration_source_name` | Identificador estável, para rastreabilidade humana e de auditoria, de qual configuração nomeada já existente originou `matching` (ex.: `"acoustic_matching.baseline.DEFAULT_CONFIGURATION"`, `"acoustic_matching.acceptance.EVIDENCE_BASED_CONFIGURATION"`, ou `"explicit_override"` quando um valor diferente de ambas as constantes nomeadas foi fornecido explicitamente ao processar esta Analysis). Não é um quarto parâmetro numérico: `matching.acceptance_threshold` já é a política de aceitação/no-match em vigor para o `method` dado; este campo apenas nomeia de onde esse valor veio. |

`matching` cobre, sozinho, tanto "a identidade e os parâmetros do método de
matching" quanto "a política de aceitação/no-match em vigor" exigidos pela
issue formal (critério de aceite 1): no código de Infrastructure já existente,
essas duas coisas são o mesmo dado (`EffectiveConfiguration.method` +
`EffectiveConfiguration.acceptance_threshold` -- `acceptance_threshold` *é*
a política de aceitação/no-match para aquele `method`; `baseline.py` e
`acceptance.py` diferem apenas em qual valor de `acceptance_threshold` cada um
nomeia). Duplicar esse valor em um campo separado só para "nomear a política"
reintroduziria exatamente o risco que este documento evita — dois números que
podem divergir silenciosamente. `configuration_source_name` resolve a
necessidade de nomear a política sem duplicar o valor numérico.

### 2.1 Valores confirmados por inspeção direta (não redefinidos aqui)

Estes valores já existem em Infrastructure e são apenas referenciados, nunca
redefinidos, por este documento:

- `canonical_audio.py`: `CANONICAL_AUDIO_SPEC` = `sample_rate_hz=48000`,
  `channels=1`, `sample_format="float32"`, `normalization` = peak, ativada,
  `target_peak_amplitude=1.0`.
- `baseline.py`: `DEFAULT_CONFIGURATION` = `method="normalized_cross_correlation_v1"`,
  `acceptance_threshold=0.75` (documentado no próprio módulo como um valor
  não calibrado, provisório, "subject to review by M2-05").
- `acceptance.py`: `EVIDENCE_BASED_CONFIGURATION` = `method="normalized_cross_correlation_v1"`,
  `acceptance_threshold≈0.7056698933077521` (derivado por uma regra de
  margem explícita e não calibrada a partir da evidência já registrada por
  `docs/matching-regression.md`, M2-03, e `docs/matching-robustness.md`,
  M2-04; ver `docs/matching-acceptance.md`).

## 3. Qual Política Está "em Vigor" para uma Analysis (Critério de Aceite 1)

A issue formal e a análise desta issue registram que `baseline.py` e
`acceptance.py` expõem duas instâncias nomeadas de `EffectiveConfiguration`
igualmente legítimas, e que escolher entre elas (ou definir uma regra de
seleção) é trabalho próprio desta issue. Esta issue resolve isso da seguinte
forma: **a política de aceitação/no-match em vigor para uma Analysis é
determinada por quem inicia o processamento daquela Analysis, não fixada de
forma global neste contrato de Core.** Concretamente:

- `match_cue` (`baseline.py`) já recebe `configuration: EffectiveConfiguration`
  como parâmetro explícito (`configuration: EffectiveConfiguration = DEFAULT_CONFIGURATION`),
  não como um valor fixo; qualquer chamador já pode fornecer
  `EVIDENCE_BASED_CONFIGURATION`, `DEFAULT_CONFIGURATION`, ou outro valor.
- `effective_configuration.matching` e `configuration_source_name` registram,
  por Analysis, **qual valor foi de fato fornecido** para aquela execução --
  nunca um valor assumido ou lido de um default global no momento da
  interpretação (ver seção 4).
- Isso não redefine nem descarta nenhuma das duas políticas de M2: ambas
  permanecem disponíveis e válidas; esta issue apenas garante que a Analysis
  registra de forma traçável qual delas (ou qual outro valor) foi realmente
  usada, satisfazendo o critério de aceite 1 sem impor uma escolha global
  entre `DEFAULT_CONFIGURATION` e `EVIDENCE_BASED_CONFIGURATION` que nem o
  M2-05 nem a issue formal já fixaram.
- Desde S0009, a camada Application escolhe
  `EVIDENCE_BASED_MULTI_OCCURRENCE_CONFIGURATION` para Analyses novas. Essa
  escolha não reinterpreta records históricos: cada execução continua usando
  o método já capturado no snapshot persistido.

## 4. Captura no Ponto de Uso (Critério de Aceite 5)

`effective_configuration` é um **valor** capturado e anexado à Analysis no
momento em que ela é processada -- nunca uma leitura ao vivo de um default de
módulo (por exemplo, `baseline.DEFAULT_CONFIGURATION` ou
`canonical_audio.CANONICAL_AUDIO_SPEC`) feita posteriormente, no momento da
interpretação do resultado. Isto resolve diretamente o risco de severidade
alta registrado por `states/M3/M3-02/issue-operational-state.json` (risks[0]):
duas execuções da mesma Analysis sob configurações globais diferentes não
podem divergir silenciosamente, porque cada Analysis carrega sua própria
cópia dos valores efetivamente usados, e não uma referência que seria
reavaliada contra o estado atual do módulo.

Isso não implica que uma Analysis persista esse valor entre reinícios do
processo (persistência é explicitamente fora de escopo, seção 6): implica
apenas que, enquanto a Analysis existir em memória (ou em qualquer
serialização futura, fora de M3-02), `effective_configuration` é uma cópia
estática, não um ponteiro para um estado mutável.

## 5. Decisão de Referenciamento (Critério de Aceite 2)

**Decisão: cópia embutida (`embedded copy`), não referência versionada.**

`Analysis.effective_configuration` contém os valores de `canonicalization` e
`matching` diretamente, como dados inertes copiados no momento de uso (seção
4) -- não um identificador que precise ser resolvido posteriormente contra um
registro externo de configurações versionadas.

### Justificativa

- **Nenhuma camada de persistência existe.** Uma referência versionada
  pressupõe um registro (por identidade ou por número de versão) contra o
  qual o identificador é resolvido; nenhum registro desse tipo existe em M1,
  M2 ou M3-01, e criar um seria, por si só, uma forma de API/registro de
  configuração exposto -- exatamente o que a issue formal proíbe ("Não
  inclui: uma API pública ou exposta de configuração").
- **Uma referência versionada reabriria o risco que esta issue fecha.** Se
  `effective_configuration` fosse um identificador (ex.: `"config-v3"`) em vez
  de um valor, duas Analyses com o mesmo identificador dependeriam de esse
  identificador nunca ser reatribuído a um valor diferente -- reintroduzindo,
  por outra via, o mesmo risco de divergência silenciosa que a seção 4 já
  fecha ao exigir um valor capturado, não uma referência resolvida
  posteriormente.
- **Cópia embutida não requer histórico de configuração.** Uma referência
  versionada, para ser útil, pressupõe um histórico de valores versionados por
  trás do identificador; isso é exatamente "persistir histórico de
  configuração entre execuções de Analysis", que a issue formal também exclui
  explicitamente.
- **Cópia embutida mantém o Core independente de Infrastructure.** Resolver
  uma referência versionada exigiria uma camada de acesso (Infrastructure ou
  Application) capaz de consultar o registro; uma cópia embutida é um valor
  de Core puro, sem esse acoplamento (`docs/architecture.md`, Princípio 1).

### Não-objetivos explícitos desta decisão

- Não introduz um identificador de versão de configuração.
- Não introduz persistência de histórico de configuração entre execuções de
  Analysis.
- Não expõe uma API/endpoint de configuração arbitrária ou uma superfície de
  consulta de configuração. S0010 acrescenta apenas um campo opcional e
  delimitado à criação de Analysis.

## 6. Fora de Escopo Deste Documento

- Persistência de `Analysis`, `EffectiveConfigurationSnapshot`, ou histórico
  de configuração em SQLite ou qualquer outro armazenamento.
- Uma API pública de configuração arbitrária. O único controle externo é o
  `minimum_similarity_score` opcional de S0010.
- Alterar ou re-derivar a decisão de canonicalização do M1
  (`canonical_audio.py`) ou a política de aceitação do M2 (`baseline.py`,
  `acceptance.py`); todos os três permanecem inalterados por este documento.
- Qualquer implementação numérica de matching, calibração de threshold, ou
  uma pipeline de matching paralela/duplicada ao matcher single-cue
  existente.
- Uma regra automática de seleção de default entre `DEFAULT_CONFIGURATION` e
  `EVIDENCE_BASED_CONFIGURATION` (seção 3); isso é prerrogativa de uma camada
  de Application futura.
- Lógica de orquestração multi-cue e política de seleção/deduplicação de
  múltiplas occurrences (M3-03, M3-04).
- O schema versionado de Analysis Result e sua serialização JSON (M3-06),
  incluindo como `EffectiveConfigurationSnapshot` seria efetivamente
  serializado.
- API REST, WebUI, autenticação ou execução assíncrona.

## 7. Lacunas e Decisões Registradas

- **G1 (forma exata do snapshot):** resolvido por este documento (seção 2):
  `EffectiveConfigurationSnapshot` tem exatamente três campos --
  `canonicalization`, `matching`, `configuration_source_name` -- derivados
  diretamente dos três critérios de aceite da issue formal e da observação de
  que "identidade do método" e "política de aceitação/no-match" já são o
  mesmo dado em `EffectiveConfiguration` (M2).
- **G2 (local do documento):** resolvido em `intents/M3/M3-02/implementation-handoff.json`
  e confirmado nesta implementação -- nenhum documento equivalente já
  existente em `docs/` cobria este escopo; `docs/analysis-core-contracts.md`
  seção 7 exclui explicitamente esta rastreabilidade completa do seu próprio
  escopo. `docs/analysis-effective-configuration.md` é a localização
  definitiva.
- **G3 (decisão de referenciamento):** resolvido por este documento (seção
  5): cópia embutida, com justificativa explícita e não-objetivos
  declarados.
- **G4 (contratos de handoff ausentes):** `reference-library/contracts/ai-assistive-task-package-contract.md`
  e `reference-library/contracts/implementation-handoff-contract.md`
  permanecem ausentes na reference-library do repositório alvo; nenhum dos
  dois é gerado ou pressuposto por este documento.
- **G6 (lista desatualizada de arquivos a consultar):** resolvido por
  `intents/M3/M3-02/implementation-handoff.json`, que adicionou
  `docs/analysis-core-contracts.md` como leitura obrigatória; este documento
  o consultou e estende sua seção 6 sem duplicá-la ou contradizê-la.
- **G7 (qual política de aceitação está "em vigor"):** resolvido por este
  documento (seção 3): é uma escolha por chamador/por Analysis, registrada de
  forma traçável via `matching` e `configuration_source_name`, não uma
  seleção global fixada por este contrato de Core.
- Este documento não substitui revisão de fronteira explícita antes que
  M3-03 e M3-06 dependam deste contrato (ver seção 1 e o risco de alta
  severidade registrado por `states/M3/M3-02/issue-operational-state.json`).

## Default após S0009

Analyses novas capturam
`acoustic_matching.acceptance.EVIDENCE_BASED_MULTI_OCCURRENCE_CONFIGURATION`:
`method="normalized_cross_correlation_multi_v1"` e o mesmo
`acceptance_threshold` numérico derivado pelo corpus histórico. Registros já
persistidos continuam autoexplicativos e não são migrados: o executor roteia
pelo `matching.method`, preservando `normalized_cross_correlation_v1` como
single-best. Nenhum campo foi acrescentado a `MatchingSnapshot`; supressão e
limite são semânticas fixas do identificador versionado.

## Proveniência e imutabilidade após S0010

S0010 mantém duas origens explícitas para o mesmo método selecionado pelo
servidor:

- requisição omitida ou `null`: preserva exatamente o
  `acceptance_threshold` de
  `EVIDENCE_BASED_MULTI_OCCURRENCE_CONFIGURATION` e a origem
  `acoustic_matching.acceptance.EVIDENCE_BASED_MULTI_OCCURRENCE_CONFIGURATION`;
- `minimum_similarity_score` explícito em `0.0..1.0`: preserva exatamente o
  decimal solicitado em `matching.acceptance_threshold` e registra a origem
  estável anterior com o sufixo `+request.minimum_similarity_score`.

Mesmo quando o valor explícito é numericamente igual ao default, a segunda
origem é registrada. O snapshot embutido é persistido antes da execução e
permanece imutável durante toda a vida da Analysis. Alterar Settings no
navegador depois da criação afeta somente requisições futuras; o executor
continua reconstruindo sua configuração exclusivamente do snapshot persistido.
Nenhum campo novo ou migração de schema é necessário.

## Referências

- `docs/analysis-core-contracts.md` -- contrato original de `Analysis`,
  `Cue`, `Occurrence` (M3-01); campo `effective_configuration` /
  `method_or_configuration` estendido por este documento; seção 6
  (compatibilidade M2/M1) e seção 7 (fora de escopo, delega a
  rastreabilidade completa a M3-02).
- `docs/architecture.md` -- "Core", "Analysis", "Canonical Audio", "Acoustic
  Matching", Princípios e Restrições 1 e 5.
- `docs/milestones.md` -- M3, Escopo Núcleo e Dependências.
- `docs/matching-contract.md` -- semântica de score, timestamp e política de
  resultado do matcher single-cue (método-específico, não confiança
  calibrada).
- `docs/matching-acceptance.md` -- derivação e limitações de
  `EVIDENCE_BASED_CONFIGURATION` (M2-05).
- `src/audio_cue_locator/infrastructure/media_processing/canonical_audio.py` --
  `CanonicalAudioSpec`, `CANONICAL_AUDIO_SPEC` (M1-03).
- `src/audio_cue_locator/infrastructure/acoustic_matching/baseline.py` --
  `EffectiveConfiguration`, `DEFAULT_CONFIGURATION` (M2-02).
- `src/audio_cue_locator/infrastructure/acoustic_matching/acceptance.py` --
  `EVIDENCE_BASED_CONFIGURATION` (M2-05).
- `intents/M3/M3-02/implementation-handoff.json` -- escopo autorizado desta
  issue e decisões de handoff (G2, G6).
