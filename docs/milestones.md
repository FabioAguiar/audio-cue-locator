# Milestones

## Finalidade

Este documento registra o planejamento de evolução do Audio Cue Locator em milestones proporcionais, encerráveis e deriváveis a partir de `docs/vision.md` e `docs/architecture.md`.

As milestones descrevem capacidades que o projeto deverá adquirir, os limites que precisam ser preservados, as evidências necessárias para encerramento e os critérios que permitirão futura geração de drafts de issues.

Este documento não executa implementação, não publica issues, não define a milestone vigente e não substitui State operacional. A ordem das milestones representa dependência lógica de planejamento, não um cursor automático de execução.

## Regras de Leitura

- Milestones são unidades de planejamento orientadas a capacidades encerráveis.
- Milestones não são issues formais.
- Milestones não são microtarefas.
- Issues previstas representam tipos de trabalho que poderão ser derivados posteriormente; não constituem drafts nem autorização de execução.
- A implementação concreta depende de etapa posterior explicitamente autorizada.
- A ordem textual das milestones representa uma sequência lógica recomendada, mas não deve ser usada isoladamente para inferir State operacional.
- Uma milestone pode depender de outra sem que essa dependência autorize execução automática.
- Lacunas técnicas podem ser resolvidas dentro de uma milestone quando a própria validação da lacuna fizer parte de sua capacidade encerrável.
- Itens condicionados a necessidade futura não devem ser promovidos a milestones obrigatórias sem evidência.
- A estratégia de documentação da implementação vigente é `milestones-only`.
- A ausência de Implementation Map neste estágio é intencional.
- Documentação acumulativa futura, se necessária, deve ser autorizada por issue ou handoff apropriado e não inferida apenas a partir deste documento.
- Arquivos reais, testes e artifacts versionáveis permanecem a fonte técnica primária sobre o estado implementado.

## Relação com docs/vision.md

`docs/vision.md` define a direção macro do produto:

- localizar uma ou mais cues acústicas de referência em arquivos de áudio ou vídeo;
- produzir ocorrências temporais e scores interpretáveis;
- oferecer uso standalone e consumo programático;
- manter o núcleo genérico e independente de semântica externa;
- priorizar simplicidade, reprodutibilidade e crescimento incremental;
- evitar infraestrutura distribuída e complexidade prematura;
- validar hipóteses algorítmicas antes de consolidá-las.

As milestones deste documento transformam essa direção em capacidades sucessivas sem alterar os limites definidos pela visão.

Itens classificados como fora de escopo na visão permanecem fora de escopo aqui, salvo revisão documental futura explícita.

## Relação com docs/architecture.md

`docs/architecture.md` é a principal fonte para:

- fronteiras entre Core, Application, Infrastructure, REST API e WebUI;
- modular monolith como baseline;
- Ports and Adapters leve;
- FFmpeg como adapter de media processing;
- NumPy/SciPy como baseline numérico;
- SQLite para estado e metadados;
- filesystem local para bytes e artifacts;
- lifecycle assíncrono local para analyses;
- WebUI como cliente da REST API;
- separação entre control plane e data plane;
- separação entre score e confidence;
- versionamento de API e resultados;
- estratégia `milestones-only`;
- limites contra overengineering.

A sequência de milestones respeita as dependências arquiteturais declaradas:

```text
canonicalização
→ matching
→ contratos de Analysis e Result
→ persistência e lifecycle
→ API
→ WebUI
→ baseline operacional reproduzível
```

## Relação com State Operacional

`docs/milestones.md` não é State operacional.

Este documento:

- não declara qual milestone está vigente;
- não declara automaticamente uma milestone como concluída;
- não registra cursor de execução;
- não controla reabertura;
- não deve ser alterado por runtime para refletir progresso operacional.

Se o projeto adotar State operacional explícito, esse State deverá viver em artifact separado, por exemplo:

```text
docs/project-status/milestone-state.json
```

A existência futura desse artifact não é requisito desta etapa.

A milestone vigente, quando houver, deve ser determinada pelo State operacional autorizado e não apenas pela posição textual neste documento.

## Relação com Documentação da Implementação

**Estratégia aplicável: `milestones-only`**

A arquitetura definiu que, no estágio atual, `vision.md`, `architecture.md`, `milestones.md`, arquivos reais e testes fornecem contexto suficiente.

Portanto:

- nenhuma milestone deste documento exige criação de Implementation Map por padrão;
- cada milestone deve apenas avaliar se os gatilhos de revisão da estratégia ocorreram;
- issues e `implementation_handoff` futuros são os mecanismos apropriados para autorizar criação ou atualização de documentação acumulativa;
- um Implementation Map futuro, se adotado, servirá para navegação e handoff;
- um Implementation Map futuro não será changelog;
- um Implementation Map futuro não será State operacional;
- um Implementation Map futuro não substituirá arquivos reais, testes, arquitetura, milestones ou histórico de versionamento;
- a ausência de mapa durante `milestones-only` não constitui lacuna documental.

A estratégia deve ser reavaliada quando houver sinais materiais como:

- várias áreas implementadas difíceis de localizar;
- handoffs recorrentes exigindo reconstrução ampla de contexto;
- crescimento paralelo significativo de processing, storage, API e WebUI;
- dificuldade frequente para identificar contratos e responsabilidades;
- `milestones.md` começar a ser usado indevidamente como descrição detalhada da implementação existente.

Até que esses sinais apareçam, a regra é não criar documentação acumulativa adicional.

## Visão Geral das Milestones

| Milestone | Foco | Resultado esperado | Derivável? |
|---|---|---|---|
| M1 | Fundação e mídia canônica | Projeto executável com pipeline determinístico de áudio canônico para entradas suportadas | Sim |
| M2 | Matching acústico | Baseline de single-cue matching validado com score, timestamp e evidência de qualidade/desempenho | Sim |
| M3 | Analysis e resultados | Modelo central de Analysis/Cue/Occurrence e resultado versionado com multi-cue/multi-occurrence | Sim |
| M4 | Lifecycle persistente local | Analyses persistentes, assets locais e execução assíncrona local com estados coerentes | Sim |
| M5 | REST API v1 | Capacidades centrais acessíveis por API versionada com uploads, criação, status e resultado | Sim |
| M6 | WebUI standalone | Fluxo humano completo consumindo exclusivamente a API | Sim |
| M7 | Baseline operacional reproduzível | Produto local empacotado, observável, limitado e validado end-to-end | Sim |

---

## M1 — Fundação e Pipeline de Mídia Canônica

### Objetivo

Estabelecer a base executável do projeto e uma capacidade determinística de transformar mídias suportadas em uma representação canônica de áudio adequada às etapas posteriores de matching.

