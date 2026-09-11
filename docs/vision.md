# Vision

## Finalidade

Este documento registra a direção macro do projeto Audio Cue Locator: seu propósito, valor central, limites, critérios de sucesso, restrições conhecidas, preferências de desenvolvimento e incertezas relevantes.

Ele deve servir como referência estável para decisões futuras sem substituir documentação arquitetural, planejamento de milestones, backlog, issues ou planos de implementação. Decisões técnicas detalhadas devem ser tomadas em etapas posteriores, com base em validação e evidências.

## Visão Geral do Projeto

Audio Cue Locator é um projeto independente para localizar amostras acústicas de referência dentro de arquivos maiores de áudio ou vídeo.

O projeto deve permitir que um usuário forneça uma mídia de origem e uma ou mais cues de referência e obtenha, como resultado, as ocorrências temporais encontradas, acompanhadas de métricas de similaridade e dados estruturados suficientes para interpretação humana e consumo programático.

O produto deve ser utilizável de forma standalone por meio de uma interface local e também expor suas capacidades essenciais por meio de uma API programática versionada. A experiência standalone e a integração por API devem representar duas formas de acesso ao mesmo núcleo funcional, e não implementações independentes da mesma lógica.

A direção geral é construir uma ferramenta tecnicamente coerente, demonstrável, reproduzível e adequada para uso real e para apresentação em portfólio, preservando simplicidade inicial e espaço para evolução posterior.

## Problema ou Oportunidade

Localizar manualmente um som específico dentro de uma gravação longa pode exigir inspeção repetitiva, reprodução manual da mídia e conhecimento prévio aproximado de onde o evento acústico ocorre.

Há uma oportunidade de transformar essa tarefa em uma capacidade genérica: fornecer uma amostra acústica conhecida e localizar automaticamente ocorrências semelhantes dentro de uma mídia maior.

O problema observado é, portanto, identificar de maneira reproduzível onde uma ou mais referências acústicas aparecem em um arquivo de áudio ou vídeo e representar esse resultado de forma clara e estruturada.

Também existe a oportunidade de tornar essa capacidade reutilizável por outras aplicações, sem incorporar semântica específica do domínio consumidor.

Hipóteses que ainda precisam de validação incluem a robustez dos métodos de matching diante de compressão, ruído, diferenças de volume, mixagem, transformações temporais e outras variações entre a cue de referência e sua ocorrência real.

## Público-Alvo ou Usuários

O projeto deve atender principalmente a:

- usuários que desejam localizar sons de referência em arquivos de áudio ou vídeo por meio de uma aplicação standalone;
- desenvolvedores que desejam consumir a capacidade de localização de cues programaticamente;
- projetos de processamento de mídia que precisam obter ocorrências temporais de eventos acústicos sem incorporar internamente toda a lógica de análise;
- mantenedores e colaboradores que precisem compreender, testar, reproduzir e evoluir o projeto.

Não é necessário que o usuário final conheça os algoritmos internos de processamento de áudio para utilizar a aplicação.

## Objetivo Principal

Permitir que uma mídia de áudio ou vídeo seja analisada contra uma ou mais cues acústicas de referência e produzir um resultado estruturado que indique onde cada cue foi encontrada, com timestamps, método utilizado e score de similaridade apropriado.

A mesma capacidade central deve poder ser utilizada por uma aplicação standalone e por consumidores programáticos.

## Objetivos Secundários

- oferecer uma experiência visual simples para submissão da mídia, acompanhamento da análise e inspeção dos resultados;
- representar temporalmente as ocorrências encontradas de maneira compreensível;
- permitir exportação dos resultados em formato estruturado, inicialmente JSON;
- oferecer contratos programáticos versionados;
- manter comportamento reproduzível e testável;
- oferecer tratamento explícito de falhas e entradas inválidas;
- permitir execução local simples;
- permitir containerização e distribuição reproduzível quando o projeto atingir esse estágio;
- preservar a possibilidade de integração futura com diferentes formas de armazenamento ou referência de artifacts;
- documentar conceitos, contratos e comportamento de forma suficiente para uso, manutenção e evolução;
- manter o projeto apresentável como produto independente de portfólio.

