# Analysis Lifecycle State Machine

## Finalidade

Este documento descreve o contrato executavel definido por
`src/audio_cue_locator/core/analysis_lifecycle.py` (M4-01): a enumeracao
dos estados de lifecycle de `Analysis`, o conjunto completo de transicoes
validas entre eles, e o invariante de finalidade dos estados terminais.
Ele existe para que a Analysis Repository (M4-03, persistencia SQLite) e o
Local Analysis Executor (M4-04) validem e apliquem transicoes contra um
unico contrato Core autoritativo, em vez de cada um implementar sua propria
nocao de quais transicoes sao validas (`docs/architecture.md`, Principio
1: "O Core nao conhece interfaces externas"; `issues/M4/M4-01/formal-
issue.json`, secoes 2-3).

Este documento nao implementa persistencia, execucao, ou semantica de
recuperacao apos reinicio/interrupcao. Tambem nao redefine `Analysis`,
`Cue`, `Occurrence` ou `PerCueOutcome`, ja fixados por
`docs/analysis-core-contracts.md` (M3-01/M3-05); ele documenta apenas a
camada de regras de transicao de lifecycle que aquele documento
explicitamente deixa para uma issue posterior.

## Posicao no Fluxo

```text
Core (este documento e analysis_lifecycle.py)
  AnalysisLifecycleState = QUEUED | RUNNING | SUCCEEDED | FAILED
  VALID_TRANSITIONS = {
      QUEUED -> RUNNING,
      RUNNING -> SUCCEEDED,
      RUNNING -> FAILED,
  }
      |
      | consultado antes de persistir ou agir sobre uma nova transicao
      v
Infrastructure -- Analysis Repository (M4-03, SQLite)
  valida e persiste `Analysis.state` usando este contrato
      |
      v
Infrastructure/Application -- Local Analysis Executor (M4-04)
  aplica transicoes durante o processamento de uma Analysis
```

Este documento e o modulo que ele descreve nao conhecem SQLite, filesystem
ou qualquer tipo especifico de executor, tal como nenhum outro artefato
Core (`docs/architecture.md`, Principio 1). M4-03 e M4-04 consomem este
contrato; nao o reimplementam.

## 1. Estados

| Estado | Significado |
|---|---|
| `QUEUED` | A Analysis foi aceita conceitualmente e aguarda processamento; nenhuma cue foi ainda avaliada pelo matcher. |
| `RUNNING` | O processamento por cue esta em andamento. |
| `SUCCEEDED` | Todas as cues foram processadas e seus outcomes independentes estao presentes. Estado terminal. |
| `FAILED` | O processamento nao pode concluir de forma valida; um erro estruturado descreve a falha. Estado terminal. |

Esta e a mesma enumeracao ja registrada, em nivel conceitual, por
`docs/analysis-core-contracts.md` (secao 4) e por `docs/architecture.md`
("Analysis"). Nenhum quinto estado (por exemplo, cancelamento) e
adicionado aqui: `docs/architecture.md` fixa explicitamente que
"Cancelamento nao faz parte do contrato inicial", decisao que este
documento preserva.

## 2. Transicoes Validas

O conjunto completo de transicoes validas e:

```text
QUEUED -> RUNNING
RUNNING -> SUCCEEDED
RUNNING -> FAILED
```

Qualquer par de estados nao listado acima -- incluindo qualquer transicao
que parta de `SUCCEEDED` ou de `FAILED` -- e invalido. Nao existe
transicao de auto-loop (por exemplo `RUNNING -> RUNNING`) neste contrato
inicial.

## 3. Invariante: Finalidade dos Estados Terminais

`SUCCEEDED` e `FAILED` sao estados terminais: nenhuma transicao parte
deles. Isso e representado no codigo por `TERMINAL_STATES` (o conjunto
`{SUCCEEDED, FAILED}`) e reforcado estruturalmente porque `VALID_
TRANSITIONS` nao contem nenhum par cuja origem seja um desses dois
estados -- o invariante nao depende de uma verificacao redundante feita a
parte pelo chamador. `is_terminal(state)` expoe essa verificacao para uso
externo (por exemplo, para uma futura Analysis Repository decidir se uma
transicao recebida deve ser rejeitada antes mesmo de consultar `VALID_
TRANSITIONS`).