Ao final da milestone, o projeto deve conseguir receber uma mídia de teste suportada, identificar seu áudio, canonicalizá-lo e produzir metadata técnica reproduzível sem depender de lógica de matching.

### Problema ou Lacuna

O projeto possui decisões arquiteturais, mas ainda não existe uma base material capaz de:

- executar de forma reproduzível;
- validar uma mídia;
- extrair áudio de um container de vídeo;
- produzir uma representação canônica;
- confirmar parâmetros técnicos como channels, sample rate e sample representation.

Sem essa capacidade, qualquer avaliação de matching seria construída sobre uma entrada instável.

### Contexto

A arquitetura define FFmpeg como boundary de media processing e estabelece que canonicalização é dependência do matching.

Também determina que:

- formatos oficialmente suportados devem ser explícitos;
- o suporte não pode ser inferido apenas do que uma instalação arbitrária do FFmpeg aceita;
- Core não deve depender de FFmpeg;
- subprocessos precisam ser encapsulados;
- transformação de mídia deve ser determinística para configuração fixa.

Esta milestone deve reduzir primeiro as incertezas do pipeline de entrada.

### Escopo Núcleo

- estabelecer a base inicial do projeto em Python;
- fixar uma versão inicial de runtime compatível com as dependências selecionadas;
- materializar a separação mínima entre Core, Application, Infrastructure e interfaces de teste necessárias;
- integrar FFmpeg por um adapter de media processing;
- realizar probing de mídia;
- identificar presença e seleção do stream de áudio;
- suportar ao menos um formato de áudio e um container de vídeo adequados para validação inicial;
- definir e validar a primeira representação canônica de áudio;
- determinar sample rate inicial por evidência técnica;
- determinar representação de samples e quantidade de channels;
- definir se normalização de amplitude pertence ao baseline e, se pertencer, qual comportamento inicial;
- produzir metadata técnica suficiente para testes e rastreabilidade;
- criar fixtures pequenas e reproduzíveis;
- tratar entradas inválidas ou sem stream de áudio de forma explícita;
- validar comportamento determinístico para entradas e configuração conhecidas.

### Fora de Escopo

- matching acústico;
- thresholds;
- score de similaridade;
- Analysis persistente;
- SQLite;
- executor assíncrono;
- REST API pública;
- WebUI;
- múltiplas cues;
- múltiplas occurrences;
- waveform para interface;
- object storage;
- referências externas de assets;
- autenticação;
- processamento distribuído.

### Entregáveis Esperados

- projeto Python inicial executável e testável;
- boundary clara de media processing;
- adapter FFmpeg funcional;
- contrato interno para representação de áudio canônico;
- conjunto inicial explicitamente documentado de formatos validados;
- parâmetros iniciais de canonicalização justificados;
- fixtures reproduzíveis para áudio e vídeo;
- testes unitários e de integração da canonicalização;
- erros estruturados para classes principais de falha de mídia;
- evidência de determinismo para inputs conhecidos.

### Documentação da Implementação

- Estratégia aplicável: `milestones-only`
- Avaliação esperada: confirmar que a estrutura inicial continua pequena e navegável apenas com arquitetura, milestones, código e testes.
- Documentos candidatos: nenhum Implementation Map previsto.
- Critério para criar: somente se a materialização do projeto já produzir complexidade de navegação incompatível com `milestones-only`.
- Critério para atualizar: não se aplica enquanto nenhum mapa existir.
- Critério para não atualizar: mudanças normais de bootstrap, adapters e testes não justificam documentação acumulativa adicional.

### Dependências

- `docs/vision.md` aprovado como direção de produto.
- `docs/architecture.md` aprovado como arquitetura inicial.
- FFmpeg disponível no ambiente de desenvolvimento ou empacotado de forma compatível com a validação da milestone.

Não depende de outra milestone implementada.

### Componentes ou Áreas Afetadas

- Core, apenas para tipos técnicos mínimos que não contaminem a semântica do produto;
- Application, quando necessário para coordenar a operação de canonicalização;
- Media Processing;
- Observability and Configuration;
- tooling de testes e fixtures.

### Issues Previstas ou Critérios de Derivação

- Critério: criar a base de runtime e dependências sem antecipar componentes de milestones posteriores.
  - Possível tipo de issue: foundation/bootstrap.
  - Observação: deve preservar modular monolith e boundaries definidos.

- Critério: encapsular probing e decode/canonicalização.
  - Possível tipo de issue: media adapter.
  - Observação: deve impedir espalhamento de comandos FFmpeg pelo projeto.

- Critério: decidir parâmetros canônicos com evidência.
  - Possível tipo de issue: technical validation.
  - Observação: sample rate e normalização não devem ser escolhidos apenas por preferência.

- Critério: criar material reproduzível de validação.
  - Possível tipo de issue: fixtures/testing.
  - Observação: fixtures devem ser pequenas e adequadas para versionamento.

### Definition of Done

A milestone pode ser encerrada quando:

- o projeto executa em ambiente limpo com dependências declaradas;
- uma mídia de áudio suportada é canonicalizada com sucesso;
- um container de vídeo suportado tem seu áudio identificado e canonicalizado com sucesso;
- a representação canônica está definida e testada;
- a mesma entrada com a mesma configuração produz resultado técnico equivalente de forma determinística;
- mídia sem áudio ou inválida produz erro explícito;
- FFmpeg permanece encapsulado na infraestrutura;
- formatos validados estão declarados;
- parâmetros de canonicalização possuem justificativa registrada;
- testes automatizados relevantes passam;
- nenhuma capacidade de matching foi introduzida por atalho.

### Evidência Mínima

- suíte automatizada de testes de canonicalização;
- fixtures sintéticas ou curadas versionáveis;
- execução registrada demonstrando áudio e vídeo canonicalizados;
- metadata resultante com duração, channels, sample rate e representação;
- registro da decisão técnica sobre parâmetros canônicos;
- prova de erro controlado para mídia inválida ou sem áudio.

### Riscos e Lacunas

- o sample rate ideal pode depender do tipo real de cues;
- normalização de amplitude pode melhorar ou degradar matching futuro;
- diferentes builds do FFmpeg podem produzir pequenas diferenças;
- escolher formatos iniciais amplos demais pode aumentar escopo prematuramente;
- fixtures sintéticas não substituem material representativo para validar matching;
- limites de tamanho e duração ainda não precisam ser definitivos nesta milestone.

### Critérios de Derivabilidade

A milestone está pronta para gerar drafts de issues porque:

