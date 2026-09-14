# Architecture

## Finalidade

Este documento consolida a arquitetura inicial do Audio Cue Locator a partir da visão aprovada do projeto.

Seu papel é definir decisões estruturais suficientes para orientar futuras milestones, issues, handoffs e validações sem antecipar detalhes de implementação que ainda dependem de evidência. Ele estabelece responsabilidades, dependências, fronteiras, fluxos, persistência, runtime e critérios de evolução.

Este documento não é um roadmap, backlog ou plano de execução. Ele também não substitui validação empírica dos algoritmos de processamento de áudio, contratos formais que venham a ser versionados ou documentação específica de implementação quando essa documentação se tornar necessária.

Quando houver divergência futura entre uma hipótese registrada aqui e evidência obtida durante implementação ou validação, a arquitetura deve ser revisada explicitamente em vez de preservar uma decisão inadequada por inércia.

## Resumo da Arquitetura

Audio Cue Locator será inicialmente uma **aplicação modular única**, organizada segundo uma combinação de **arquitetura em camadas leve** e **Ports and Adapters leve**.

A escolha evita tanto acoplamento direto entre interface, processamento de áudio e persistência quanto a complexidade de uma Clean Architecture rígida ou de uma arquitetura distribuída prematura.

A direção de dependências será:

```text
Interfaces
    ↓
Application
    ↓
Core

Infrastructure
    └── implementa ports requeridos por Application/Core
```

As principais áreas lógicas serão:

```text
WebUI
  ↓ HTTP
REST API
  ↓
Application
  ↓
Core
  ↓ ports
Infrastructure
  ├── media probing / decoding / canonicalization
  ├── acoustic matching
  ├── asset storage
  ├── analysis persistence
  └── local job execution
```

O backend será um único sistema implantável no estágio inicial. A WebUI será um cliente da API e poderá ser empacotada e servida junto ao backend ou separadamente sem alterar a fronteira lógica.

Análises serão representadas como operações com lifecycle próprio. A API não deverá manter uma requisição HTTP aberta durante todo o processamento. O modelo arquitetural será orientado a jobs assíncronos no nível da aplicação, mas sua execução inicial será local e limitada, sem broker ou fila distribuída.

O plano de controle e o plano de dados permanecerão distintos:

```text
Control Plane
    API, Analysis, status, configuração, IDs e resultados

Data Plane
    bytes de mídia, cues, artifacts derivados e referências de armazenamento
```

Para o baseline local:

- **Python** será a linguagem principal do backend e do processamento;
- **FastAPI** será o framework da API REST;
- **Pydantic** será usado para contratos de fronteira e validação de payloads;
- **FFmpeg** será o adapter principal para inspeção, extração, decodificação e canonicalização de mídia;
- **NumPy** e **SciPy** formarão a base numérica inicial para matching;
- **SQLite** armazenará estado e metadados da aplicação;
- **filesystem local** armazenará arquivos de mídia, cues, artifacts derivados quando necessários e resultados exportáveis;
- a WebUI será uma aplicação web cliente da API; seu framework específico permanece pendente.

Essa arquitetura é local-first, mas as interfaces de armazenamento e resolução de assets devem preservar a possibilidade de outras estratégias no futuro sem tornar armazenamento remoto requisito do núcleo.

## Contexto Derivado da Visão

A arquitetura precisa responder aos seguintes fatos e direções:

- o produto localiza amostras acústicas de referência dentro de mídias maiores;
- a entrada pode ser áudio ou vídeo;
- uma análise pode envolver uma ou várias cues;
- uma cue pode produzir uma ou várias ocorrências;
- timestamps e scores precisam ser estruturados e interpretáveis;
- a aplicação deve funcionar standalone;
- as mesmas capacidades centrais precisam ser acessíveis programaticamente;
- WebUI e API não podem implementar lógicas concorrentes de análise;
- arquivos de mídia podem ser grandes;
- processamento pode durar mais do que uma requisição HTTP interativa;
- execução local simples é uma prioridade;
- resultados precisam ser reproduzíveis, testáveis e versionáveis;
- a arquitetura não pode incorporar semântica específica de um domínio consumidor;
- a solução deve evitar infraestrutura distribuída enquanto não houver necessidade comprovada;
- métodos de matching, sample rate, thresholds e precisão precisam ser validados por evidência;
- o projeto poderá crescer em processamento, API, interface e integrações, mas não deve antecipar todo esse crescimento estruturalmente.

Esses fatores favorecem um núcleo pequeno e independente, uma camada de aplicação explícita e adapters substituíveis nas áreas ainda sujeitas a validação.

## Objetivos Arquiteturais

A arquitetura deve garantir:

- um núcleo de análise independente de HTTP, WebUI, banco de dados e filesystem;
- uma única fronteira de aplicação reutilizada por todas as interfaces;
- separação entre conceitos do produto e mecanismos técnicos;
- separação entre controle da análise e transporte/armazenamento dos bytes de mídia;
- capacidade de trocar ou comparar matchers sem reestruturar a aplicação;
- capacidade de alterar o mecanismo de armazenamento sem contaminar o modelo central;
- lifecycle explícito para análises potencialmente demoradas;
- API REST versionada e contratos externos estáveis;
- resultados estruturados, versionados e rastreáveis;
- execução local simples;
- comportamento determinístico sempre que o algoritmo e suas entradas permitirem;
- configuração explícita de parâmetros que afetam resultados;
- testabilidade das regras sem exigir dependências externas em todos os testes;
- observabilidade suficiente para diagnosticar uma análise por seu identificador;
- isolamento de detalhes de FFmpeg e bibliotecas numéricas atrás de responsabilidades claras;
- possibilidade de crescimento sem dependência precoce de cloud, broker de mensagens ou múltiplos serviços;
- custo de contexto moderado para manutenção humana e assistida por IA.

## Não Objetivos Arquiteturais

A arquitetura inicial não pretende resolver:

