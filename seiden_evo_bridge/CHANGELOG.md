# Changelog

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