- o objetivo é observável;
- a lacuna está delimitada;
- o escopo não depende de matching;
- as principais áreas afetadas são conhecidas;
- os entregáveis são verificáveis;
- as decisões abertas podem ser tratadas por validação técnica dentro da própria milestone;
- fora de escopo está explícito;
- a estratégia documental está definida.

### Notas de Continuidade

A saída de M1 deve ser tratada como contrato técnico de entrada para o matcher.

M2 não deve redefinir a canonicalização por conveniência local. Se evidência de matching demonstrar que os parâmetros de M1 são inadequados, a decisão deve ser revisada explicitamente e acompanhada de regressão dos testes de mídia.

---

## M2 — Baseline de Matching Acústico

### Objetivo

Entregar e validar um primeiro matcher capaz de localizar uma única cue de referência dentro de uma mídia canonicalizada, produzindo timestamp e score metodologicamente definido.

A milestone deve provar a função central do produto antes de introduzir persistência, API ou interface visual.

### Problema ou Lacuna

O projeto ainda não possui evidência de que uma técnica de matching consiga localizar cues com qualidade e custo computacional aceitáveis.

Também permanecem abertas decisões sobre:

- método exato de correlação;
- threshold;
- score;
- busca direta ou baseada em FFT;
- tolerância a ruído e alterações de nível;
- precisão temporal.

Sem esse baseline, contratos e interfaces poderiam ser construídos sobre comportamento acústico inadequado.

### Contexto

A arquitetura estabelece NumPy/SciPy como baseline numérico e correlação normalizada, ou técnica equivalente derivada dessa abordagem, como primeira família a ser validada.

Ela também exige:

- score separado de confidence;
- matcher isolado do Application;
- evidência antes de otimização;
- capacidade de substituir o método posteriormente;
- testes com fixtures determinísticas e material representativo.

### Escopo Núcleo

- definir o contrato interno inicial de matcher;
- implementar uma primeira estratégia de single-cue matching;
- produzir timestamp de melhor correspondência;
- produzir score com semântica documentada;
- definir um threshold inicial ou estratégia equivalente de decisão;
- definir comportamento explícito para cue não encontrada;
- avaliar correlação direta e/ou FFT conforme necessidade observada;
- medir precisão temporal em material controlado;
- medir custo de execução em mídias representativas;
- testar variações de amplitude, compressão, ruído ou condições equivalentes selecionadas;
- estabelecer dataset/fixtures mínimos para regressão;
- registrar limites conhecidos do baseline.

### Fora de Escopo

- múltiplas cues por Analysis;
- múltiplas occurrences da mesma cue como capacidade completa;
- persistência de Analysis;
- SQLite;
- executor local assíncrono;
- REST API;
- WebUI;
- waveform;
- calibration de confidence;
- machine learning;
- fingerprinting complexo sem evidência de necessidade;
- processamento distribuído;
- otimização para escalas não medidas.

### Entregáveis Esperados

- contrato interno do matcher;
- implementação do baseline de matching;
- definição explícita do score;
- política inicial de aceitação/rejeição;
- comportamento de no-match;
- fixtures sintéticas para localização temporal conhecida;
- conjunto representativo mínimo para avaliação;
- testes automatizados de matching;
- benchmark técnico inicial;
- registro de limitações e condições em que o baseline falha;
- decisão documentada sobre adequação do método para continuidade.

### Documentação da Implementação

- Estratégia aplicável: `milestones-only`
- Avaliação esperada: confirmar se a introdução do matcher cria navegação complexa; a expectativa é que não.
- Documentos candidatos: nenhum Implementation Map previsto.
- Critério para criar: somente se múltiplas estratégias reais de matcher e infraestrutura associada já tornarem a navegação materialmente difícil.
- Critério para atualizar: não se aplica enquanto nenhum mapa existir.
- Critério para não atualizar: experimentos, benchmarks e refatorações do matcher permanecem documentados em artifacts técnicos apropriados, não em mapa acumulativo.

### Dependências

- M1 concluída.
- Representação canônica de áudio definida e reproduzível.
- Fixtures de mídia utilizáveis pelo matcher.

### Componentes ou Áreas Afetadas

- Core, para semântica de score/resultado mínimo quando necessário;
- Acoustic Matching;
- Observability and Configuration;
- tooling de testes, benchmarks e fixtures.

### Issues Previstas ou Critérios de Derivação

- Critério: implementar o contrato e baseline do matcher.
  - Possível tipo de issue: acoustic matching.
  - Observação: não deve acoplar NumPy/SciPy ao Core.

- Critério: validar timestamp e score.
  - Possível tipo de issue: algorithm validation.
  - Observação: deve separar qualidade de detecção de custo computacional.

- Critério: avaliar robustez mínima.
  - Possível tipo de issue: robustness experiment.
  - Observação: não deve expandir para técnicas avançadas sem evidência.

- Critério: definir threshold/no-match.
  - Possível tipo de issue: result semantics.
  - Observação: threshold deve ser rastreável e configurável conforme decisão posterior.

### Definition of Done

A milestone pode ser encerrada quando:

- uma cue conhecida é localizada em fixtures controladas dentro da precisão aceita para o baseline;
- o matcher retorna score com semântica explicitamente documentada;
- no-match é representado de forma inequívoca;
- existe uma política inicial de threshold ou decisão equivalente;
- desempenho é medido em pelo menos um cenário representativo;
- testes de regressão cobrem correspondência positiva, negativa e casos limítrofes selecionados;
- limitações relevantes estão registradas;
- o baseline é considerado adequado para sustentar o modelo de Analysis ou sua inadequação gera decisão arquitetural explícita antes de prosseguir.

### Evidência Mínima

- resultados automatizados de fixtures com timestamps conhecidos;
- benchmark inicial;
- comparação entre casos positivos e negativos;
- evidência de comportamento sob pelo menos algumas perturbações representativas;
- registro do método, parâmetros e limitações;
- testes automatizados passando.

### Riscos e Lacunas

- correlação normalizada pode ser insuficiente para mídias com transformações significativas;
- threshold pode variar conforme tipo de cue;
- score pode não ser comparável entre métodos futuros;
- precisão observada em sinais sintéticos pode superestimar desempenho real;
- FFT pode reduzir custo em casos longos, mas adicionar complexidade desnecessária em casos pequenos;
- material representativo suficiente ainda precisa ser escolhido cuidadosamente.

### Critérios de Derivabilidade

A milestone está pronta para derivação porque:

- seu objetivo é uma única capacidade central;
- a canonicalização é dependência explícita;
- o método inicial está arquiteturalmente delimitado;
- as principais incertezas são objetos de validação da própria milestone;
- há entregáveis e evidência observáveis;
- itens avançados permanecem fora de escopo.

