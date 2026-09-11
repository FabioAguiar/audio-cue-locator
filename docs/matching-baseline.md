# Matching Baseline

## Finalidade

Este documento descreve a primeira implementação numérica do matcher de cue
única (single-cue matcher) do Audio Cue Locator: o método escolhido
(correlação cruzada normalizada), a faixa e direção do score, a convenção de
timestamp e o tratamento determinístico de entradas degeneradas.

Ele **não redefine** o contrato interno já fixado por M2-01
(`docs/matching-contract.md`): as pré-condições de entrada, a semântica de
timestamp, a semântica de score e as quatro alternativas de resultado
(`found`, `no_match`, `invalid_input`, `processing_failure`) descritas aqui
são a implementação concreta das obrigações que `docs/matching-contract.md`
já deixa em aberto para M2-02 (método exato, forma dos parâmetros, faixa
numérica do score). Em caso de qualquer aparente divergência, o contrato
(`docs/matching-contract.md`) prevalece; este documento é o registro
técnico da escolha feita por este baseline, não uma segunda fonte de
verdade.

Implementação: `src/audio_cue_locator/infrastructure/acoustic_matching/baseline.py`.
Testes: `tests/test_matching_baseline.py`.

## 1. Método

O baseline usa **correlação cruzada normalizada** (normalized
cross-correlation). Para cada posição possível (lag) de uma janela do
tamanho da cue dentro do source já canonicalizado, o score é:

```text
score(lag) = dot(source[lag : lag + len(cue)], cue)
             / (||source[lag : lag + len(cue)]|| * ||cue||)
```

onde `dot` é o produto interno e `||x||` é a norma L2 (euclidiana) de `x`.

A correlação bruta é calculada via `numpy.correlate(..., mode="valid")`
(definição direta, não acelerada por FFT), e a energia de cada janela via
soma de prefixos (`O(N)` em memória e tempo, sem materializar todas as
janelas sobrepostas). A escolha deliberada de **não** usar FFT ou otimização
de performance mantém a implementação de referência simples de revisar e
verificar manualmente; otimização de performance está explicitamente fora
de escopo desta issue (issue formal M2-02, §4 "Não inclui") e pode ser
revisitada no futuro caso uma necessidade medida surja.

O identificador de método estável (docs/matching-contract.md, §2) é:

```text
"normalized_cross_correlation_v1"
```

## 2. Configuração Efetiva

Todo resultado carrega uma `EffectiveConfiguration` com:

- `method`: o identificador de método acima;
- `acceptance_threshold`: um corte numérico simples e explícito, usado
  **apenas por este baseline** para distinguir `found` de `no_match` em suas
  próprias verificações focadas.

Configuração padrão (`DEFAULT_CONFIGURATION`):

```text
method = "normalized_cross_correlation_v1"
acceptance_threshold = 0.75
```

`acceptance_threshold` **não é** a política de aceitação baseada em
evidência reservada a M2-05 (docs/matching-contract.md, §4 e §7): não há
calibração de threshold, medição empírica de precisão, nem calibração de
confidence neste baseline. O valor 0.75 é um corte técnico provisório para
permitir que este baseline produza `found` e `no_match` de forma
determinística em suas próprias verificações controladas; ele deve ser
tratado como não-calibrado e sujeito a revisão por M2-05.

## 3. Timestamp da Melhor Correspondência

Implementado exatamente conforme docs/matching-contract.md, §3: o índice do
lag de melhor score é convertido para segundos dividindo pela taxa de
amostragem canônica (`CANONICAL_AUDIO_SPEC.sample_rate_hz`, 48000 Hz),
nunca redefinida por este módulo. Como o índice do melhor candidato está
sempre entre `0` e `len(source) - len(cue)` (por construção do algoritmo de
janela deslizante), o timestamp resultante está sempre dentro dos limites
válidos `0 <= timestamp <= duração_do_source_canonicalizado`; uma verificação
defensiva adicional (seção 6) converte qualquer violação inesperada dessa
garantia em `processing_failure`, nunca em um timestamp fora dos limites.

## 4. Semântica de Score

- **Faixa**: `[-1.0, 1.0]` (correlação cruzada normalizada por norma L2).
- **Direção**: `1.0` é uma correspondência positiva perfeita (mesma forma e
  amplitude relativa nesse lag); `0.0` é ausência de correlação linear;
  `-1.0` é uma correspondência perfeita com fase invertida.
- **Não é confidence**: o score é uma similaridade específica deste método,
  não uma probabilidade calibrada (docs/matching-contract.md, §4).
- **Não é comparável entre métodos**: um score de `0.8` produzido por este
  método não é necessariamente equivalente, em qualidade de correspondência,
  a um score de `0.8` produzido por um método futuro diferente.