## Núcleo do Projeto

O núcleo da visão compreende:

- uma mídia de origem contendo áudio, diretamente ou por meio de um contêiner de vídeo;
- uma ou mais cues acústicas de referência;
- preparação ou canonicalização adequada do áudio para análise;
- comparação entre a mídia analisada e as referências;
- identificação de uma ou mais ocorrências temporais;
- produção de scores de similaridade ou métricas equivalentes;
- resultado estruturado e versionável;
- uma fronteira de aplicação que permita utilizar a mesma capacidade por interface humana e por API.

O núcleo deve permanecer genérico. Conceitos pertencentes a domínios consumidores não devem se tornar parte do modelo central de Audio Cue Locator.

## Funcionalidades ou Capacidades Desejadas

Em nível de visão, são desejadas as seguintes capacidades:

- selecionar, enviar ou referenciar uma mídia de áudio ou vídeo;
- selecionar, enviar ou referenciar uma ou várias cues;
- extrair ou acessar o áudio quando a mídia de origem for vídeo;
- analisar uma cue individual;
- analisar múltiplas cues em uma mesma mídia;
- representar múltiplas ocorrências de uma mesma cue quando aplicável;
- fornecer timestamps precisos das ocorrências;
- fornecer score de similaridade e informações suficientes para interpretar o resultado;
- expor o método de matching utilizado quando isso for relevante para interpretação ou auditoria;
- informar progresso ou estado de processamento quando a análise não puder ser concluída imediatamente;
- apresentar os resultados em uma interface visual;
- oferecer uma representação temporal do áudio e das detecções, quando tecnicamente adequada;
- permitir download do resultado estruturado;
- disponibilizar as capacidades essenciais por API REST versionada;
- produzir erros estruturados e compreensíveis;
- oferecer observabilidade básica compatível com uma aplicação local e integrável;
- suportar, futuramente, referências externas a artifacts sem exigir que toda integração dependa de upload repetido de arquivos grandes.

O conjunto real de formatos de entrada suportados deve ser definido posteriormente por validação técnica e documentação explícita.

## Fora de Escopo

Nesta visão inicial, ficam fora de escopo:

- interpretar semanticamente o significado de uma cue para um domínio consumidor;
- modelar conceitos específicos de jogos, partidas, rodadas, jogadores, campanhas ou outros domínios externos;
- OCR, análise de frames ou reconhecimento visual;
- edição, corte ou montagem automática de vídeo;
- publicação de mídia em plataformas externas;
- scheduling de publicação ou automações de distribuição;
- reconhecimento genérico de eventos sonoros sem uma referência ou modelo apropriado;
- treinamento de modelos de machine learning como requisito do núcleo inicial;
- transformação automática de um score de similaridade em probabilidade ou confiança estatisticamente calibrada sem metodologia que a sustente;
- streaming de áudio em tempo real como requisito inicial;
- processamento distribuído como requisito inicial;
- Kubernetes, service mesh ou infraestrutura de orquestração complexa como requisito inicial;
- Kafka ou outras plataformas de eventos distribuídos como requisito inicial;
- múltiplos bancos de dados como requisito inicial;
- autenticação corporativa, multi-tenancy ou gestão complexa de usuários como requisito inicial;
- dependência obrigatória de cloud ou object storage remoto.

Itens fora de escopo podem ser reavaliados no futuro se houver necessidade concreta, mas não devem orientar a fundação inicial do projeto.

## Restrições Conhecidas

