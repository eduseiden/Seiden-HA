# Seiden EVO Bridge 0.5.1

Integra leitores EVO Facial ao Home Assistant, publica eventos de presença, mantém o estado de ocupação, monitora a disponibilidade dos leitores e disponibiliza entidades operacionais para dashboards.

## Novidade da versão 0.5.1

A última fotografia capturada pelo leitor passa a ser disponibilizada automaticamente pelo add-on, sem necessidade de configurar manualmente uma câmera genérica no `configuration.yaml`.

A entidade criada é:

```text
sensor.seiden_evo_last_photo
```

O add-on baixa a fotografia indicada em `photo_url`, grava uma cópia atualizada em:

```text
/config/www/seiden_evo/latest.jpg
```

e publica a imagem por meio do atributo `entity_picture` com controle de cache.

## Card recomendado

```yaml
type: picture-entity
entity: sensor.seiden_evo_last_photo
name: Última passagem
show_name: true
show_state: false
show_entity_picture: true
fit_mode: contain
```

> Observação: esta entidade visual é publicada pelo add-on por meio da API de estados do Home Assistant. Ela exibe a última imagem estática e não oferece streaming ou serviços nativos de câmera.

## Arquiteturas suportadas

- `amd64`: mini PCs Intel/AMD
- `aarch64`: Raspberry Pi 5 e outros equipamentos ARM64

## Configuração nova

```yaml
publish_last_photo: true
photo_max_size_mb: 5
```

- `publish_last_photo`: habilita ou desabilita a publicação automática da última imagem.
- `photo_max_size_mb`: tamanho máximo aceito para a imagem baixada do leitor.

## Entidades principais

```text
binary_sensor.seiden_evo_bridge_running
sensor.seiden_evo_bridge_version
sensor.seiden_evo_bridge_uptime
sensor.seiden_evo_people_inside
sensor.seiden_evo_events_today
sensor.seiden_evo_entries_today
sensor.seiden_evo_exits_today
sensor.seiden_evo_last_person
sensor.seiden_evo_last_action
sensor.seiden_evo_last_reader
sensor.seiden_evo_last_event_time
sensor.seiden_evo_last_photo
```

Também é criada uma entidade de conectividade para cada leitor ativo.