- processamento distribuído;
- auto-scaling;
- alta disponibilidade;
- execução multi-região;
- service mesh;
- event sourcing;
- CQRS;
- arquitetura orientada a eventos distribuídos;
- microserviços internos;
- filas externas obrigatórias;
- autenticação corporativa;
- multi-tenancy;
- billing ou quotas comerciais;
- streaming em tempo real;
- treinamento de modelos de machine learning;
- reconhecimento sem referência;
- classificação semântica de eventos acústicos;
- OCR ou visão computacional;
- edição de mídia;
- publicação em plataformas externas;
- integração obrigatória com object storage;
- abstração genérica para todo tipo possível de processamento de mídia;
- compatibilidade retroativa ilimitada com contratos ainda não publicados;
- suporte a todos os formatos que FFmpeg seja tecnicamente capaz de abrir.

O fato de uma dependência possuir determinada capacidade não transforma essa capacidade em suporte oficial do produto.

## Drivers Arquiteturais

### Simplicidade operacional

O projeto deve poder ser executado localmente com poucas dependências operacionais. Isso favorece um backend único, banco embarcado e filesystem no baseline.

### Processamento potencialmente demorado

Arquivos maiores e matching acústico podem exceder tempos apropriados para uma requisição HTTP síncrona. O lifecycle da análise precisa ser independente do lifecycle da request.

### Tamanho dos dados

Mídias podem ser grandes. A arquitetura deve permitir upload no modo standalone sem assumir que upload HTTP será a única estratégia futura de acesso a assets.

### Incerteza algorítmica

Matching acústico ainda precisa ser validado. O matcher é uma área de variação arquitetural explícita e não deve contaminar API, persistência ou WebUI.

### Reprodutibilidade

Resultados precisam poder ser explicados a partir de:

- assets utilizados;
- configuração de análise;
- método de matching;
- parâmetros relevantes;
- versão do contrato de resultado;
- versão da aplicação ou algoritmo quando necessária.

### Uso standalone e programático

A WebUI não é o produto inteiro. Ela é uma interface sobre as mesmas operações que consumidores externos utilizarão pela API.

### Segurança de mídia não confiável

Arquivos submetidos pelo usuário precisam ser tratados como dados não confiáveis. Decodificação, metadata probing, uso de filesystem e execução de subprocessos precisam ter limites claros.

### Crescimento incremental

A arquitetura precisa suportar crescimento, mas apenas onde há um eixo real de mudança: matcher, storage, interface, executor e formatos de mídia.

### Manutenção assistida

O projeto poderá ser mantido com assistência recorrente de IA e handoffs entre sessões. Fronteiras simples e módulos com responsabilidades claras são preferíveis a cadeias profundas de abstração.

## Princípios e Restrições

### 1. O Core não conhece interfaces externas

Core não deve depender de:

- FastAPI;
- HTTP;
- Pydantic usado exclusivamente como schema de transporte;
- SQLite;
- filesystem;
- FFmpeg;
- frameworks de WebUI;
- detalhes de processo ou container.

Tipos do Core devem representar o problema do produto, não sua exposição externa.

### 2. Interfaces chamam Application

REST API e quaisquer interfaces futuras devem invocar operações da camada Application.

Não deve existir um caminho especial de processamento exclusivo da WebUI.

### 3. Infrastructure implementa mecanismos

FFmpeg, filesystem, SQLite e bibliotecas numéricas pertencem à infraestrutura ou a adapters técnicos.

Seu comportamento pode ser usado pelo núcleo por meio de contratos internos apropriados, mas suas APIs não devem se tornar o modelo de domínio.

### 4. Abstrações precisam corresponder a eixos reais de mudança

Ports devem existir principalmente onde há necessidade concreta de substituição, teste ou isolamento.

Áreas justificadas desde o início incluem:

- resolução/leitura de assets;
- canonicalização de mídia;
- matching;
- persistência de análises;
- armazenamento de artifacts;
- execução assíncrona local.

Não deve haver uma interface para cada classe ou função apenas por padrão arquitetural.

### 5. Score não significa confidence automaticamente

O resultado deve expor o score produzido pelo método de matching e identificar o método utilizado.

O termo `confidence` não deve ser utilizado como sinônimo de score sem calibração que justifique essa interpretação.

### 6. Formatos suportados são contrato explícito

O conjunto oficial de formatos não deve ser derivado automaticamente do que FFmpeg aceita em uma instalação específica.

Formatos e limites suportados precisam ser documentados e testados.

### 7. Configuração que altera resultados é parte da rastreabilidade

Parâmetros como canonicalização, método de matching e threshold devem ser explicitamente associados à análise ou a uma configuração versionada.

### 8. Local-first não significa local-only

O baseline utilizará SQLite e filesystem. A arquitetura deve evitar acoplamento que impeça outro backend de assets no futuro, mas não deve implementar storage remoto antes de existir necessidade.

### 9. Assíncrono não significa distribuído

A análise deve possuir lifecycle assíncrono em relação à API, mas a execução inicial será local.

Broker, worker distribuído e processamento remoto só serão considerados mediante necessidade observável.

### 10. Artifacts derivados não são fonte absoluta

Áudio canonicalizado, previews, waveform data e caches podem ser regeneráveis.

A fonte de uma análise é composta pelas referências de entrada, configuração e contratos persistidos necessários para reproduzir ou interpretar o resultado.

### 11. Falha é um estado explícito

Erros de mídia, canonicalização, matching, persistência ou recursos devem produzir estado e erro estruturados.

Falhas não devem ser convertidas silenciosamente em detections vazias.

### 12. Evidência precede otimização

Estratégias mais complexas de matching, paralelismo ou armazenamento precisam ser introduzidas a partir de medição e testes representativos.

## Componentes ou Áreas Principais

### Core

Contém os conceitos estáveis do produto e regras que não dependem de mecanismos externos.

Conceitos esperados:

- `Analysis`;
- `Cue`;
- `Occurrence`;
- estado de análise;
- configuração semântica de matching;
- score;
- resultado da análise;
- erros ou invariantes centrais.

Os nomes finais e a granularidade exata dos tipos podem ser refinados durante definição dos contratos, desde que a fronteira permaneça independente de infraestrutura.

### Application

Coordena casos de uso e o lifecycle das análises.

Responsabilidades principais:

- registrar ou receber referências de assets;
- criar uma análise;
- validar pré-condições de alto nível;
- iniciar o processamento;
- consultar estado;
- obter resultado;
- coordenar canonicalização e matching;
- associar erros ao lifecycle;
- persistir transições relevantes;
- controlar a relação entre análise, cues e artifacts.

Application não deve implementar algoritmos numéricos nem lógica HTTP.