### Notas de Continuidade

M3 deve consumir o comportamento validado do matcher, não redefinir sua matemática.

Se M2 concluir que o baseline não é tecnicamente suficiente, a continuidade deve primeiro registrar a revisão arquitetural necessária em vez de esconder limitações atrás de contratos mais amplos.

---

## M3 — Modelo de Analysis e Resultados Estruturados

### Objetivo

Consolidar os conceitos centrais do produto e transformar o matcher validado em uma capacidade de análise estruturada que suporte múltiplas cues e múltiplas occurrences sem depender ainda de persistência ou API pública.

### Problema ou Lacuna

Após provar o single-cue matching, o projeto ainda precisa responder de forma consistente:

- o que é uma Analysis;
- como cues são identificadas;
- como occurrences são representadas;
- como múltiplas cues são coordenadas;
- como múltiplas occurrences são selecionadas ou agrupadas;
- como resultado e configuração são versionados;
- como score, método e parâmetros permanecem rastreáveis.

Sem esses contratos, persistência e API seriam definidas sobre estruturas instáveis.

### Contexto

A arquitetura identifica `Analysis`, `Cue`, `Occurrence` e `Analysis Result` como conceitos centrais.

Também exige:

- Core independente de mecanismos externos;
- resultado JSON versionável;
- score separado da decisão;
- configuração que altera resultado como parte da rastreabilidade;
- múltiplas cues e occurrences evoluindo no mesmo núcleo;
- estruturas internas separadas dos contratos externos.

### Escopo Núcleo

- definir modelos centrais iniciais de Analysis, Cue e Occurrence;
- definir estados conceituais necessários antes da persistência;
- definir configuração efetiva de matching associada à análise;
- definir semântica temporal de occurrence;
- definir política inicial para múltiplas occurrences;
- definir deduplicação, agrupamento ou supressão quando necessária;
- suportar múltiplas cues em uma única análise;
- garantir que falha ou ausência de uma cue tenha representação explícita;
- definir `Analysis Result` estruturado;
- introduzir `schema_version` do resultado;
- registrar método e parâmetros relevantes no resultado;
- definir serialização JSON independente da futura API;
- validar reprodutibilidade do resultado para inputs/configuração conhecidos.

### Fora de Escopo

- SQLite;
- filesystem como storage oficial de assets;
- executor assíncrono;
- REST API;
- WebUI;
- autenticação;
- retenção de uploads;
- referências externas de assets;
- object storage;
- confidence calibrada;
- suporte a novos matchers além do necessário para validar o contrato.

### Entregáveis Esperados

- modelo conceitual inicial de Analysis;
- modelo de Cue;
- modelo de Occurrence;
- configuração de análise rastreável;
- política inicial de múltiplas occurrences;
- coordenação multi-cue;
- `Analysis Result` versionado;
- JSON exportável;
- regras explícitas de no-match e falha por cue quando aplicáveis;
- testes de unidade e integração do fluxo multi-cue;
- fixtures cobrindo repetição, ausência e sobreposição selecionada;
- contrato suficiente para persistência e exposição futura.

### Documentação da Implementação

- Estratégia aplicável: `milestones-only`
- Avaliação esperada: revisar se a criação simultânea de Core/Application/Matching ainda é facilmente navegável.
- Documentos candidatos: nenhum por padrão.
- Critério para criar: somente se a quantidade de contratos e áreas implementadas ultrapassar os gatilhos arquiteturais de navegação.
- Critério para atualizar: não se aplica enquanto não houver documentação acumulativa.
- Critério para não atualizar: criação normal dos modelos centrais e schemas não justifica mapa se arquitetura e código continuam fáceis de localizar.

### Dependências

- M1 concluída.
- M2 concluída com matcher considerado adequado para o baseline.

### Componentes ou Áreas Afetadas

- Core;
- Application;
- Acoustic Matching;
- Observability and Configuration;
- serialization/export de resultado como responsabilidade de fronteira ainda não HTTP.

### Issues Previstas ou Critérios de Derivação

- Critério: consolidar conceitos centrais.
  - Possível tipo de issue: core contracts.
  - Observação: tipos centrais não devem depender de Pydantic de transporte.

- Critério: ampliar single-cue para multi-cue.
  - Possível tipo de issue: application orchestration.
  - Observação: não criar pipeline paralelo.

- Critério: representar múltiplas occurrences.
  - Possível tipo de issue: occurrence policy.
  - Observação: deduplicação deve derivar de comportamento observado.

- Critério: formalizar resultado versionado.
  - Possível tipo de issue: result schema.
  - Observação: schema version não deve ser confundido com versão futura da API.

### Definition of Done

A milestone pode ser encerrada quando:

- Analysis, Cue e Occurrence possuem semântica clara e testes;
- uma Analysis pode coordenar mais de uma cue;
- uma cue pode produzir zero, uma ou várias occurrences;
- política de seleção/deduplicação está explicitamente definida;
- resultado contém score, método e informação de configuração necessária à interpretação;
- `schema_version` está presente;
- resultado JSON é determinístico para um caso conhecido;
- no-match não é confundido com falha;
- Core permanece livre de dependências de transporte e storage;
- contratos estão estáveis o suficiente para suportar persistência local.

### Evidência Mínima

- testes multi-cue;
- testes de múltiplas occurrences;
- exemplos versionáveis de resultado JSON;
- caso de no-match;
- caso de falha controlada;
- verificação de schema version;
- revisão de boundary confirmando ausência de infraestrutura no Core.

### Riscos e Lacunas

- a política ideal de múltiplas occurrences pode depender de dados reais;
- semântica de `end` temporal pode não ser justificável para todos os métodos;
- thresholds por cue podem ser necessários;
- resultado excessivamente detalhado pode tornar compatibilidade futura custosa;
- resultado simples demais pode perder rastreabilidade;
- futuras famílias de matcher podem exigir metadata adicional sem quebrar o contrato principal.

### Critérios de Derivabilidade

A milestone é derivável porque:

- conceitos centrais estão identificados na arquitetura;
- as dependências algorítmicas serão resolvidas por M2;
- escopo e limites estão claros;
- entregáveis podem ser testados sem banco ou API;
- riscos estão localizados e não exigem infraestrutura futura;
- a milestone entrega uma capacidade completa de análise em memória.

### Notas de Continuidade

M4 deve persistir e executar o modelo consolidado aqui, não criar um segundo modelo operacional paralelo.

Alterações incompatíveis no schema de resultado após M3 devem ser deliberadas e versionadas.

---

## M4 — Lifecycle Persistente de Analysis

### Objetivo

