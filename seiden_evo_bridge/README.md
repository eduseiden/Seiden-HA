# Seiden EVO Bridge 0.4.5

Integra leitores EVO Facial ao Home Assistant, mantém o motor de ocupação e publica eventos e entidades operacionais.

## Eventos

- `seiden_presence`
- `seiden_reader_offline`
- `seiden_reader_online`

## Entidades operacionais

A versão 0.4.5 cria e atualiza diretamente no Home Assistant:

- `binary_sensor.seiden_evo_bridge_running`
- `sensor.seiden_evo_bridge_version`
- `sensor.seiden_evo_bridge_uptime`
- `sensor.seiden_evo_readers_online`
- `sensor.seiden_evo_readers_offline`
- `sensor.seiden_evo_readers_unknown`
- `sensor.seiden_evo_readers_status`
- `sensor.seiden_evo_people_inside`
- `binary_sensor.seiden_evo_building_occupied`
- `sensor.seiden_evo_events_today`
- `sensor.seiden_evo_entries_today`
- `sensor.seiden_evo_exits_today`
- `sensor.seiden_evo_last_person`
- `sensor.seiden_evo_last_action`
- `sensor.seiden_evo_last_reader`
- `sensor.seiden_evo_last_event_time`
- `binary_sensor.seiden_evo_reader_<nome_do_leitor>` para cada leitor ativo.

As entidades são atualizadas imediatamente após um evento e, para diagnóstico e uptime, a cada 60 segundos.

## Dashboard

O arquivo `dashboard_evo.yaml` contém um dashboard operacional completo usando apenas cards nativos do Home Assistant.

Para importar:

1. Abra **Configurações → Painéis**.
2. Crie um painel em modo YAML ou abra o editor de configuração bruta de uma nova visualização.
3. Copie o conteúdo de `dashboard_evo.yaml`.

## Observação técnica

As entidades são publicadas pela API de estados do Home Assistant. Após uma reinicialização do Home Assistant, elas voltam a aparecer assim que o EVO Bridge executar sua próxima atualização.


## Compatibilidade de arquitetura

A versão 0.4.5 pode ser construída e instalada em:

- Home Assistant OS em Intel/AMD 64 bits (`amd64`);
- Home Assistant OS em Raspberry Pi 5 (`aarch64`).

O arquivo `build.yaml` seleciona automaticamente a imagem-base adequada para cada arquitetura durante a construção do App.

## Dados da última fotografia

O evento `seiden_presence` e o estado persistente do último evento passam a incluir também `photo_filename`, além de `photo_url`. O sensor `sensor.seiden_evo_last_person` publica ambos como atributos.

O estado de `sensor.seiden_evo_last_action` é exibido em português (`Entrada` ou `Saída`), preservando o valor técnico original (`entered` ou `exited`) no atributo `action`.