## 5. Tratamento Determinístico de Entradas Degeneradas

Nenhum destes casos é convertido silenciosamente em outro resultado. A
tabela relaciona a condição observada ao resultado produzido e à
justificativa:

| Condição | Resultado | Justificativa |
|---|---|---|
| `source` ou `cue` não é `numpy.ndarray`, não é 1-D, não tem `dtype=float32`, ou contém valores não finitos (NaN/Inf) | `invalid_input` | Viola a pré-condição de que ambos já estejam canonicalizados (docs/matching-contract.md, §1). |
| `cue` vazia (duração zero) | `invalid_input` | Precondição explícita do contrato (docs/matching-contract.md, §1). |
| `source` mais curto que a `cue` | `no_match` | docs/matching-contract.md §1 permite explicitamente que a duração do source seja menor que a da cue; não existe nenhuma janela candidata de tamanho completo, então o processamento é válido e conclui sem candidato, não uma violação de precondição. |
| `cue` com energia ~zero (silenciosa, mas não vazia) | `no_match`, score determinístico `0.0` | A correlação normalizada é matematicamente indefinida (0/0) para uma cue de energia zero; este baseline define esse caso, de forma determinística e documentada, como "sem correlação" (score `0.0`), nunca abaixo de um threshold não-negativo, portanto sempre `no_match`. |
| Uma ou mais janelas do `source` com energia ~zero (silêncio) | Score `0.0` determinístico para essas janelas | Mesma convenção acima, aplicada por janela; se todas as janelas forem silenciosas, o resultado final é `no_match` com score `0.0`. |
| Falha inesperada durante o cálculo da correlação, ou um índice de candidato internamente inconsistente (fora dos limites válidos) | `processing_failure` | Distinto de `no_match` (conclusão válida) e de `invalid_input` (rejeição anterior ao processamento); ver seção 6. |

## 6. Verificação em Exemplos Controlados

`tests/test_matching_baseline.py` cobre, com exemplos sintéticos e
verificáveis manualmente:

- `found`: uma cue embutida verbatim dentro de um source com silêncio ao
  redor produz score `1.0` (autocorrelação) e o timestamp exato de início da
  cue; determinismo entre chamadas repetidas.
- `no_match`: (a) uma cue alternada contra um source constante produz score
  exatamente `0.0` em todas as janelas; (b) `source` mais curto que a `cue`;
  (c) `cue` silenciosa; (d) `source` inteiramente silencioso.
- `invalid_input`: `cue` vazia; `dtype` não-`float32`; valores não finitos;
  array não unidimensional; tipo que não é `numpy.ndarray`.
- `processing_failure`: injeção de uma função de localização de candidato
  (`candidate_locator`, um mecanismo de teste/extensão que não faz parte do
  contrato observável) que levanta uma exceção, e outra que retorna um
  índice fora dos limites válidos.
- Guarda de fronteira arquitetural: `src/audio_cue_locator/core/__init__.py`
  e `src/audio_cue_locator/application/__init__.py` não importam `numpy`
  nem `scipy` (issue formal M2-02, §8, critério de aceite 4).

Estas são verificações controladas/sintéticas, não uma avaliação de
robustez em mídia representativa; docs/milestones.md reserva essa avaliação
para uma etapa posterior do milestone M2.

## 7. Fora de Escopo Deste Baseline

- Calibração de threshold final, medição empírica de precisão/score,
  calibração de confidence (M2-05).
- Política de aceitação/rejeição baseada em evidência (M2-05).
- Otimização de performance (FFT, busca em janela, paralelização) sem
  necessidade medida.
- Qualquer alteração na canonicalização de áudio ou em
  `CANONICAL_AUDIO_SPEC` definidos em M1.
- Múltiplas cues, suporte completo a múltiplas ocorrências, schemas
  públicos de Analysis, persistência, execução assíncrona, REST API e
  WebUI (docs/milestones.md — M2, Fora de Escopo).
- Verificação de probing/decodificação ao vivo (não mockada) de mídia
  representativa — pré-requisito herdado de M1/M2-01, pendente e não
  resolvido por este documento.

## Referências

- `docs/matching-contract.md` — contrato interno do matcher (M2-01); este
  documento implementa, e não redefine, suas seções 1-5.
- `docs/architecture.md` — Princípios e Restrições #1, #5, #7, #11;
  Componentes ou Áreas Principais (Core, Application, Acoustic Matching).
- `docs/milestones.md` — M2 (Baseline de Matching Acústico).
- `src/audio_cue_locator/infrastructure/acoustic_matching/baseline.py` —
  implementação de referência.
- `tests/test_matching_baseline.py` — verificações controladas.
- `issues/M2/M2-02/formal-issue.json` — especificação formal desta issue.
