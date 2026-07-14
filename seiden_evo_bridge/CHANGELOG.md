# Changelog

## 0.4.4

### Adicionado

- Entidades operacionais criadas diretamente no Home Assistant para uso em dashboards.
- Estado geral do Bridge, versão e uptime.
- Contadores de leitores online, offline e em verificação.
- Estado individual de conectividade para cada leitor ativo.
- Quantidade e lista de pessoas presentes.
- Contadores diários de movimentos, entradas e saídas.
- Informações da última pessoa, último movimento, último leitor e horário.
- Sensor consolidado com o estado de todos os leitores.
- Exemplo de dashboard operacional em `dashboard_evo.yaml`.

### Alterado

- O estado persistente passa a armazenar contadores diários e o último evento.
- As entidades operacionais são atualizadas após eventos e periodicamente a cada 60 segundos.
- O modo de espera sem leitores ativos também mantém as entidades do Bridge atualizadas.

## 0.4.3

### Corrigido

- IPs e nomes duplicados entre leitores desativados deixam de impedir
  a inicialização do Bridge.
- Leitor ativo e leitor desativado podem compartilhar temporariamente
  o mesmo IP ou nome.
- Apenas duplicidades entre leitores ativos são tratadas como erro
  operacional crítico.

### Alterado

- Duplicidade entre leitor ativo e desativado gera WARNING.
- Duplicidade apenas entre leitores desativados gera INFO.
- Leitores desativados continuam fora do polling, backoff e eventos.
- Validação estrutural foi separada da validação operacional.


## 0.4.2

### Corrigido

- Todos os leitores desativados deixam de causar encerramento crítico.
- O Bridge permanece ativo em modo de espera quando não há leitores ativos.
- Removida a duplicidade de traceback em falhas críticas.
- Melhorada a apresentação dos logs durante manutenção planejada.

### Alterado

- Erros de comunicação são resumidos no nível WARNING.
- A exceção completa permanece disponível no nível DEBUG.
- Leitores desativados também são validados na inicialização.
- Nenhum polling ou evento de disponibilidade é gerado quando todos
  os leitores estão desativados.


## 0.4.1

### Adicionado

- Opção `enabled` para cada leitor de entrada e saída.
- Possibilidade de desativar temporariamente um leitor sem removê-lo.
- Contagem de leitores ativos e desativados na inicialização.
- Identificação dos leitores desativados no log.

### Alterado

- Leitores desativados não realizam polling.
- Leitores desativados não geram backoff.
- Leitores desativados não geram eventos de disponibilidade.
- Configurações antigas sem `enabled` continuam sendo consideradas ativas.

## 0.4.0

### Adicionado

- Listas independentes para leitores de entrada e de saída.
- Configuração `entry_readers`.
- Configuração `exit_readers`.
- Direção determinada automaticamente pelo grupo do leitor.
- Contadores de leitores de entrada e saída na inicialização.
- Validação de nomes duplicados.
- Compatibilidade temporária com a configuração antiga `readers`.
- Configuração efetivamente carregada disponível no nível DEBUG,
  com senhas ocultadas.

### Alterado

- Removido o campo editável `direction` de cada leitor.
- A direção não depende mais do seletor gráfico do Home Assistant.
- Leitores em `entry_readers` são tratados internamente como `in`.
- Leitores em `exit_readers` são tratados internamente como `out`.

### Correção

- Corrigida a inconsistência em que o formulário mostrava `in`,
  mas o App continuava utilizando `out` internamente.


## 0.3.1

### Adicionado

- Data e hora em todas as mensagens do Seiden EVO Bridge.
- Níveis configuráveis de logging:
  - DEBUG
  - INFO
  - WARNING
  - ERROR
- Configuração `log_level` na interface do App.
- Logs detalhados de registros EVO no nível DEBUG.
- Padronização das mensagens com identificação do componente e leitor.

### Alterado

- Mensagens de indisponibilidade passam a usar o nível WARNING.
- Falhas de integração com o Home Assistant passam a usar o nível ERROR.
- Eventos operacionais normais passam a usar o nível INFO.


## 0.3.0

### Adicionado

- Backoff exponencial independente por leitor.
- Intervalo máximo de nova tentativa configurável.
- Timeout HTTP configurável.
- Evento `seiden_reader_offline`.
- Evento `seiden_reader_online`.
- Informação da duração da indisponibilidade.
- Validação da configuração na inicialização.
- Logs padronizados por leitor.
- Escrita atômica do estado persistente.
- Campo `building_occupied` no evento de presença.
- Campo `was_already_inside`.
- Campo `exit_without_entry`.

### Corrigido

- Duplicidade causada pela criação do registro antes da associação da foto.
- Alteração indevida do horário de entrada em autenticações repetidas.
- Indicação incorreta de última saída quando o usuário não constava como presente.
- Reinicialização diária dos indicadores de primeira entrada e última saída.

## 0.2.2

- Correções de indentação.
- Deduplicação dos eventos com e sem `photourl`.

## 0.2.0

- Occupancy Engine.
- Entrada e saída.
- Pessoas presentes.
- Primeira entrada.
- Última saída.
- Persistência de estado.

## 0.1.0

- MVP de comunicação com o EVO Facial.
- Leitura de logs.
- Publicação de eventos no Home Assistant.