### Media Processing

Área de infraestrutura responsável por compreender a mídia como arquivo técnico.

Inclui:

- probing;
- identificação de streams de áudio;
- decodificação;
- extração de áudio de containers de vídeo;
- conversão para representação canônica;
- leitura de duração e metadata técnica necessária.

FFmpeg será o adapter inicial dessa área.

### Acoustic Matching

Área responsável por comparar áudio canonicalizado com cues canonicalizadas.

O baseline técnico será baseado em operações numéricas com NumPy/SciPy.

A primeira família de algoritmo a ser validada será matching por correlação normalizada ou técnica equivalente derivada dessa abordagem.

A arquitetura deve permitir substituir ou adicionar métodos sem alterar o contrato externo de Analysis.

A validação empírica ainda precisa determinar:

- método exato;
- forma de normalização;
- sample rate;
- janela;
- estratégia de busca;
- threshold;
- política de supressão ou agrupamento de ocorrências;
- comportamento diante de ruído e transformações.

### Asset Storage

Armazena bytes de:

- mídia de origem;
- cues;
- artifacts derivados quando persistência for necessária;
- resultado JSON exportável.

O baseline será filesystem local.

O storage deve trabalhar com identificadores internos, não com caminhos fornecidos diretamente como identidade pública do asset.

### Analysis Repository

Persiste metadados e estado de aplicação.

O baseline será SQLite.

Dados esperados incluem:

- identificadores;
- lifecycle;
- referências de assets;
- configuração;
- timestamps operacionais;
- referências de resultado;
- erros estruturados;
- metadata de rastreabilidade.

O banco não deve armazenar grandes blobs de mídia no baseline.

### Local Analysis Executor

Executa análises fora do tempo de vida da requisição HTTP.

Ele pertence ao backend, mas é uma responsabilidade separada do transporte REST.

Características esperadas:

- concorrência limitada;
- isolamento do event loop HTTP;
- atualização explícita de estado;
- propagação estruturada de falhas;
- possibilidade de recuperação ou marcação coerente após interrupção.

A técnica concreta de execução — thread, processo, pool ou worker local — permanece sujeita a benchmark e validação.

### REST API

Expõe a fronteira programática versionada.

Responsabilidades:

- validação de requests;
- upload ou registro de assets suportados;
- criação de Analysis;
- consulta de estado;
- obtenção de resultado;
- entrega de erros externos consistentes;
- serialização de contratos;
- documentação HTTP.

FastAPI e Pydantic formarão a base inicial dessa interface.

### WebUI

Oferece a experiência standalone para usuários humanos.

A WebUI deve:

- utilizar a REST API;
- permitir submissão das entradas;
- iniciar análises;
- acompanhar estado;
- apresentar detections;
- disponibilizar o resultado estruturado;
- representar temporalmente áudio e ocorrências quando essa capacidade for introduzida.

A WebUI não deve acessar SQLite, filesystem do backend ou Core diretamente.

O framework específico ainda será decidido.

### Observability and Configuration

Responsabilidade transversal, sem se tornar uma camada de negócio.

Inclui:

- configuração explícita;
- logs estruturados;
- identificação de análise nos logs;
- duração de estágios relevantes;
- erros operacionais;
- informações suficientes para diagnóstico local.

Observabilidade não deve registrar conteúdo sensível ou bytes de mídia.

## Responsabilidades

| Área | Responsável por | Não responsável por |
|---|---|---|
| Core | conceitos, invariantes e semântica central | HTTP, FFmpeg, SQL, filesystem, UI |
| Application | coordenação de casos de uso e lifecycle | correlação numérica, renderização, storage físico |
| Media Processing | decodificação e canonicalização | interpretar significado de cue |
| Acoustic Matching | localizar similaridade acústica | lifecycle HTTP, persistência, UI |
| Asset Storage | bytes e artifacts | regras de análise |
| Analysis Repository | estado e metadados | armazenar grandes mídias |
| Local Executor | execução desacoplada da request | contrato REST |
| REST API | exposição programática | algoritmo acústico |
| WebUI | interação humana | lógica de análise paralela |
| Observability/Configuration | diagnóstico e parâmetros explícitos | definir regras do domínio |

## Fronteiras e Boundaries

### Core vs Infrastructure

Core não importa mecanismos externos.

Infrastructure pode depender de tipos e ports internos necessários para cumprir contratos, mas não deve forçar tipos específicos de biblioteca para dentro do Core.

### Application vs Algorithm

Application solicita canonicalização e matching.

Ela não conhece detalhes como filtros FFmpeg, operações FFT, implementação de correlação ou layout de arrays NumPy.

### API vs Processing

A API gerencia requests e responses.

A análise continua independentemente da conexão HTTP após criação do job.

### WebUI vs Backend

A WebUI interage apenas por contratos HTTP públicos da aplicação.

Nenhum acesso direto ao banco ou aos diretórios internos é considerado interface suportada.

### Control Plane vs Data Plane

O Control Plane transporta:

- IDs;
- configuração;
- comandos de criação;
- estado;
- metadados;
- resultados estruturados.

O Data Plane transporta ou referencia:

- arquivos de mídia;
- cues;
- artifacts binários;
- derivados temporários.

Uploads HTTP pertencem ao Data Plane, mesmo quando iniciados por endpoints da mesma API.

### Asset Identity vs Storage Location

Um asset deve possuir identidade lógica independente do caminho físico.

A localização pode mudar sem alterar o conceito de asset.

Caminhos locais, URIs futuras ou chaves de object storage não devem aparecer como identidade de domínio.

### Source Media vs Derived Artifacts

A mídia/cue submetida é entrada da análise.

Áudio canonicalizado, dados de waveform ou caches são derivados e podem ter política de retenção diferente.

### Result Contract vs Internal Representation

O JSON externo deve possuir schema versionado.

Estruturas internas podem evoluir desde que compatibilidade externa seja preservada ou versionada adequadamente.

### Score vs Decision

Matcher produz score.

A regra que aceita ou rejeita uma ocorrência utiliza score e configuração explícita.

Essa distinção deve permanecer visível para permitir calibrar thresholds sem redefinir o algoritmo.

### Product Semantics vs Consumer Semantics

Audio Cue Locator conhece cues acústicas e ocorrências.