- O projeto deve ser independente e utilizável sem depender de outro sistema.
- A aplicação standalone deve acessar as capacidades centrais por uma fronteira de aplicação clara; a interface visual não deve depender de uma implementação paralela da lógica de análise.
- As capacidades programáticas relevantes devem poder ser expostas por contratos versionados.
- Arquivos de mídia podem ser grandes; o desenho futuro não deve presumir que todo cenário de integração será melhor atendido por transferência repetida do arquivo completo via HTTP.
- Operações de análise podem ser demoradas e devem poder evoluir para um modelo apropriado de acompanhamento de estado sem exigir, de partida, infraestrutura distribuída.
- O projeto deve priorizar simplicidade proporcional ao estágio atual e evitar complexidade operacional sem necessidade demonstrada.
- Execução local deve permanecer uma forma válida de utilização.
- Resultados relevantes devem ser reproduzíveis e estruturados.
- Hipóteses algorítmicas devem ser validadas com testes e amostras representativas antes de serem tratadas como comportamento consolidado.
- Formatos de entrada, limites de tamanho, precisão temporal e características de matching não devem ser prometidos antes de validação técnica.

## Preferências do Usuário

- Desenvolvimento incremental, com pequenas alterações verificáveis.
- Auditoria do estado material relevante antes de mudanças importantes.
- Validação por evidência antes de avançar para uma nova etapa.
- Discussão de trade-offs quando houver mais de uma alternativa razoável.
- Fronteiras claras entre domínio, aplicação, infraestrutura e interfaces, sem exigir uma arquitetura específica antecipadamente.
- Separação clara entre capacidade central e detalhes de transporte ou armazenamento.
- API como interface real de aplicação, e não apenas uma camada decorativa sobre uma WebUI acoplada diretamente à implementação.
- Preferência por uma solução inicial simples, extensível e compreensível em vez de arquitetura distribuída prematura.
- Qualidade suficiente para que o projeto seja demonstrável em portfólio e reutilizável fora de um único caso de uso.
- Testes automatizados, configuração explícita, documentação, tratamento de erros, observabilidade básica e reprodutibilidade como características importantes da evolução do projeto.
- Ecossistema Python para processamento e backend é uma direção aceita neste estágio, mas a escolha final de componentes específicos deve ser confirmada durante a definição arquitetural.
- Uso de uma API HTTP versionada e de uma interface Web como cliente dessa fronteira é uma direção aceita, sem definir antecipadamente framework ou topologia de deployment.
- Tecnologias de processamento numérico e de mídia podem ser adotadas quando reduzirem complexidade e melhorarem robustez, desde que sua necessidade seja demonstrada.

## Critérios de Sucesso

A visão poderá ser considerada atendida quando, em alto nível:

- um usuário conseguir fornecer uma mídia e pelo menos uma cue de referência e obter localizações temporais úteis;
- o sistema conseguir trabalhar com áudio proveniente de arquivos de vídeo dentro dos formatos oficialmente suportados;
- o resultado identificar claramente a cue analisada, suas ocorrências e seus scores;
- múltiplas cues puderem ser analisadas de forma consistente;
- o resultado estruturado puder ser exportado e consumido programaticamente;
- a aplicação standalone oferecer um fluxo completo e compreensível de análise;
- uma API versionada oferecer acesso às capacidades centrais relevantes;
- a implementação central não depender de conceitos específicos de um domínio consumidor;
- o projeto puder ser executado, testado e demonstrado de forma reproduzível;
- erros de entrada e processamento forem reportados de maneira explícita e útil;
- a solução permanecer proporcional ao problema, sem depender de infraestrutura distribuída desnecessária;
- documentação suficiente existir para explicar propósito, uso, contratos e principais limites;
- integrações externas puderem consumir resultados sem acoplamento ao código interno do projeto.

## Riscos e Incertezas