Transformar a análise em memória em uma operação persistente e potencialmente longa, com assets locais, estado transacional e execução desacoplada do lifecycle de uma chamada síncrona.

### Problema ou Lacuna

Mesmo com Analysis e Result definidos, o produto ainda não suporta:

- persistência entre operações;
- ownership de assets;
- estados `QUEUED`, `RUNNING`, `SUCCEEDED` e `FAILED`;
- execução fora do tempo de uma request;
- recuperação coerente após falhas;
- retenção e cleanup;
- correlação por `analysis_id`.

Essas capacidades são necessárias antes de expor a aplicação por REST.

### Contexto

A arquitetura escolhe:

- filesystem local para bytes;
- SQLite para estado e metadados;
- executor local com concorrência limitada;
- análise assíncrona em relação à API;
- ausência de broker distribuído;
- `analysis_id` como correlação principal;
- falhas explícitas;
- derived artifacts temporários por padrão.

### Escopo Núcleo

- implementar identidade lógica de Asset separada de path físico;
- armazenar source media e cues em filesystem controlado;
- registrar checksum para rastreabilidade;
- persistir Analysis e metadados em SQLite;
- implementar estados `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`;
- implementar transições válidas de lifecycle;
- coordenar execução local fora da chamada síncrona do caso de uso;
- limitar concorrência;
- persistir resultado ou referência ao resultado;
- normalizar e persistir erros;
- definir comportamento inicial para Analysis interrompida por restart;
- definir política inicial de retry, inclusive ausência explícita de retry automático se essa for a decisão;
- definir ownership e cleanup de temporários;
- definir retenção mínima para uploads e resultados;
- garantir correlação de logs por `analysis_id`.

### Fora de Escopo

- REST API pública;
- WebUI;
- broker de mensagens;
- worker distribuído;
- PostgreSQL ou banco externo;
- object storage;
- horizontal scaling;
- autenticação;
- multi-tenancy;
- referências externas de asset;
- cancelamento de Analysis, salvo se evidência mostrar necessidade imediata.

### Entregáveis Esperados

- Asset Storage local;
- Analysis Repository SQLite;
- lifecycle persistente;
- executor local;
- política de concorrência;
- política de recuperação após restart;
- política de retenção e cleanup inicial;
- errors persistidos e estruturados;
- logs correlacionáveis;
- testes de persistência;
- testes de transição de estado;
- testes de falha;
- validação de isolamento entre analyses.

### Documentação da Implementação

- Estratégia aplicável: `milestones-only`
- Avaliação esperada: esta milestone é o primeiro ponto em que várias áreas materiais passam a coexistir; avaliar explicitamente os gatilhos de revisão.
- Documentos candidatos: nenhum automaticamente.
- Critério para criar: somente se localizar storage, repository, executor e seus fluxos começar a exigir reconstrução recorrente de contexto.
- Critério para atualizar: não se aplica sem mapa existente.
- Critério para não atualizar: se arquitetura, milestones, nomes dos módulos e testes ainda fornecerem navegação suficiente, manter `milestones-only`.

### Dependências

- M3 concluída.
- Contratos de Analysis e Result estáveis o suficiente para persistência.

### Componentes ou Áreas Afetadas

- Application;
- Asset Storage;
- Analysis Repository;
- Local Analysis Executor;
- Observability and Configuration;
- Infrastructure;
- Core, apenas para estados e invariantes que pertençam de fato ao domínio da Analysis.

### Issues Previstas ou Critérios de Derivação

- Critério: persistir bytes sem expor paths como identidade.
  - Possível tipo de issue: asset storage.
  - Observação: deve incluir integridade e path safety.

- Critério: persistir lifecycle.
  - Possível tipo de issue: analysis repository.
  - Observação: SQL deve permanecer confinado ao adapter.

- Critério: executar fora da chamada síncrona.
  - Possível tipo de issue: local executor.
  - Observação: escolha entre thread/process deve usar evidência.

- Critério: manter estado coerente em falhas/restarts.
  - Possível tipo de issue: recovery semantics.
  - Observação: não introduzir broker apenas para resolver reinício local.

- Critério: gerenciar temporários.
  - Possível tipo de issue: retention and cleanup.
  - Observação: cleanup não pode apagar artifacts pertencentes a outra Analysis.

### Definition of Done

A milestone pode ser encerrada quando:

- source e cues recebem identidades lógicas independentes de paths;
- bytes são armazenados de forma controlada;
- Analysis é persistida no SQLite;
- lifecycle válido é aplicado e testado;
- execução pode ocorrer fora da chamada que criou a Analysis;
- concorrência possui limite explícito;
- sucesso persiste resultado;
- falha persiste estado e erro coerentes;
- restart possui comportamento documentado para analyses interrompidas;
- temporários possuem ownership e cleanup seguro;
- `analysis_id` correlaciona persistência e logs;
- testes de integração demonstram isolation entre analyses;
- nenhum broker ou storage remoto foi introduzido.

### Evidência Mínima

- testes de repository;
- testes de Asset Storage;
- testes de transições de estado;
- teste de execução assíncrona local;
- teste de falha de media processing ou matching;
- teste de restart/recovery conforme política escolhida;
- teste de cleanup seguro;
- inspeção de SQLite e filesystem demonstrando separação adequada de metadados e bytes.

### Riscos e Lacunas

- thread vs process ainda depende de benchmark;
- CPU-bound matching pode exigir processo em vez de thread;
- SQLite pode exigir configuração cuidadosa sob concorrência;
- política de recovery pode ser simples no baseline, mas precisa evitar Analysis eternamente em `RUNNING`;
- retenção pode consumir disco se não houver limites;
- checksum de arquivos grandes adiciona custo de ingest;
- retry automático pode repetir operações caras ou não idempotentes sem necessidade.

### Critérios de Derivabilidade

A milestone está pronta para derivação porque:

- storage e persistence estão definidos arquiteturalmente;
- lifecycle já possui estados iniciais;
- executor local é uma responsabilidade clara;
- não depende de API ou WebUI;
- riscos de concorrência podem ser tratados por validação interna;
- critérios de encerramento são objetivos.

### Notas de Continuidade

M5 deve expor o lifecycle já existente e não implementar jobs diretamente na camada HTTP.

Se M4 demonstrar que SQLite ou o executor local não atendem ao baseline, qualquer substituição deve ser justificada por evidência antes de ampliar a infraestrutura.

---

## M5 — REST API v1

### Objetivo

Expor as capacidades centrais do produto por uma REST API versionada, permitindo submissão de assets, criação de Analysis, consulta de estado e obtenção de resultado sem duplicar regras do Core ou Application.

### Problema ou Lacuna