Ele não deve modelar o significado que outra aplicação atribui à ocorrência.

## Fluxos Principais

### Fluxo standalone

```text
usuário
→ WebUI
→ upload/registro da mídia
→ upload/registro das cues
→ criação da Analysis
→ Analysis ID
→ acompanhamento de estado
→ processamento local
→ resultado estruturado
→ visualização
→ download do JSON
```

A WebUI não executa matching local como fonte alternativa de resultados.

### Fluxo programático

```text
cliente
→ REST API
→ assets
→ criação da Analysis
→ 202 Accepted + Analysis ID
→ consulta de status
→ resultado
```

A semântica externa deve permitir que o cliente trate a análise como operação potencialmente longa.

### Fluxo interno de uma análise

```text
Analysis em QUEUED
→ executor reivindica a Analysis
→ RUNNING
→ resolve mídia e cues
→ valida/probe mídia
→ canonicaliza source
→ canonicaliza cues
→ executa matcher
→ agrega/filtra ocorrências
→ constrói resultado versionado
→ persiste resultado e metadados
→ SUCCEEDED
```

Em qualquer estágio:

```text
erro conhecido ou inesperado
→ normalização do erro
→ persistência do erro
→ FAILED
```

### Fluxo de mídia

```text
asset original
→ probe
→ seleção do stream de áudio
→ decode
→ canonicalização
→ representação adequada ao matcher
```

A canonicalização deve ser determinística para uma configuração fixa.

### Fluxo de matching

```text
source canonicalizado
+
cue canonicalizada
+
configuração
→ matcher
→ candidatos
→ score
→ threshold/política de seleção
→ Occurrence(s)
```

A política de múltiplas ocorrências ainda precisa ser validada, mas o modelo arquitetural deve suportá-la sem exigir mudança estrutural posterior.

### Fluxo futuro de referência externa

Não faz parte do baseline implementado, mas a fronteira deve permitir:

```text
cliente
→ referência de asset suportada
→ Asset Resolver
→ bytes acessíveis localmente ao processamento
→ pipeline normal
```

Esse fluxo não autoriza suporte genérico a qualquer URI. Cada esquema futuro deverá ser explicitamente suportado e validado.

## Dados, Artefatos ou Documentos Relevantes

### Asset

Representa uma entrada ou artifact identificável.

Metadata arquitetural relevante:

- identificador;
- tipo lógico;
- nome informativo sanitizado;
- media type detectado;
- tamanho;
- checksum;
- localização interna de storage;
- timestamps operacionais quando necessários.

O checksum deve auxiliar rastreabilidade e integridade. Ele não precisa ser a identidade pública do asset.

### Analysis

Representa uma solicitação de localização acústica.

Deve registrar, conceitualmente:

- identificador;
- estado;
- source asset;
- cues;
- configuração efetiva;
- timestamps de lifecycle;
- método ou configuração de matching;
- resultado ou referência ao resultado;
- erro estruturado quando aplicável.

Estados iniciais:

```text
QUEUED
RUNNING
SUCCEEDED
FAILED
```

Cancelamento não faz parte do contrato inicial.

### Cue

Representa uma referência acústica fornecida para comparação.

Ela deve possuir identidade dentro da Analysis e referência ao asset correspondente.

Metadata semântica opcional pode existir para apresentação, mas não deve introduzir significado de domínio no núcleo.

### Occurrence

Representa uma localização produzida pelo matcher.

Deve permitir, no mínimo:

- cue identificada;
- posição temporal;
- score;
- método de matching.

A presença e a semântica de um `end` temporal dependem do método e ainda precisam ser definidas. O contrato não deve inventar precisão que o algoritmo não consiga sustentar.

### Analysis Result

É o resultado externo estruturado.

Deve incluir:

- `schema_version`;
- `analysis_id`;
- estado final;
- informação suficiente sobre as cues;
- occurrences;
- scores;
- método;
- metadata de análise necessária à interpretação;
- erros, quando o contrato de consulta exigir exposição do estado de falha.

JSON será o formato exportável inicial.

### Canonical Audio

É uma representação derivada para processamento interno.

Não é contrato público primário.

Características que precisam ser explícitas:

- channels;
- sample rate;
- sample representation;
- duração;
- transformação aplicada.

**Decisão (M1-03):** o contrato interno `CanonicalAudioSpec`
(`src/audio_cue_locator/infrastructure/media_processing/canonical_audio.py`)
fixa `sample_rate_hz=48000`, `channels=1` (mono), `sample_format="float32"`
(amostras normalizadas em `[-1.0, 1.0]`), e normalização de amplitude
habilitada por padrão (peak normalization, pico alvo `1.0`, com silêncio
preservado sem divisão por zero). `FFmpegMediaAdapter.extract_audio`
(M1-02) não faz resample nem downmix por conta própria; a canonicalização
(resample para 48000 Hz, downmix para mono, conversão para float32 e
normalização de pico) é responsabilidade desta camada, aplicada sobre a
saída já decodificada pelo adapter. A justificativa técnica de cada
parâmetro está registrada em `CANONICAL_AUDIO_JUSTIFICATIONS`, no mesmo
módulo.

Esta decisão foi tomada por inspeção do código do adapter já existente e
por fatos estabelecidos de processamento digital de áudio (teorema de
Nyquist-Shannon; convenções numpy/scipy para correlação), e não por
evidência de probing/decode real (não-mockada) contra arquivos de mídia
representativos: ffmpeg/ffprobe não estavam disponíveis no ambiente desta
implementação (ver `PENDING_LIVE_PROBING_VERIFICATION` no mesmo módulo).
A decisão é explicitamente revisável: qualquer alteração futura, motivada
por essa verificação empírica pendente ou por evidência de matching, deve
ser explícita e acompanhada de regressão dos testes de mídia, conforme as
Notas de Continuidade de M1 — nunca uma redefinição silenciosa.

### Temporary Artifacts

Podem incluir:

- áudio extraído;
- áudio canonicalizado;
- dados para waveform;
- caches numéricos.

Esses artifacts não devem ser preservados indefinidamente por padrão. Uma política de retenção e cleanup deverá ser definida.

### Logs e métricas

São evidência operacional e diagnóstico, não fonte formal do resultado.

Logs devem referenciar `analysis_id` e evitar conteúdo desnecessário dos arquivos submetidos.