- A robustez de template matching simples pode ser insuficiente diante de compressão, ruído, equalização, alterações de volume, mixagem ou pequenas transformações temporais.
- Ainda não está definido qual método ou conjunto de métodos de matching oferecerá o melhor equilíbrio entre precisão, desempenho e compreensibilidade.
- O significado de `score` precisa ser definido cuidadosamente. Scores produzidos por métodos diferentes podem não ser diretamente comparáveis.
- Uma métrica chamada `confidence` só deve ser exposta se houver fundamento para interpretá-la como confiança calibrada.
- O formato canônico de áudio e a taxa de amostragem adequada ainda precisam ser validados.
- O conjunto inicial de formatos de áudio e vídeo suportados ainda precisa ser definido.
- A precisão temporal necessária para diferentes tipos de cue ainda precisa ser caracterizada.
- Arquivos longos podem exigir otimizações específicas de memória, CPU ou estratégia de correlação.
- Não está definido se a primeira versão deve procurar apenas a melhor ocorrência ou todas as ocorrências acima de determinado critério.
- Cues semelhantes, sobrepostas ou repetidas podem gerar ambiguidades que precisarão de tratamento explícito.
- O comportamento desejado quando uma cue não é encontrada precisa ser formalizado.
- Upload por browser é apropriado para uso standalone, mas pode ser inadequado para integrações com arquivos muito grandes.
- Persistência de jobs, resultados e artifacts temporários precisa equilibrar simplicidade, recuperação e limpeza.
- Processamento de arquivos fornecidos pelo usuário introduz riscos de validação, consumo excessivo de recursos e segurança operacional.
- A representação visual do áudio pode aumentar bastante o escopo da interface se não for mantida proporcional ao objetivo central.
- O nome Audio Cue Locator é provisório até que seja confirmado como nome definitivo do projeto.

## Decisões Pendentes

- Confirmar o nome definitivo do projeto.
- Definir o formato canônico utilizado internamente para análise de áudio.
- Definir a taxa de amostragem e demais parâmetros de canonicalização.
- Definir os formatos de mídia oficialmente suportados na primeira versão.
- Escolher e validar o método inicial de matching.
- Definir se mais de um método de matching fará parte do núcleo inicial ou de evoluções posteriores.
- Definir a semântica precisa de score e thresholds.
- Definir a política de múltiplas ocorrências.
- Definir a granularidade e precisão dos timestamps.
- Definir o modelo inicial de Analysis, Cue, Occurrence e Result.
- Definir o ciclo de estados de uma análise.
- Confirmar se todas as análises da API serão tratadas como jobs ou se haverá também operações síncronas limitadas.
- Definir como assets enviados serão identificados, armazenados e limpos.
- Definir quando referências por filesystem, URI ou object storage devem ser suportadas.
- Definir a persistência mínima necessária para estado de análises e resultados.
- Definir frameworks e bibliotecas específicos para backend, processamento numérico e WebUI.
- Definir a forma inicial de empacotamento e containerização.
- Definir limites de tamanho, duração, concorrência e consumo de recursos.
- Definir se autenticação será necessária em algum modo de deployment futuro.
- Definir o nível inicial de visualização temporal oferecido pela WebUI.
- Definir critérios quantitativos de qualidade e conjuntos de dados ou fixtures para avaliar o matcher.

## Âncoras para Arquitetura

A futura definição arquitetural deve considerar as seguintes orientações:

- preservar um núcleo de análise independente de WebUI, protocolo HTTP e mecanismo de armazenamento;
- permitir que interfaces humanas e programáticas utilizem a mesma fronteira de aplicação;
- manter o modelo central genérico e livre de semântica específica de consumidores;
- separar claramente decisões de controle da análise das decisões sobre transporte e localização de arquivos;
- considerar explicitamente a distinção entre control plane e data plane quando arquivos grandes estiverem envolvidos;
- permitir processamento potencialmente demorado sem assumir, de partida, uma fila distribuída;
- manter contratos e resultados versionáveis;
- tratar decoder, canonicalização, matching e armazenamento como responsabilidades separáveis quando essa separação reduzir acoplamento;
- favorecer componentes substituíveis nas áreas em que ainda existem incertezas algorítmicas ou operacionais;
- priorizar execução local simples e preservar cloud como opção, não requisito;
- garantir comportamento observável, testável e reproduzível;
- usar erros estruturados e contratos explícitos nas fronteiras externas;
- evitar microserviços internos sem necessidade demonstrada;
- evitar infraestrutura operacional cuja complexidade exceda o estágio e a escala do projeto;
- considerar segurança e limites de recursos no processamento de mídia não confiável;
- preservar a possibilidade de crescimento sem projetar antecipadamente toda a infraestrutura futura.