O produto ainda não possui uma fronteira programática externa.

Sem API:

- consumidores externos não podem integrar a capacidade;
- a futura WebUI não possui uma interface estável;
- lifecycle persistente permanece apenas interno;
- schemas de request/response e erros externos ainda não estão formalizados.

### Contexto

A arquitetura define:

- FastAPI como framework REST inicial;
- Pydantic para contratos de transporte;
- `/api/v1` como boundary versionada;
- WebUI como cliente da API;
- Analysis como operação potencialmente longa;
- separação entre API request lifecycle e Analysis lifecycle;
- upload HTTP como caminho inicial standalone;
- resultado com versionamento próprio.

### Escopo Núcleo

- definir contratos externos de Asset, Analysis e Error;
- definir endpoints v1 necessários ao fluxo principal;
- suportar upload de source media;
- suportar upload de cues;
- criar Analysis a partir de assets válidos;
- retornar identificador da Analysis;
- representar criação assíncrona com semântica HTTP adequada;
- consultar estado;
- consultar/obter resultado;
- expor falhas de forma estruturada sem detalhes sensíveis;
- aplicar validação de tipos, tamanho e quantidade conforme limites definidos;
- produzir OpenAPI/documentação automática coerente;
- preservar versionamento separado entre API e result schema;
- testar contratos e integração com Application.

### Fora de Escopo

- WebUI;
- autenticação corporativa;
- multi-tenancy;
- quotas comerciais;
- upload resumível;
- streaming;
- object storage;
- referências arbitrárias por URI;
- WebSocket obrigatório;
- polling altamente otimizado;
- processamento distribuído;
- endpoints administrativos não necessários ao fluxo do produto;
- compatibilidade com múltiplas versões de API além da v1 inicial.

### Entregáveis Esperados

- REST API v1 funcional;
- schemas Pydantic de request/response;
- fluxo de upload/registro de source e cues;
- criação de Analysis;
- consulta de estado;
- obtenção de resultado;
- error schema;
- limites iniciais de upload e quantidade de cues;
- documentação OpenAPI;
- testes de contrato;
- testes de integração;
- validação de que a API não contém algoritmo acústico.

### Documentação da Implementação

- Estratégia aplicável: `milestones-only`
- Avaliação esperada: revisar os gatilhos porque API adiciona nova área material, mas não criar mapa automaticamente.
- Documentos candidatos: nenhum por padrão.
- Critério para criar: necessidade recorrente de navegar contratos, Application e adapters durante handoffs.
- Critério para atualizar: não se aplica sem documentação acumulativa.
- Critério para não atualizar: OpenAPI, arquitetura e testes de contrato são suficientes enquanto a API continuar pequena.

### Dependências

- M4 concluída.
- Lifecycle persistente e executor local disponíveis.
- Result schema de M3 disponível.

### Componentes ou Áreas Afetadas

- REST API;
- Application;
- Asset Storage;
- Analysis Repository;
- Observability and Configuration;
- segurança de upload;
- contratos de fronteira.

### Issues Previstas ou Critérios de Derivação

- Critério: formalizar schemas externos.
  - Possível tipo de issue: API contracts.
  - Observação: schemas de transporte não devem substituir tipos do Core.

- Critério: expor ingestão de assets.
  - Possível tipo de issue: API asset upload.
  - Observação: cliente não controla path interno.

- Critério: expor lifecycle.
  - Possível tipo de issue: analysis endpoints.
  - Observação: a API deve refletir, não redefinir, os estados persistidos.

- Critério: padronizar falhas.
  - Possível tipo de issue: API error handling.
  - Observação: não expor stack traces ou paths do host.

- Critério: validar fronteira.
  - Possível tipo de issue: contract/integration tests.
  - Observação: incluir comportamento 202/consulta posterior conforme contrato final.

### Definition of Done

A milestone pode ser encerrada quando:

- a API está versionada sob v1;
- source media e cues podem ser submetidos dentro dos limites suportados;
- uma Analysis pode ser criada programaticamente;
- criação retorna identidade persistente sem manter a request aberta até conclusão;
- status pode ser consultado;
- resultado de Analysis concluída pode ser obtido;
- falha produz resposta externa estruturada;
- OpenAPI descreve os contratos relevantes;
- testes de contrato e integração passam;
- a API não acessa matcher, SQLite ou filesystem por atalhos que contornem Application/ports;
- o result schema mantém sua própria versão.

### Evidência Mínima

- OpenAPI gerado;
- testes automatizados dos endpoints principais;
- exemplo de fluxo completo via cliente HTTP de teste;
- caso de sucesso;
- caso de no-match;
- caso de input inválido;
- caso de Analysis que termina em `FAILED`;
- verificação de status assíncrono;
- revisão das boundaries API/Application.

### Riscos e Lacunas

- limites máximos de upload podem precisar de ajuste após uso real;
- comportamento de idempotência da criação ainda precisa ser decidido;
- listagem de analyses pode não ser necessária no baseline;
- uploads grandes podem demonstrar limites do caminho HTTP;
- polling excessivo pode exigir estratégia de atualização futura;
- CORS e autenticação dependem do modo real de deployment;
- exposição remota continua fora do baseline confiável até controles adicionais serem avaliados.

### Critérios de Derivabilidade

A milestone é derivável porque:

- endpoints derivam diretamente do lifecycle e assets existentes;
- framework e versionamento estão definidos;
- WebUI não é dependência;
- os contratos externos podem ser testados isoladamente;
- itens de segurança e limites estão identificados;
- integrações avançadas permanecem fora do escopo.

### Notas de Continuidade

M6 deve tratar a API v1 como boundary real.

Se a WebUI exigir comportamento ausente, a mudança deve ser avaliada como evolução do contrato da API, não resolvida com acesso direto ao backend interno.

---

## M6 — WebUI Standalone

### Objetivo

Entregar uma experiência standalone completa para usuário humano, utilizando exclusivamente a REST API para submissão de mídia e cues, criação e acompanhamento de Analysis, visualização de resultados e download do JSON.

### Problema ou Lacuna

Apesar da capacidade programática, o produto ainda não oferece uma interface utilizável por pessoas sem escrever clientes de API.

Também permanecem pendentes:

- framework específico da WebUI;
- experiência de upload;
- representação de progresso;
- apresentação de occurrences e scores;
- nível inicial de visualização temporal.

### Contexto

A visão exige aplicação standalone demonstrável.

A arquitetura determina que:

- WebUI é cliente da REST API;
- não deve acessar Core, SQLite ou filesystem diretamente;
- visualização temporal é desejada, mas não deve bloquear a função principal;
- framework específico pode ser escolhido posteriormente;
- o fluxo humano deve reutilizar exatamente as mesmas capacidades programáticas.