### Documentos versionados

Documentos de visão, arquitetura, milestones futuras, contratos e documentação operacional devem permanecer separados de artifacts runtime.

Runtime não deve reescrever documentação versionada como mecanismo de estado.

## Runtime, Tooling e Workflows

### Backend runtime

O backend será implementado em Python.

A versão exata deverá ser fixada no bootstrap do projeto usando uma versão suportada pelas dependências selecionadas.

O runtime deve reunir inicialmente:

- API;
- Application;
- Core;
- adapters;
- executor local;
- persistência local.

Isso constitui um modular monolith. A separação lógica não implica containers ou serviços separados.

### FastAPI e Pydantic

FastAPI será a fronteira REST inicial.

Pydantic será usado para:

- requests;
- responses;
- validação de fronteira;
- geração de schema/documentação da API.

Tipos Pydantic de transporte não devem ser reutilizados automaticamente como entidades do Core.

### FFmpeg

FFmpeg será dependência externa de runtime para media processing.

Seu uso deve permanecer encapsulado.

A aplicação não deve depender de strings de comando espalhadas por casos de uso.

O adapter deve tratar:

- probing;
- decode;
- seleção de áudio;
- canonicalização;
- timeout/falha;
- metadata técnica necessária.

### NumPy e SciPy

Serão a base inicial para processamento numérico.

Seu papel é permitir uma implementação eficiente e testável do matcher sem loops Python de alto custo para arquivos longos.

A arquitetura não exige que toda técnica futura seja implementada com SciPy; apenas estabelece um baseline adequado ao estágio atual.

### SQLite

Será o armazenamento inicial de estado e metadata.

É adequado ao baseline porque:

- execução local é prioritária;
- há apenas um backend inicialmente;
- não existe requisito de alta concorrência distribuída;
- oferece persistência transacional sem operação de banco externo.

Acesso a SQLite deve permanecer atrás de uma responsabilidade de repository para evitar espalhar SQL pelo Application.

### Filesystem

Será o storage inicial de bytes.

A organização física deve ser controlada pela aplicação/adapter e não pelo nome fornecido pelo usuário.

O filesystem armazenará apenas o que a política de retenção determinar.

### Executor local

O executor deve:

- ser limitado em concorrência;
- não bloquear diretamente o handling da request;
- trabalhar com Analysis IDs persistidos;
- produzir transições de estado explícitas.

A escolha entre thread pool, process pool ou processo de worker local será feita com base em benchmark e comportamento das dependências.

### WebUI runtime

A WebUI será uma aplicação web consumindo a API.

Seu framework ainda não foi escolhido porque essa decisão não altera a fronteira central.

A escolha futura deve favorecer:

- manutenção simples;
- upload de arquivos;
- polling ou atualização de status;
- visualização temporal;
- download do JSON;
- bom suporte a execução local.

### Containerização

Containerização é esperada, mas não define a arquitetura interna.

O objetivo será empacotar de forma reproduzível:

- runtime Python;
- dependências;
- FFmpeg;
- backend;
- WebUI quando apropriado.

A topologia inicial deve preferir uma implantação simples. Containers adicionais só devem existir quando houver responsabilidade operacional independente que os justifique.

### Workflows de desenvolvimento

O desenvolvimento deve preservar:

- testes unitários do Core e Application;
- testes de integração para adapters;
- fixtures de áudio reproduzíveis;
- testes de contrato da API;
- validação end-to-end do caminho standalone;
- benchmarks direcionados às decisões algorítmicas e de concorrência.

Benchmarks e datasets de validação devem informar decisões, não permanecer apenas como demonstrações manuais.

## Segurança, Versionamento e Rastreabilidade

### Arquivos submetidos são não confiáveis

A aplicação deve aplicar controles para:

- tamanho máximo;
- duração máxima quando aplicável;
- quantidade de cues;
- tipos aceitos;
- probing real da mídia;
- caminhos seguros;
- nomes de arquivo sanitizados para apresentação;
- timeout de processamento;
- uso limitado de recursos;
- cleanup de temporários.

A extensão do arquivo não deve ser a única forma de validação.

### Execução de FFmpeg

O adapter deve evitar construção de shell commands não sanitizados.

Entradas de usuário não devem ser interpoladas em comandos executados por shell como texto livre.

Falhas e timeouts precisam ser convertidos para erros estruturados.

### Filesystem

O cliente não deve controlar diretamente o caminho interno de destino.

Paths externos ou URIs futuras precisam passar por resolução e validação específicas.

Path traversal deve ser tratado como condição inválida.

### Exposição de rede

O baseline é local-first.

Autenticação não é requisito inicial para uso local confiável.

Se o serviço for exposto remotamente ou compartilhado entre usuários, autenticação, autorização, quotas, TLS, CORS e limites adicionais deverão ser reavaliados antes dessa exposição ser considerada suportada.

### Segredos

Não devem ser versionados:

- credenciais;
- tokens;
- `.env` real;
- chaves privadas;
- secrets de serviços externos.

Configuração versionada deve conter apenas valores seguros ou exemplos.

### Dados runtime

Não devem ser tratados como documentação versionável:

- banco SQLite local de execução;
- uploads;
- volumes;
- temporários;
- caches;
- logs brutos;
- artifacts gerados por análises reais.

Fixtures pequenas e explicitamente curadas para testes são uma categoria separada e podem ser versionadas quando legal e tecnicamente apropriado.

### API versioning

A API pública será versionada sob uma fronteira como:

```text
/api/v1
```

Mudanças incompatíveis devem produzir nova versão ou política explícita de migração.

### Result schema versioning

Resultado JSON deve carregar versão de schema própria.

A versão da API e a versão do resultado podem evoluir de forma relacionada, mas não precisam ser o mesmo conceito.

### Algorithm traceability

Uma ocorrência deve poder ser interpretada sabendo:

- método utilizado;
- configuração relevante;
- versão ou identidade do comportamento quando mudanças puderem alterar resultados.

Isso não implica versionar cada refatoração interna.

### Asset traceability

Assets devem ter metadata suficiente para confirmar quais bytes participaram da análise.

Checksum criptográfico, inicialmente SHA-256, é apropriado para integridade e rastreabilidade.

### Analysis traceability