`is_valid_transition(from_state, to_state)` responde apenas com base em
`VALID_TRANSITIONS`. `transition(from_state, to_state)` e a funcao pura de
validacao: retorna `to_state` quando a transicao e valida, ou levanta
`InvalidLifecycleTransitionError` caso contrario. Nenhuma das duas funcoes
persiste ou executa nada; este modulo nao possui persistencia ou execucao
proprias -- essas responsabilidades permanecem em M4-03 e M4-04,
respectivamente.

## 4. Extensao Reservada para M4-05 (Restart/Interrupcao)

O formal issue M4-01 exige explicitamente que este contrato deixe espaco
para uma futura transicao relacionada a reinicio/interrupcao (por exemplo,
uma `Analysis` que estava `RUNNING` quando o executor foi interrompido),
sem adivinhar seu formato agora (`issues/M4/M4-01/formal-issue.json`,
secao 4, "Restricoes"; criterio de aceite 4).

Este documento e `analysis_lifecycle.py` reservam esse ponto de extensao
das seguintes formas:

- Nenhuma transicao relacionada a recuperacao apos interrupcao esta
  definida em `VALID_TRANSITIONS` hoje.
- O comentario acima de `VALID_TRANSITIONS`, no codigo, registra
  explicitamente essa reserva como nota para M4-05.
- Uma futura M4-05 deve **adicionar** uma transicao (e, se justificado,
  um estado) a este contrato existente, em vez de redefinir os quatro
  estados ou as tres transicoes ja fixadas acima. Isso preserva a
  estabilidade que M4-03 e M4-04 (bloqueadas por M4-01) precisam para
  depender deste contrato sem re-derivar suas proprias regras.

## 5. Fronteira do Core (Independencia de Infrastructure)

Nem `AnalysisLifecycleState`, nem `VALID_TRANSITIONS`, nem
`InvalidLifecycleTransitionError`, nem as funcoes `is_terminal`,
`is_valid_transition` ou `transition` importam ou requerem, direta ou
indiretamente:

- SQLite ou qualquer driver de banco de dados;
- caminhos de filesystem;
- qualquer tipo especifico de executor (thread, processo, pool);
- FastAPI, Pydantic-como-transporte, FFmpeg ou `numpy`.

`src/audio_cue_locator/core/analysis_lifecycle.py` nao importa nada alem
da biblioteca padrao do Python (`enum`, `__future__`), satisfazendo
diretamente o criterio de aceite 3 da issue formal.

## 6. Fora de Escopo Deste Documento

- Persistencia do estado de `Analysis` em SQLite (M4-03).
- Implementacao do executor local que reivindica e processa Analyses
  atraves deste lifecycle (M4-04).
- Semantica exata da transicao de reinicio/interrupcao (M4-05); apenas o
  ponto de extensao e reservado aqui, nao sua forma.
- Politica de retencao/cleanup de assets (M4-02, M4-06).
- Qualquer modificacao a `docs/analysis-core-contracts.md` ou aos tipos
  `Analysis`, `Cue`, `Occurrence`, `PerCueOutcome` la definidos.

## Referencias

- `docs/architecture.md` -- "Analysis", Principios e Restricoes 1 e 9.
- `docs/analysis-core-contracts.md` -- secao 4, "Estados de Lifecycle da
  `Analysis`" (M3-01), o contrato conceitual que este documento estende
  sem redefinir.
- `src/audio_cue_locator/core/analysis_lifecycle.py` -- forma executavel
  deste documento.
- `tests/test_analysis_lifecycle.py` -- cobertura automatizada das
  transicoes validas e da rejeicao de transicoes a partir de um estado
  terminal.
- `issues/M4/M4-01/formal-issue.json` -- escopo autorizado e criterios de
  aceite desta issue.
- `intents/M4/M4-01/implementation-handoff.json` -- escopo autorizado e
  decisoes de handoff (G1-G5).