### Escopo Núcleo

- selecionar framework de WebUI proporcional ao projeto;
- permitir seleção/upload de source media;
- permitir uma ou várias cues;
- iniciar Analysis pela API;
- acompanhar estado da Analysis;
- apresentar erros de forma compreensível;
- apresentar occurrences, timestamps, scores e método relevante;
- permitir download do JSON de resultado;
- fornecer visualização temporal mínima quando tecnicamente adequada;
- associar occurrences à timeline de forma compreensível;
- manter a UI responsiva ao lifecycle assíncrono;
- validar o fluxo completo como usuário standalone.

### Fora de Escopo

- editor de áudio;
- edição de vídeo;
- waveform avançada se exigir infraestrutura desproporcional;
- player profissional de mídia;
- autenticação complexa;
- multiusuário;
- colaboração;
- dashboards administrativos;
- configuração de algoritmos excessivamente detalhada na primeira UI;
- armazenamento remoto;
- temas ou customização visual extensiva;
- aplicação mobile nativa.

### Entregáveis Esperados

- WebUI funcional;
- fluxo completo de upload → Analysis → status → resultado;
- suporte a múltiplas cues;
- apresentação clara de no-match e falhas;
- tabela/lista de occurrences;
- representação temporal proporcional;
- download de JSON;
- tratamento de estados de loading;
- testes de componentes ou fluxo conforme stack selecionada;
- validação end-to-end contra a API real.

### Documentação da Implementação

- Estratégia aplicável: `milestones-only`
- Avaliação esperada: esta milestone cria uma segunda interface material e é um ponto obrigatório de revisão dos gatilhos da estratégia.
- Documentos candidatos: `implementation-map-single` somente se a navegação entre backend e frontend se tornar materialmente custosa.
- Critério para criar: handoffs começarem a exigir repetidamente explicação de onde ficam contratos, fluxo de API e principais áreas de UI.
- Critério para atualizar: não se aplica enquanto a estratégia não for revisada.
- Critério para não atualizar: se frontend e backend permanecerem pequenos, com fronteiras claras e documentação existente suficiente, manter `milestones-only`.

### Dependências

- M5 concluída.
- API v1 estável o suficiente para a WebUI consumir.
- Lifecycle e result schema disponíveis.

### Componentes ou Áreas Afetadas

- WebUI;
- REST API, apenas quando lacunas reais de contrato forem descobertas;
- Observability and Configuration;
- fluxo end-to-end standalone.

### Issues Previstas ou Critérios de Derivação

- Critério: escolher stack da WebUI.
  - Possível tipo de issue: frontend foundation.
  - Observação: decisão deve favorecer simplicidade, upload, polling e timeline.

- Critério: implementar fluxo de análise.
  - Possível tipo de issue: standalone analysis flow.
  - Observação: toda operação deve usar API.

- Critério: apresentar resultado.
  - Possível tipo de issue: result visualization.
  - Observação: score não deve ser rotulado como confidence sem calibração.

- Critério: introduzir timeline.
  - Possível tipo de issue: temporal visualization.
  - Observação: começar com representação mínima suficiente.

- Critério: validar caminho real.
  - Possível tipo de issue: end-to-end UI validation.
  - Observação: deve utilizar backend real, não lógica simulada como fonte final.

### Definition of Done

A milestone pode ser encerrada quando:

- um usuário consegue fornecer source e cues pela interface;
- a UI cria Analysis pela API;
- estados `QUEUED`, `RUNNING`, `SUCCEEDED` e `FAILED` são representados adequadamente;
- occurrences e scores são apresentados de forma compreensível;
- o JSON pode ser baixado;
- uma representação temporal mínima existe ou uma decisão justificada demonstra que deve ser postergada sem comprometer o fluxo principal;
- erros de entrada e processamento são apresentados sem detalhes sensíveis;
- a UI não possui caminho alternativo de matching;
- o fluxo completo funciona end-to-end em execução local;
- testes relevantes passam.

### Evidência Mínima

- teste end-to-end do fluxo standalone;
- captura ou registro demonstrando Analysis concluída pela WebUI;
- caso multi-cue;
- caso no-match;
- caso de erro;
- download validado do JSON;
- evidência de que as chamadas da UI utilizam a API pública;
- validação da visualização temporal, se introduzida.

### Riscos e Lacunas

- waveform pode ampliar muito o escopo;
- upload de arquivos grandes pode afetar UX;
- polling muito frequente pode gerar carga desnecessária;
- framework escolhido pode aumentar custo de manutenção sem benefício;
- visualização de scores pode induzir interpretação incorreta;
- múltiplas occurrences podem exigir decisões visuais não triviais;
- suporte de browser para preview de certos formatos pode diferir do suporte backend.

### Critérios de Derivabilidade

A milestone é derivável porque:

- a API define a boundary;
- o fluxo humano está claro;
- framework é decisão local que não altera arquitetura central;
- visualização avançada está explicitamente limitada;
- Definition of Done pode ser validada end-to-end;
- itens não essenciais permanecem fora de escopo.

### Notas de Continuidade

M7 deve consolidar a experiência já funcional em runtime reproduzível.

A WebUI não deve ser usada como justificativa para introduzir storage remoto, autenticação ou múltiplos serviços sem necessidade observada.

---

## M7 — Baseline Operacional Reproduzível

### Objetivo

Consolidar o produto em um baseline local reproduzível, seguro para o escopo declarado, observável, containerizável e demonstrável end-to-end.

A milestone deve transformar as capacidades já implementadas em uma primeira versão coerente do produto, sem expandir seu domínio.

### Problema ou Lacuna

Mesmo com backend, API e WebUI funcionais, o projeto ainda precisa demonstrar:

- instalação e execução reproduzíveis;
- empacotamento coerente das dependências;
- FFmpeg disponível de forma previsível;
- limites operacionais;
- cleanup e retenção adequados;
- observabilidade básica;
- tratamento consistente de erros;
- validação end-to-end em ambiente próximo ao uso real;
- documentação suficiente para operação e demonstração.

### Contexto

A arquitetura prevê containerização como consolidação do runtime, não como fundamento do desenho interno.

Também exige:

- local-first;
- segurança para mídia não confiável;
- limites de tamanho, duração, cues, timeout e recursos;
- logs estruturados;
- temporários com ownership;
- não versionamento de runtime local;
- revisão antes de qualquer exposição remota;
- ausência de infraestrutura distribuída sem evidência.

### Escopo Núcleo