`analysis_id` deve ser o identificador de correlação principal entre:

- API;
- persistência;
- logs;
- artifacts;
- resultado.

### Rastreabilidade de falhas

Falhas devem distinguir, em nível apropriado:

- entrada inválida;
- mídia não suportada;
- falha de decode/canonicalização;
- falha de matching;
- limitação de recursos;
- persistência;
- falha interna.

Mensagens externas não devem expor detalhes sensíveis do host.

## Estratégia de Documentação da Implementação

**Estratégia adotada: `milestones-only`**

### Justificativa

O projeto ainda está em estágio fundacional e não possui uma base implementada suficientemente grande para justificar um mapa acumulativo dedicado.

A arquitetura já separa áreas relevantes, mas criar agora um `implementation-map` produziria principalmente uma previsão da estrutura futura, e não um mapa de implementação material.

Nesse estágio:

- `vision.md` registra propósito e direção;
- `architecture.md` registra decisões e boundaries;
- `docs/milestones.md` deverá registrar evolução planejada;
- arquivos reais e testes serão a fonte material do estado implementado.

Essa combinação é suficiente até que a quantidade de áreas implementadas ou a frequência de handoffs torne a navegação materialmente custosa.

### Revisão ao final de M7

Com Core, Application, Infrastructure, REST API, WebUI e um runtime operacional reproduzível já existindo materialmente (M7-01 a M7-05), a estratégia foi revisada explicitamente contra os sete sinais abaixo, usando exemplos concretos pós-M7 em vez de um julgamento único.

| Sinal | Achado |
|---|---|
| Crescimento para várias áreas implementadas com arquivos principais difíceis de localizar | Não observado. A topologia atual de `src/audio_cue_locator/` (oito subpacotes -- `core`; `application`, com seu próprio `ports/`; `infrastructure`, dividido em `acoustic_matching`, `analysis_repository`, `asset_storage`, `execution` e `media_processing`; `interfaces/rest_api`; `observability` -- 34 arquivos Python) segue diretamente as fronteiras já documentadas neste arquivo; nenhum handoff de M7-01 a M7-06 registrou dificuldade em localizar um arquivo principal. |
| Repetição frequente das mesmas explicações em handoffs | Presente de forma limitada, mas atribuível à disciplina de reverificação independente que cada fase ASF (Specification/Analysis/State/Handoff/Control) exige, não à ausência de documentação: os fatos reconfirmados a cada fase (localização de arquivos, limites arquiteturais) foram idênticos e corretos em todas as reconfirmações, sem nenhuma correção necessária ao longo de M7-01 a M7-05. |
| Uma issue típica exigir leitura ampla de áreas não relacionadas | A leitura ampla observada (por exemplo, mais de vinte caminhos consultados por `intents/M7/M7-05/implementation-handoff.json`) correlaciona com a natureza de validação end-to-end daquela issue específica, não com um padrão típico: issues de escopo mais estreito (M7-02, M7-03) não exigiram leitura equivalente. |
| Dificuldade recorrente de identificar onde contratos ou responsabilidades estão implementados | Não observada. As fronteiras Core/Application/Infrastructure/Interfaces permanecem correspondidas de forma direta e verificável à estrutura de diretórios real. |
| Crescimento substancial de WebUI, API, processamento, storage e integrações em paralelo | Crescimento real, porém ainda modesto em escala absoluta (34 arquivos Python em `src/`, três subdiretórios em `webui/src/`); a divisão de `infrastructure` em cinco subáreas reflete separação intencional, não sinal de descontrole. Um `implementation-map-single` ainda cobriria essa topologia em um único documento pequeno, o que por si só não demonstra necessidade. |
| `milestones.md` começar a acumular descrição de estado implementado em vez de planejamento | Não observado. Busca no arquivo inteiro (1407 linhas, sete seções por milestone) não encontrou narrativa de estado implementado substituindo planejamento; a estrutura Objetivo/Problema/Escopo/Entregáveis Esperados permanece igual em todas as seções. |
| Custo de contexto para manutenção assistida deixar de ser pequeno | O custo de consulta multi-arquivo observado não se traduziu em retrabalho, erro ou issue bloqueada: as cinco issues de M7 concluídas (M7-01 a M7-05) e a própria preparação desta revisão (M7-06) completaram suas confirmações sem contradição. Este é o sinal mais próximo de ocorrer e deve ser o mais monitorado a partir de M8. |

Nenhum dos sete sinais está confirmado no momento desta revisão. A estratégia `milestones-only` permanece adotada e nenhum `docs/implementation-map.md` foi criado por esta revisão.

### Quando revisar a decisão

A estratégia deverá ser reavaliada se ocorrer um ou mais destes sinais:

- crescimento para várias áreas implementadas com arquivos principais difíceis de localizar;
- repetição frequente das mesmas explicações em handoffs;
- uma issue típica exigir leitura ampla de áreas não relacionadas;
- dificuldade recorrente de identificar onde contratos ou responsabilidades estão implementados;
- crescimento substancial de WebUI, API, processamento, storage e integrações em paralelo;
- `milestones.md` começar a acumular descrição de estado implementado em vez de planejamento;
- custo de contexto para manutenção assistida deixar de ser pequeno.

### Quando criar documentação acumulativa

Se a revisão demonstrar necessidade real, a primeira alternativa a avaliar deve ser `implementation-map-single`.

Se o projeto já tiver crescido a ponto de um mapa único ser grande ou carregar contexto irrelevante, deve-se avaliar diretamente `implementation-map-hierarchical`.

`implementation-index-assisted` só deve ser considerado quando manutenção manual se tornar concretamente custosa e houver validação humana do índice.

### Quando atualizar documentação acumulativa

Caso um mapa seja adotado no futuro, atualizá-lo apenas quando a mudança:

- cria uma nova área material;
- muda responsabilidade arquitetural observável;
- altera arquivos principais necessários para futuras mudanças;
- altera um fluxo importante;
- cria ou altera contrato estrutural;
- adiciona integração relevante;
- reduz ambiguidade real para navegação futura.

### Quando não atualizar

Não atualizar mapa futuro por:

- refatoração interna pequena;
- correção localizada;
- alteração cosmética;
- mudança textual;
- simples lista de arquivos tocados;
- repetição de informação já evidente em arquitetura ou milestone.

### Limites