Essas âncoras orientam análise futura e não constituem uma arquitetura definitiva.

## Âncoras para Milestones

A futura decomposição do trabalho deve preservar as seguintes prioridades:

- validar cedo a capacidade fundamental de preparar áudio de forma determinística;
- validar o matching acústico antes de investir em interfaces ou infraestrutura sofisticada;
- estabelecer conceitos e contratos estáveis antes de expandir integrações;
- provar inicialmente o caminho mais simples de análise antes de aumentar cardinalidade, formatos ou algoritmos;
- tratar múltiplas cues e múltiplas ocorrências como extensões naturais do comportamento fundamental, sem misturar prematuramente problemas distintos;
- introduzir API e lifecycle de análise somente sobre um núcleo já validado;
- evoluir a WebUI sobre contratos estáveis, evitando que decisões visuais ditem o modelo central;
- adiar mecanismos externos de artifacts e integrações avançadas até que o fluxo standalone esteja demonstrado;
- validar desempenho e robustez com evidência antes de ampliar promessas de escala;
- manter cada incremento pequeno o suficiente para permitir validação objetiva, sem fragmentar o trabalho em microtarefas sem valor demonstrável;
- preservar explicitamente itens fora de escopo para impedir crescimento silencioso.

Esta seção não define milestones nem sua numeração.

## Âncoras para Documentação da Implementação

O projeto apresenta sinais de que documentação acumulativa poderá ser útil no futuro:

- uso recorrente de assistência por IA durante desenvolvimento e revisão;
- possibilidade de handoff entre sessões;
- crescimento esperado em áreas distintas, como processamento de áudio, aplicação, API, interface visual e infraestrutura;
- necessidade de preservar decisões e reduzir reconstrução repetida de contexto;
- risco de documentação excessiva se mapas forem introduzidos cedo demais;
- risco de um único mapa se tornar grande e pouco consultável caso o projeto cresça substancialmente.

A estratégia de documentação da implementação deve ser decidida posteriormente, de forma proporcional ao tamanho real do projeto.

Como ponto de partida, deve ser considerada a possibilidade de permanecer apenas com documentação de visão, arquitetura e milestones enquanto isso for suficiente. Se a complexidade crescer, poderão ser avaliadas estratégias como:

- `milestones-only`;
- `implementation-map-single`;
- `implementation-map-hierarchical`;
- `implementation-index-assisted`;
- `not-applicable` para áreas ou mudanças em que documentação adicional não agregue valor.

Qualquer mapa futuro deve servir como auxílio de navegação e handoff, não como substituto dos arquivos reais ou como changelog automático.

## Notas para Próximos Passos

Os próximos passos documentais prováveis são:

- revisar e confirmar esta visão como referência inicial do projeto;
- realizar descoberta arquitetural antes de consolidar `docs/architecture.md`;
- validar as principais incertezas técnicas que possam alterar decisões arquiteturais;
- definir contratos conceituais iniciais sem antecipar detalhes desnecessários de implementação;
- consolidar posteriormente `docs/architecture.md` com decisões justificadas e limites explícitos;
- preparar `docs/milestones.md` somente após a direção arquitetural estar suficientemente compreendida;
- revisar periodicamente esta visão quando houver mudança real de propósito, escopo núcleo, restrições ou critérios de sucesso.

Questões técnicas ainda abertas devem permanecer como decisões pendentes até que sejam resolvidas por análise e evidência, sem serem promovidas automaticamente a requisitos permanentes.