- definir empacotamento reproduzível do backend;
- incluir ou declarar FFmpeg de forma controlada;
- empacotar WebUI de maneira compatível com a topologia local escolhida;
- preferir deployment simples;
- definir configuração segura por padrão;
- estabelecer limites iniciais de tamanho, duração, quantidade de cues e concorrência;
- estabelecer timeouts relevantes;
- validar sanitização e path safety;
- validar retenção e cleanup;
- consolidar logs estruturados por `analysis_id`;
- documentar operação local;
- documentar formatos oficialmente suportados;
- documentar limites e comportamento de erro;
- executar validação end-to-end em ambiente reproduzível;
- executar regressão funcional e benchmark mínimo do baseline;
- revisar se `milestones-only` continua adequada.

### Fora de Escopo

- cloud obrigatória;
- Kubernetes;
- alta disponibilidade;
- scaling horizontal;
- broker distribuído;
- object storage;
- autenticação corporativa;
- multi-tenancy;
- deployment público suportado sem revisão adicional;
- monitoramento corporativo;
- SLA;
- streaming;
- integrações externas por artifact URI sem caso de uso concreto;
- novos algoritmos apenas para aumentar escopo de portfólio.

### Entregáveis Esperados

- runtime local reproduzível;
- containerização ou empacotamento equivalente coerente com a arquitetura;
- dependências e versões controladas;
- FFmpeg disponível de forma previsível;
- configuração de limites;
- política de cleanup aplicada;
- observabilidade básica;
- documentação de execução local;
- documentação de formatos e limites;
- testes end-to-end;
- regressão automatizada;
- benchmark mínimo do fluxo principal;
- demonstração standalone;
- demonstração programática via API;
- revisão explícita da estratégia de documentação da implementação.

### Documentação da Implementação

- Estratégia aplicável: `milestones-only`
- Avaliação esperada: realizar revisão formal dos gatilhos definidos em `architecture.md`, pois ao final desta milestone Core, Infrastructure, API e WebUI já existirão materialmente.
- Documentos candidatos: `implementation-map-single` apenas se a revisão demonstrar necessidade real; `implementation-map-hierarchical` somente se um mapa único já seria excessivo.
- Critério para criar: custo recorrente de navegação/handoff ou dificuldade material para localizar responsabilidades.
- Critério para atualizar: somente se a estratégia for alterada e um documento acumulativo passar a existir.
- Critério para não atualizar: se código, testes, arquitetura e milestones continuarem suficientes, confirmar a permanência em `milestones-only`.
- Limite: qualquer mapa futuro deve orientar navegação, não servir como changelog, State operacional ou fonte absoluta.

### Dependências

- M1 a M6 concluídas.
- Fluxo standalone funcional.
- API v1 funcional.
- Policies básicas de lifecycle, storage e cleanup já definidas.

### Componentes ou Áreas Afetadas

- Core, apenas por regressão;
- Application;
- Media Processing;
- Acoustic Matching;
- Asset Storage;
- Analysis Repository;
- Local Analysis Executor;
- REST API;
- WebUI;
- Observability and Configuration;
- tooling de build, testes e packaging.

### Issues Previstas ou Critérios de Derivação

- Critério: tornar runtime reproduzível.
  - Possível tipo de issue: packaging/containerization.
  - Observação: não dividir serviços sem necessidade operacional.

- Critério: estabelecer limites.
  - Possível tipo de issue: resource and input guardrails.
  - Observação: limites devem refletir testes e uso local.

- Critério: consolidar cleanup e retenção.
  - Possível tipo de issue: operational hygiene.
  - Observação: não apagar inputs/results incorretamente.

- Critério: consolidar observabilidade.
  - Possível tipo de issue: structured logging/diagnostics.
  - Observação: evitar logs de payloads ou bytes de mídia.

- Critério: validar produto completo.
  - Possível tipo de issue: end-to-end/release validation.
  - Observação: incluir standalone e API.

- Critério: revisar documentação acumulativa.
  - Possível tipo de issue: documentation strategy review.
  - Observação: revisão não implica adoção automática de mapa.

### Definition of Done

A milestone pode ser encerrada quando:

- uma instalação limpa consegue iniciar o produto com procedimento documentado;
- backend, FFmpeg, storage local e WebUI funcionam no runtime suportado;
- o fluxo standalone completo é reproduzível;
- o fluxo programático completo é reproduzível;
- limites operacionais iniciais estão configurados e testados;
- uploads inválidos ou excessivos são rejeitados de forma controlada;
- subprocessos e paths respeitam as regras de segurança arquiteturais;
- cleanup e retenção não deixam temporários sem ownership;
- logs permitem acompanhar uma Analysis por `analysis_id`;
- testes automatizados e end-to-end passam;
- benchmark mínimo do fluxo principal está registrado;
- formatos suportados e limites estão documentados;
- nenhuma dependência de broker, storage remoto ou cloud foi introduzida sem necessidade;
- a estratégia de documentação da implementação foi revisada e a decisão resultante registrada.

### Evidência Mínima

- build ou empacotamento reproduzível validado;
- execução em ambiente limpo;
- teste end-to-end da WebUI;
- teste end-to-end da API;
- relatório de regressão;
- benchmark mínimo;
- testes de limites e input inválido;
- teste de cleanup;
- logs estruturados correlacionados;
- documentação de operação local;
- revisão registrada da estratégia `milestones-only`.

### Riscos e Lacunas

- containerização pode mascarar dependências se não houver validação em ambiente limpo;
- limites escolhidos podem precisar de revisão após uso real;
- segurança para exposição pública é mais ampla do que segurança local;
- suporte multi-OS pode aumentar matriz de testes;
- FFmpeg pode variar entre plataformas;
- benchmark local não define capacidade de produção remota;
- a primeira versão demonstrável pode revelar necessidade futura de asset references externos;
- essa necessidade não deve ser assumida antes de evidência.

### Critérios de Derivabilidade

A milestone está pronta para derivação porque:

- consolida capacidades já delimitadas em milestones anteriores;
- não introduz novo domínio funcional;
- packaging, segurança, observabilidade e validação possuem fronteiras claras;
- Definition of Done é verificável;
- itens de operação distribuída permanecem explicitamente fora de escopo;
- a revisão documental possui critérios definidos.

### Notas de Continuidade

A conclusão desta milestone representa um baseline independente e demonstrável do produto, não o fim de sua evolução.

Capacidades futuras como referências externas de assets, object storage, deployment remoto, autenticação, novos matchers ou maior escala só devem ser promovidas a novas milestones quando houver necessidade concreta, evidência e atualização compatível de visão/arquitetura quando necessário.

Nenhuma dessas extensões é presumida como próxima milestone por este documento.