Um Implementation Map futuro:

- não será changelog;
- não substituirá histórico de versionamento;
- não substituirá `architecture.md`;
- não substituirá `milestones.md`;
- não substituirá issues;
- não autorizará implementação;
- não será fonte absoluta sobre o código;
- deverá ser validado contra os arquivos reais quando usado em handoff.

A ausência de mapa durante a estratégia `milestones-only` é intencional e não representa documentação incompleta.

### Relação com futuras milestones, issues e handoffs

`docs/milestones.md` deverá refletir esta estratégia e avaliar sua revisão somente quando a complexidade justificar.

Issues e handoffs futuros poderão exigir atualização documental concreta apenas quando o escopo da mudança alterar informações que a estratégia vigente determine como acumulativas.

A execução concreta continuará sendo autorizada por issues ou handoffs apropriados, não por este documento nem por um mapa futuro.

## Impacto Esperado sobre Milestones

A arquitetura deve orientar o planejamento futuro sem predeterminar sua quantidade ou numeração.

O planejamento deve considerar que:

- canonicalização de mídia é dependência do matching;
- o matcher precisa ser validado antes que UI sofisticada ou escala operacional recebam investimento significativo;
- contratos centrais de Analysis, Cue, Occurrence e Result precisam estabilizar antes de interfaces externas dependerem deles amplamente;
- múltiplas cues e múltiplas ocorrências devem evoluir sobre o mesmo núcleo, não por pipelines paralelos;
- lifecycle assíncrono deve ser introduzido preservando o backend local simples;
- API deve refletir casos de uso já validados, não servir como local para definir regra de análise;
- WebUI deve evoluir sobre contratos da API;
- visualização temporal não deve bloquear a validação da função principal de localização;
- persistência externa e artifact URIs devem permanecer posteriores a uma necessidade concreta;
- containerização deve consolidar um runtime funcional, não esconder dependências ou decisões ainda instáveis;
- limites de recursos e segurança precisam aparecer antes que o sistema seja tratado como serviço remoto;
- milestones devem possuir validações observáveis e evitar misturar várias incertezas de alto risco no mesmo incremento;
- a estratégia `milestones-only` deve ser reavaliada somente quando o crescimento material justificar documentação acumulativa.

## Riscos e Trade-offs

### Modular monolith vs serviços separados

**Decisão:** modular monolith.

**Benefício:** menor custo operacional, testes e execução local mais simples, menos contratos de rede internos.

**Custo aceito:** escalabilidade independente das áreas não existe inicialmente.

**Revisão:** somente se carga, isolamento de falhas ou ciclo de deployment demonstrarem necessidade real.

### Ports and Adapters leve vs camadas rígidas

**Decisão:** utilizar ports apenas em boundaries úteis.

**Benefício:** isolamento de infraestrutura sem multiplicação artificial de interfaces.

**Risco:** disciplina é necessária para impedir imports de infraestrutura no Core/Application.

### FastAPI específico vs API framework-agnostic

**Decisão:** FastAPI é aceito na interface REST inicial.

**Benefício:** validação, OpenAPI e desenvolvimento simples.

**Custo:** a interface HTTP fica dependente do framework, o que é aceitável porque essa dependência fica confinada à borda.

### Filesystem + SQLite vs infraestrutura externa

**Decisão:** filesystem e SQLite no baseline.

**Benefício:** operação local mínima e persistência suficiente para o estágio.

**Custo:** não atende horizontal scaling ou múltiplos workers distribuídos.

**Revisão:** apenas quando o modo operacional deixar de ser local/single-backend.

### Jobs assíncronos locais vs processamento síncrono

**Decisão:** lifecycle assíncrono em relação à API.

**Benefício:** evita requests longas e prepara processamento de mídia real.

**Custo:** exige persistência de estado e política de recuperação.

### Executor local vs fila distribuída

**Decisão:** executor local com concorrência limitada.

**Benefício:** preserva simplicidade.

**Risco:** crash do processo pode interromper trabalhos em andamento.

A política exata de recuperação precisa ser definida.

### FFmpeg como boundary externo

**Decisão:** encapsular FFmpeg em adapter de media processing.

**Benefício:** amplo suporte técnico e canonicalização robusta.

**Riscos:** diferenças de build/plataforma, subprocessos, consumo de recursos e superfície de entrada não confiável.

### Matching por correlação como baseline vs métodos mais sofisticados

**Decisão:** validar correlação normalizada como primeira família de método.

**Benefício:** compreensível, reproduzível e adequada para estabelecer baseline.

**Risco:** pode falhar em casos com ruído, transformações ou mixagem.

A arquitetura preserva substituição do matcher sem afirmar que essa técnica será suficiente para todos os casos.

### Score bruto vs confidence calibrada

**Decisão:** score bruto/metodologicamente definido.

**Benefício:** evita semântica estatística não sustentada.

**Custo:** usuários podem precisar compreender thresholds e método.

### Upload HTTP vs referências externas

**Decisão:** upload é o caminho inicial standalone.

**Benefício:** experiência simples.

**Risco:** arquivos grandes podem tornar cópia e transferência ineficientes.

O Asset boundary preserva extensão futura para referências controladas.

### WebUI separada logicamente vs acesso direto ao backend interno

**Decisão:** WebUI usa API.

**Benefício:** valida a API como produto real e evita lógica duplicada.

**Custo:** mesmo na execução local há uma fronteira HTTP adicional.

Esse custo é aceito por ser parte central da visão.

### Persistir derivados vs regenerar

**Direção:** derivados devem ser temporários por padrão.

**Benefício:** reduz armazenamento e fonte de verdade duplicada.

**Risco:** regeneração possui custo computacional.

A política final depende de medição.

### Documentação mínima vs mapa acumulativo

**Decisão:** `milestones-only`.

**Benefício:** evita documento especulativo e manutenção duplicada.

**Risco:** poderá ficar insuficiente conforme o projeto crescer.

Há gatilhos explícitos para revisão.

## Lacunas e Decisões Pendentes

Ainda precisam de definição ou validação:

- nome definitivo do projeto;
- versão inicial de Python;
- framework específico da WebUI;
- lista inicial de formatos de áudio suportados;
- lista inicial de containers de vídeo suportados;
- tamanho e duração máximos aceitos;
- quantidade máxima inicial de cues por Analysis;
- verificação empírica (probing/decode real, não-mockado) dos parâmetros de
  áudio canônico decididos em M1-03 (`CanonicalAudioSpec`), pendente de
  ffmpeg/ffprobe disponíveis no ambiente de implementação;
- método preciso de correlação;
- uso de correlação direta ou baseada em FFT conforme tamanho;
- threshold padrão;
- forma de configurar thresholds por cue ou por análise;
- política para encontrar a melhor ocorrência ou todas as ocorrências;
- algoritmo de non-maximum suppression, deduplicação ou agrupamento quando necessário;
- tolerância mínima entre occurrences;
- semântica exata de início e fim temporal;
- precisão prometida para timestamps;
- comportamento quando nenhuma ocorrência é encontrada;
- forma de comparar scores produzidos por métodos diferentes no futuro;
- formato final de configuração de matcher;
- política de retenção de uploads;
- política de retenção de temporários;
- política de retenção de resultados;
- mecanismo de cleanup;
- estratégia concreta do executor local;
- concorrência padrão;
- comportamento após restart com Analysis em `RUNNING`;
- política de retry, se alguma;
- schema externo detalhado de Asset;
- schema externo detalhado de Analysis;
- schema de erro da API;
- endpoints finais e convenções de paginação/listagem, se necessárias;
- comportamento de idempotência na criação de análises;
- necessidade de listar análises no baseline;
- estratégia de geração e armazenamento de waveform;
- necessidade de preview de áudio;
- estratégia futura de `file://`, volume compartilhado ou outros asset references;
- critérios para introdução de object storage;
- autenticação e autorização para eventual deployment remoto;
- limites de CPU e memória;
- suporte oficial a sistemas operacionais fora do ambiente inicial;
- política de atualização/compatibilidade do FFmpeg;
- fixtures reais ou dataset de validação para medir qualidade;
- métricas quantitativas que determinarão se o baseline de matching é aceitável;
- limiar arquitetural que justificará abandonar SQLite/filesystem;
- política de versionamento de comportamento algorítmico quando resultados mudarem.

Essas lacunas não impedem a arquitetura inicial porque estão isoladas atrás de boundaries definidos ou dependem de evidência que deve ser produzida posteriormente.

## Critérios de Validação Arquitetural

A arquitetura será considerada adequada enquanto satisfizer os seguintes critérios.

### Alinhamento com a visão

- o sistema continua sendo um localizador de cues genérico;
- não incorpora semântica de um domínio consumidor;
- standalone e API utilizam a mesma capacidade central;
- execução local permanece viável;
- resultados permanecem estruturados e versionáveis.

### Dependências

- Core não depende de FastAPI, Pydantic de transporte, SQLite, filesystem ou FFmpeg;
- WebUI não depende diretamente do Core ou storage;
- Application não contém lógica de subprocesso ou detalhes de correlação numérica;
- Infrastructure não redefine regras centrais silenciosamente.

### Fronteiras

- asset identity é diferente de storage path;
- control plane é diferente de data plane;
- source inputs são diferentes de derived artifacts;
- score é diferente de confidence;
- API request lifecycle é diferente de analysis lifecycle;
- contratos externos são diferentes de estruturas internas.

### Proporcionalidade

- o baseline pode funcionar sem broker de mensagens;
- o baseline pode funcionar sem serviço de banco externo;
- o baseline pode funcionar sem object storage;
- nenhuma capability distribuída é exigida sem evidência;
- ports adicionais só são criados quando uma boundary concreta os justifica.

### Testabilidade

- Core e Application podem ser testados sem FFmpeg real na maioria dos testes;
- media adapters podem ser testados separadamente;
- matcher pode ser testado com fixtures determinísticas;
- API pode ser testada contra Application sem duplicar algoritmo;
- o fluxo completo pode ser validado end-to-end.

### Reprodutibilidade

Para uma configuração e assets conhecidos deve ser possível identificar:

- quais entradas foram usadas;
- qual canonicalização foi aplicada;
- qual método foi usado;
- quais parâmetros afetaram o resultado;
- qual schema descreve a saída.

### Operação

- falhas deixam Analysis em estado coerente;
- uma request HTTP encerrada não define o sucesso ou fracasso de uma análise já aceita;
- concorrência pode ser limitada;
- temporários possuem ownership identificável;
- cleanup pode ser executado sem apagar assets de outra Analysis indevidamente.

### Segurança

- mídia não confiável não controla paths internos;
- subprocessos não executam texto arbitrário do usuário;
- uploads possuem limites configuráveis;
- logs evitam segredos e dados binários;
- deployment remoto exige revisão de controles adicionais.

### Documentação

- `milestones-only` permanece suficiente enquanto a estrutura for pequena;
- documentação acumulativa só será introduzida quando reduzir ambiguidade real;
- nenhum mapa futuro substituirá investigação dos arquivos reais.

### Evolução

A arquitetura deve permitir, sem redefinir o Core:

- novo matcher;
- novo storage backend;
- novo mecanismo de execução;
- nova WebUI;
- referência externa de assets;
- persistência mais robusta caso escala futura justifique.

Permitir evolução não significa implementar essas alternativas antecipadamente.

## Notas para Futuras Milestones

O planejamento futuro deve:

- começar pelas capacidades que reduzem incerteza técnica do pipeline de áudio;
- validar canonicalização antes de depender dela em várias áreas;
- validar qualidade e custo do matcher com fixtures sintéticas e material representativo;
- estabelecer contratos centrais antes de expandir interfaces;
- introduzir persistência e lifecycle de jobs de maneira compatível com execução local;
- tratar API como fronteira real do produto;
- fazer a WebUI consumir exclusivamente a API;
- introduzir visualização temporal somente depois que o resultado fundamental for confiável;
- adicionar limites de segurança e recursos antes de considerar exposição remota;
- adiar storage remoto e execução distribuída até surgir um caso de uso concreto;
- manter cada incremento verificável e pequeno o suficiente para isolar hipóteses;
- registrar decisões que alterem boundaries ou custos de longo prazo;
- revisar a estratégia `milestones-only` apenas quando os gatilhos definidos neste documento ocorrerem;
- revisar este documento quando evidência técnica invalidar uma decisão estrutural, em vez de contornar a arquitetura com exceções locais.
