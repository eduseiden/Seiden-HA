import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any
import re

import requests


CONFIG_PATH = Path("/data/options.json")
STATE_PATH = Path("/data/occupancy_state.json")

DEFAULT_POLL_INTERVAL = 2
DEFAULT_REQUEST_TIMEOUT = 5
DEFAULT_MAX_RETRY_INTERVAL = 300
DEFAULT_LOG_LEVEL = "INFO"
BRIDGE_VERSION = "0.4.5"
DASHBOARD_PUBLISH_INTERVAL = 60

DEFAULT_PRESENCE_EVENT = "seiden_presence"
DEFAULT_READER_OFFLINE_EVENT = "seiden_reader_offline"
DEFAULT_READER_ONLINE_EVENT = "seiden_reader_online"

IDLE_SLEEP_SECONDS = 60

LOGGER = logging.getLogger("seiden_evo_bridge")


def setup_logging(log_level: str) -> None:
    """Configura o sistema de logs."""
    normalized_level = str(log_level).upper()

    valid_levels = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
    }

    numeric_level = valid_levels.get(
        normalized_level,
        logging.INFO,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="[%(asctime)s] [%(levelname)-7s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    LOGGER.handlers.clear()
    LOGGER.addHandler(handler)
    LOGGER.setLevel(numeric_level)
    LOGGER.propagate = False


def now_iso() -> str:
    """Retorna data e hora local no formato ISO."""
    return datetime.now().isoformat(timespec="seconds")


def today_str() -> str:
    """Retorna a data local atual."""
    return date.today().isoformat()


def load_config() -> dict[str, Any]:
    """Carrega as opções fornecidas pelo Home Assistant."""
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        config = json.load(file)

    if not isinstance(config, dict):
        raise RuntimeError(
            "A configuração do App não possui um objeto JSON válido"
        )

    return config


def sanitize_config_for_log(
    config: dict[str, Any],
) -> dict[str, Any]:
    """Oculta senhas antes de registrar a configuração."""
    sanitized = dict(config)

    for key in (
        "entry_readers",
        "exit_readers",
        "readers",
    ):
        sanitized[key] = [
            {
                **reader,
                "password": "***",
            }
            for reader in config.get(key, [])
            if isinstance(reader, dict)
        ]

    return sanitized


def normalize_reader(
    reader: dict[str, Any],
    direction: str,
) -> dict[str, Any]:
    """Normaliza um leitor para uso interno."""
    return {
        **reader,
        "enabled": reader.get("enabled", True),
        "direction": direction,
    }


def build_readers_from_config(
    config: dict[str, Any],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """
    Converte as listas de entrada e saída em leitores internos.

    Retorna:
    - todos os leitores;
    - leitores ativos;
    - leitores desativados.
    """
    entry_readers = config.get("entry_readers")
    exit_readers = config.get("exit_readers")

    new_configuration_present = (
        entry_readers is not None
        or exit_readers is not None
    )

    all_readers: list[dict[str, Any]] = []

    if new_configuration_present:
        entry_readers = entry_readers or []
        exit_readers = exit_readers or []

        if not isinstance(entry_readers, list):
            raise RuntimeError(
                "entry_readers deve ser uma lista"
            )

        if not isinstance(exit_readers, list):
            raise RuntimeError(
                "exit_readers deve ser uma lista"
            )

        for reader in entry_readers:
            if not isinstance(reader, dict):
                raise RuntimeError(
                    "Existe um leitor de entrada inválido"
                )

            all_readers.append(
                normalize_reader(
                    reader=reader,
                    direction="in",
                )
            )

        for reader in exit_readers:
            if not isinstance(reader, dict):
                raise RuntimeError(
                    "Existe um leitor de saída inválido"
                )

            all_readers.append(
                normalize_reader(
                    reader=reader,
                    direction="out",
                )
            )

    else:
        legacy_readers = config.get("readers", [])

        if legacy_readers:
            LOGGER.warning(
                "[CONFIG] Configuração antiga detectada em 'readers'. "
                "Migre os equipamentos para 'entry_readers' e "
                "'exit_readers'."
            )

        if not isinstance(legacy_readers, list):
            raise RuntimeError(
                "A configuração antiga 'readers' deve ser uma lista"
            )

        for reader in legacy_readers:
            if not isinstance(reader, dict):
                raise RuntimeError(
                    "Existe um leitor inválido na configuração antiga"
                )

            all_readers.append(
                normalize_reader(
                    reader=reader,
                    direction=reader.get("direction", "in"),
                )
            )

    active_readers = [
        reader
        for reader in all_readers
        if reader.get("enabled", True)
    ]

    disabled_readers = [
        reader
        for reader in all_readers
        if not reader.get("enabled", True)
    ]

    return (
        all_readers,
        active_readers,
        disabled_readers,
    )


def default_state() -> dict[str, Any]:
    """Cria o estado inicial do Occupancy Engine."""
    return {
        "date": today_str(),
        "people_inside": {},
        "first_entry_today": None,
        "last_exit_today": None,
        "entries_today": 0,
        "exits_today": 0,
        "events_today": 0,
        "last_event": None,
    }


def load_state() -> dict[str, Any]:
    """Carrega o estado persistente de ocupação."""
    if not STATE_PATH.exists():
        LOGGER.info(
            "[STATE] Nenhum estado anterior encontrado. "
            "Um novo estado será criado."
        )
        return default_state()

    try:
        with STATE_PATH.open("r", encoding="utf-8") as file:
            state = json.load(file)

    except (OSError, json.JSONDecodeError) as error:
        LOGGER.error(
            "[STATE] Não foi possível carregar o estado: %s",
            error,
        )
        LOGGER.warning(
            "[STATE] Um novo estado será iniciado."
        )
        return default_state()

    if not isinstance(state, dict):
        LOGGER.error(
            "[STATE] O arquivo de estado não possui um objeto válido."
        )
        return default_state()

    state.setdefault("date", today_str())
    state.setdefault("people_inside", {})
    state.setdefault("first_entry_today", None)
    state.setdefault("last_exit_today", None)
    state.setdefault("entries_today", 0)
    state.setdefault("exits_today", 0)
    state.setdefault("events_today", 0)
    state.setdefault("last_event", None)

    if not isinstance(state["people_inside"], dict):
        LOGGER.error(
            "[STATE] people_inside inválido. "
            "A ocupação será reiniciada."
        )
        state["people_inside"] = {}

    reset_daily_state_if_needed(state)

    return state


def save_state(state: dict[str, Any]) -> None:
    """Salva o estado persistente usando escrita atômica."""
    temporary_path = STATE_PATH.with_suffix(".tmp")

    try:
        with temporary_path.open("w", encoding="utf-8") as file:
            json.dump(
                state,
                file,
                ensure_ascii=False,
                indent=2,
            )

        temporary_path.replace(STATE_PATH)

    except OSError:
        LOGGER.exception(
            "[STATE] Falha ao salvar o estado persistente."
        )
        raise


def reset_daily_state_if_needed(
    state: dict[str, Any],
) -> None:
    """
    Reinicia os indicadores diários quando a data muda.

    Pessoas que permaneceram após a meia-noite continuam presentes.
    """
    current_date = today_str()

    if state.get("date") == current_date:
        return

    previous_date = state.get("date")

    state["date"] = current_date
    state["first_entry_today"] = None
    state["last_exit_today"] = None
    state["entries_today"] = 0
    state["exits_today"] = 0
    state["events_today"] = 0

    save_state(state)

    LOGGER.info(
        "[STATE] Novo dia iniciado: %s → %s",
        previous_date,
        current_date,
    )


def evo_cmd(
    reader: dict[str, Any],
    command: str,
    request_timeout: int,
    **kwargs: Any,
) -> dict[str, Any]:
    """Executa um comando na API HTTP do leitor EVO."""
    payload = {
        "password": reader["password"],
        "cmd": command,
    }
    payload.update(kwargs)

    url = f"http://{reader['ip']}/api"

    LOGGER.debug(
        "[EVO][%s] Requisição para %s: %s",
        reader["name"],
        url,
        {
            key: value
            for key, value in payload.items()
            if key != "password"
        },
    )

    response = requests.post(
        url,
        json=payload,
        timeout=request_timeout,
    )

    response.raise_for_status()

    data = response.json()

    if not isinstance(data, dict):
        raise ValueError(
            "A API do EVO retornou uma resposta inválida"
        )

    LOGGER.debug(
        "[EVO][%s] Resposta do comando %s: %s",
        reader["name"],
        command,
        data,
    )

    return data


def fire_ha_event(
    supervisor_token: str,
    event_type: str,
    payload: dict[str, Any],
    request_timeout: int,
) -> None:
    """Publica um evento no barramento do Home Assistant."""
    headers = {
        "Authorization": f"Bearer {supervisor_token}",
        "Content-Type": "application/json",
    }

    response = requests.post(
        f"http://supervisor/core/api/events/{event_type}",
        headers=headers,
        json=payload,
        timeout=request_timeout,
    )

    response.raise_for_status()


def safe_fire_ha_event(
    supervisor_token: str,
    event_type: str,
    payload: dict[str, Any],
    request_timeout: int,
) -> bool:
    """Publica um evento sem encerrar o Bridge em caso de falha."""
    try:
        fire_ha_event(
            supervisor_token=supervisor_token,
            event_type=event_type,
            payload=payload,
            request_timeout=request_timeout,
        )

        LOGGER.debug(
            "[HA] Evento publicado: %s | payload=%s",
            event_type,
            payload,
        )

        return True

    except requests.RequestException as error:
        LOGGER.error(
            "[HA] Não foi possível publicar o evento %s: %s",
            event_type,
            error,
        )
        return False



def slugify_entity(value: str) -> str:
    """Converte um nome em identificador seguro para entidade do HA."""
    normalized = re.sub(r"[^a-z0-9_]+", "_", str(value).strip().lower())
    return re.sub(r"_+", "_", normalized).strip("_") or "reader"


def set_ha_state(
    supervisor_token: str,
    entity_id: str,
    state: Any,
    attributes: dict[str, Any],
    request_timeout: int,
) -> bool:
    """Cria ou atualiza uma entidade operacional no Home Assistant."""
    headers = {
        "Authorization": f"Bearer {supervisor_token}",
        "Content-Type": "application/json",
    }
    payload = {"state": str(state), "attributes": attributes}

    try:
        response = requests.post(
            f"http://supervisor/core/api/states/{entity_id}",
            headers=headers,
            json=payload,
            timeout=request_timeout,
        )
        response.raise_for_status()
        return True
    except requests.RequestException as error:
        LOGGER.error(
            "[HA] Não foi possível atualizar a entidade %s: %s",
            entity_id,
            error,
        )
        return False


def publish_reader_entity(
    supervisor_token: str,
    reader: dict[str, Any],
    runtime: dict[str, Any],
    request_timeout: int,
) -> None:
    """Publica o estado operacional individual de um leitor."""
    reader_slug = slugify_entity(reader["name"])
    status = runtime.get("status", "unknown")
    entity_state = "on" if status == "online" else "off"

    set_ha_state(
        supervisor_token=supervisor_token,
        entity_id=f"binary_sensor.seiden_evo_reader_{reader_slug}",
        state=entity_state,
        attributes={
            "friendly_name": f"EVO {reader['name']}",
            "device_class": "connectivity",
            "reader_name": reader["name"],
            "reader_ip": reader["ip"],
            "direction": reader["direction"],
            "operational_status": status,
            "failure_count": runtime.get("failures", 0),
            "last_error": runtime.get("last_error"),
            "offline_since": runtime.get("offline_since_iso"),
            "last_success": runtime.get("last_success_iso"),
            "last_event": runtime.get("last_event"),
            "icon": "mdi:face-recognition",
        },
        request_timeout=request_timeout,
    )


def publish_operational_entities(
    supervisor_token: str,
    readers: list[dict[str, Any]],
    reader_runtime: dict[str, dict[str, Any]],
    state: dict[str, Any],
    started_monotonic: float,
    request_timeout: int,
) -> None:
    """Publica as entidades usadas pelo dashboard operacional."""
    runtimes = [reader_runtime[reader["ip"]] for reader in readers]
    online = sum(1 for runtime in runtimes if runtime.get("status") == "online")
    offline = sum(1 for runtime in runtimes if runtime.get("status") == "offline")
    unknown = len(readers) - online - offline
    people = list(state.get("people_inside", {}).values())
    last_event = state.get("last_event") or {}
    uptime_seconds = int(time.monotonic() - started_monotonic)

    common = {"integration": "Seiden EVO Bridge", "bridge_version": BRIDGE_VERSION}
    reader_statuses = [
        {
            "name": reader["name"],
            "ip": reader["ip"],
            "direction": reader["direction"],
            "status": reader_runtime[reader["ip"]].get("status", "unknown"),
            "failure_count": reader_runtime[reader["ip"]].get("failures", 0),
            "last_success": reader_runtime[reader["ip"]].get("last_success_iso"),
            "last_error": reader_runtime[reader["ip"]].get("last_error"),
            "last_event": reader_runtime[reader["ip"]].get("last_event"),
        }
        for reader in readers
    ]

    entities = [
        ("binary_sensor.seiden_evo_bridge_running", "on", {**common, "friendly_name": "EVO Bridge", "device_class": "running", "icon": "mdi:bridge"}),
        ("sensor.seiden_evo_bridge_version", BRIDGE_VERSION, {**common, "friendly_name": "Versão EVO Bridge", "icon": "mdi:tag-outline"}),
        ("sensor.seiden_evo_bridge_uptime", uptime_seconds, {**common, "friendly_name": "Uptime EVO Bridge", "unit_of_measurement": "s", "device_class": "duration", "state_class": "measurement", "icon": "mdi:timer-outline"}),
        ("sensor.seiden_evo_readers_online", online, {**common, "friendly_name": "Leitores EVO online", "icon": "mdi:lan-connect"}),
        ("sensor.seiden_evo_readers_offline", offline, {**common, "friendly_name": "Leitores EVO offline", "icon": "mdi:lan-disconnect"}),
        ("sensor.seiden_evo_readers_unknown", unknown, {**common, "friendly_name": "Leitores EVO verificando", "icon": "mdi:lan-pending"}),
        ("sensor.seiden_evo_readers_status", f"{online}/{len(readers)}", {**common, "friendly_name": "Estado dos leitores EVO", "readers": reader_statuses, "online": online, "offline": offline, "unknown": unknown, "icon": "mdi:server-network"}),
        ("sensor.seiden_evo_people_inside", len(people), {**common, "friendly_name": "Pessoas presentes", "people_inside": people, "names": [person.get("user_name") for person in people], "icon": "mdi:account-group"}),
        ("binary_sensor.seiden_evo_building_occupied", "on" if people else "off", {**common, "friendly_name": "Ambiente ocupado", "device_class": "occupancy", "people_inside": len(people)}),
        ("sensor.seiden_evo_events_today", state.get("events_today", 0), {**common, "friendly_name": "Movimentos hoje", "icon": "mdi:counter"}),
        ("sensor.seiden_evo_entries_today", state.get("entries_today", 0), {**common, "friendly_name": "Entradas hoje", "icon": "mdi:login"}),
        ("sensor.seiden_evo_exits_today", state.get("exits_today", 0), {**common, "friendly_name": "Saídas hoje", "icon": "mdi:logout"}),
        ("sensor.seiden_evo_last_person", last_event.get("user_name", "Nenhum evento"), {**common, "friendly_name": "Última pessoa", "user_id": last_event.get("user_id"), "photo_url": last_event.get("photo_url"), "photo_filename": last_event.get("photo_filename"), "icon": "mdi:account-clock"}),
        ("sensor.seiden_evo_last_action", action_label(last_event.get("action")), {**common, "friendly_name": "Último movimento", "action": last_event.get("action", "none"), "direction": last_event.get("direction"), "icon": "mdi:swap-horizontal"}),
        ("sensor.seiden_evo_last_reader", last_event.get("reader_name", "Nenhum evento"), {**common, "friendly_name": "Último leitor", "reader_ip": last_event.get("reader_ip"), "icon": "mdi:face-recognition"}),
        ("sensor.seiden_evo_last_event_time", last_event.get("time", "unknown"), {**common, "friendly_name": "Horário do último evento", "device_class": "timestamp", "icon": "mdi:clock-outline"}),
    ]

    for entity_id, entity_state, attributes in entities:
        set_ha_state(
            supervisor_token=supervisor_token,
            entity_id=entity_id,
            state=entity_state,
            attributes=attributes,
            request_timeout=request_timeout,
        )

    for reader in readers:
        publish_reader_entity(
            supervisor_token=supervisor_token,
            reader=reader,
            runtime=reader_runtime[reader["ip"]],
            request_timeout=request_timeout,
        )


def record_key(record: dict[str, Any]) -> str:
    """
    Cria uma chave lógica para deduplicação.

    photourl não participa porque a foto pode ser associada depois.
    """
    return "|".join(
        [
            str(record.get("time")),
            str(record.get("enrollid")),
            str(record.get("event")),
            str(record.get("mode")),
            str(record.get("inout")),
        ]
    )


def build_photo_url(
    reader: dict[str, Any],
    record: dict[str, Any],
) -> str | None:
    """Monta a URL completa da foto."""
    photo_path = record.get("photourl")

    if not photo_path:
        return None

    return f"http://{reader['ip']}{photo_path}"


def build_photo_filename(record: dict[str, Any]) -> str | None:
    """Extrai o nome do arquivo de foto informado pelo leitor."""
    photo_path = record.get("photourl")

    if not photo_path:
        return None

    filename = Path(str(photo_path)).name
    return filename or None


def action_label(action: str | None) -> str:
    """Traduz o movimento técnico para exibição no Home Assistant."""
    labels = {
        "entered": "Entrada",
        "exited": "Saída",
        "none": "Nenhum evento",
    }
    return labels.get(str(action), str(action or "Nenhum evento"))


def handle_authorized_record(
    reader: dict[str, Any],
    record: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    """Atualiza o Occupancy Engine após uma autenticação."""
    reset_daily_state_if_needed(state)

    user_id = str(record.get("enrollid"))
    user_name = record.get("name") or user_id
    direction = reader["direction"]
    event_time = record.get("time") or now_iso()
    photo_url = build_photo_url(reader, record)
    photo_filename = build_photo_filename(record)

    people_before = len(state["people_inside"])

    is_first_entry = False
    is_last_exit = False

    was_already_inside = (
        user_id in state["people_inside"]
    )

    if direction == "in":
        if not was_already_inside:
            if people_before == 0:
                is_first_entry = True

                state["first_entry_today"] = {
                    "user_id": user_id,
                    "user_name": user_name,
                    "time": event_time,
                }

            state["people_inside"][user_id] = {
                "user_id": user_id,
                "user_name": user_name,
                "entered_at": event_time,
                "reader_name": reader["name"],
                "reader_ip": reader["ip"],
            }

        action = "entered"
        state["entries_today"] = int(state.get("entries_today", 0)) + 1

    elif direction == "out":
        if was_already_inside:
            del state["people_inside"][user_id]

            if len(state["people_inside"]) == 0:
                is_last_exit = True

                state["last_exit_today"] = {
                    "user_id": user_id,
                    "user_name": user_name,
                    "time": event_time,
                }

        action = "exited"
        state["exits_today"] = int(state.get("exits_today", 0)) + 1

    else:
        raise RuntimeError(
            f"Direção interna inválida: {direction}"
        )

    people_inside = list(
        state["people_inside"].values()
    )

    state["events_today"] = int(state.get("events_today", 0)) + 1

    payload = {
        "provider": "evo",
        "reader_name": reader["name"],
        "reader_ip": reader["ip"],
        "direction": direction,
        "action": action,
        "user_id": user_id,
        "user_name": user_name,
        "authorized": True,
        "event_code": record.get("event"),
        "mode": record.get("mode"),
        "inout": record.get("inout"),
        "time": event_time,
        "photo_url": photo_url,
        "photo_filename": photo_filename,
        "was_already_inside": was_already_inside,
        "exit_without_entry": (
            direction == "out"
            and not was_already_inside
        ),
        "is_first_entry": is_first_entry,
        "is_last_exit": is_last_exit,
        "people_inside_count": len(people_inside),
        "building_occupied": len(people_inside) > 0,
        "people_inside": people_inside,
        "first_entry_today": state.get(
            "first_entry_today"
        ),
        "last_exit_today": state.get(
            "last_exit_today"
        ),
        "raw": record,
    }

    state["last_event"] = {
        "user_id": user_id,
        "user_name": user_name,
        "reader_name": reader["name"],
        "reader_ip": reader["ip"],
        "direction": direction,
        "action": action,
        "time": event_time,
        "photo_url": photo_url,
        "photo_filename": photo_filename,
    }

    save_state(state)

    return payload


def create_reader_runtime_state() -> dict[str, Any]:
    """Cria o estado de disponibilidade de um leitor."""
    return {
        "failures": 0,
        "next_check": 0.0,
        "offline": False,
        "status": "unknown",
        "offline_since_iso": None,
        "offline_since_monotonic": None,
        "last_error": None,
        "last_success_iso": None,
        "last_event": None,
    }


def calculate_backoff(
    poll_interval: int,
    failure_count: int,
    max_retry_interval: int,
) -> int:
    """Calcula o intervalo exponencial de nova tentativa."""
    exponential_interval = poll_interval * (
        2 ** max(failure_count - 1, 0)
    )

    return min(
        exponential_interval,
        max_retry_interval,
    )


def summarize_request_error(error: Exception) -> str:
    """Gera uma descrição operacional curta do erro."""
    error_text = str(error).lower()

    if "host is unreachable" in error_text:
        return "Host inacessível"

    if "connection refused" in error_text:
        return "Conexão recusada"

    if "timed out" in error_text:
        return "Tempo de conexão esgotado"

    if "name or service not known" in error_text:
        return "Nome ou endereço não encontrado"

    if "no route to host" in error_text:
        return "Sem rota para o equipamento"

    if isinstance(error, requests.HTTPError):
        response = error.response

        if response is not None:
            return f"Erro HTTP {response.status_code}"

        return "Erro HTTP"

    if isinstance(error, requests.Timeout):
        return "Tempo de conexão esgotado"

    if isinstance(error, requests.ConnectionError):
        return "Falha de conexão"

    return type(error).__name__


def mark_reader_offline(
    reader: dict[str, Any],
    runtime: dict[str, Any],
    error: Exception,
    poll_interval: int,
    max_retry_interval: int,
    supervisor_token: str,
    offline_event: str,
    request_timeout: int,
) -> None:
    """Marca um leitor como indisponível."""
    runtime["failures"] += 1

    retry_interval = calculate_backoff(
        poll_interval=poll_interval,
        failure_count=runtime["failures"],
        max_retry_interval=max_retry_interval,
    )

    runtime["next_check"] = (
        time.monotonic() + retry_interval
    )

    runtime["last_error"] = str(error)

    reader_name = reader["name"]
    reader_ip = reader["ip"]

    short_error = summarize_request_error(error)

    if not runtime["offline"]:
        runtime["offline"] = True
        runtime["status"] = "offline"
        runtime["offline_since_iso"] = now_iso()
        runtime["offline_since_monotonic"] = (
            time.monotonic()
        )

        LOGGER.warning(
            "[EVO][%s] Leitor offline: %s.",
            reader_name,
            short_error,
        )

        LOGGER.debug(
            "[EVO][%s] Exceção completa: %r",
            reader_name,
            error,
        )

        offline_payload = {
            "provider": "evo",
            "reader_name": reader_name,
            "reader_ip": reader_ip,
            "direction": reader["direction"],
            "status": "offline",
            "offline_since": runtime["offline_since_iso"],
            "failure_count": runtime["failures"],
            "retry_in_seconds": retry_interval,
            "error": short_error,
            "error_detail": str(error),
        }

        safe_fire_ha_event(
            supervisor_token=supervisor_token,
            event_type=offline_event,
            payload=offline_payload,
            request_timeout=request_timeout,
        )

    publish_reader_entity(
        supervisor_token=supervisor_token,
        reader=reader,
        runtime=runtime,
        request_timeout=request_timeout,
    )

    LOGGER.warning(
        "[EVO][%s] Tentativa %d falhou. "
        "Nova tentativa em %ss.",
        reader_name,
        runtime["failures"],
        retry_interval,
    )


def mark_reader_online(
    reader: dict[str, Any],
    runtime: dict[str, Any],
    supervisor_token: str,
    online_event: str,
    request_timeout: int,
) -> None:
    """Restaura o leitor ao estado online."""
    reader_name = reader["name"]
    reader_ip = reader["ip"]
    previous_status = runtime.get("status", "unknown")

    if runtime["offline"]:
        offline_duration = 0

        if runtime["offline_since_monotonic"] is not None:
            offline_duration = int(
                time.monotonic()
                - runtime["offline_since_monotonic"]
            )

        LOGGER.info(
            "[EVO][%s] Leitor online novamente após %ss.",
            reader_name,
            offline_duration,
        )

        online_payload = {
            "provider": "evo",
            "reader_name": reader_name,
            "reader_ip": reader_ip,
            "direction": reader["direction"],
            "status": "online",
            "online_at": now_iso(),
            "offline_since": runtime["offline_since_iso"],
            "offline_duration_seconds": offline_duration,
            "previous_failure_count": runtime["failures"],
        }

        safe_fire_ha_event(
            supervisor_token=supervisor_token,
            event_type=online_event,
            payload=online_payload,
            request_timeout=request_timeout,
        )

    runtime["failures"] = 0
    runtime["next_check"] = 0.0
    runtime["offline"] = False
    runtime["status"] = "online"
    runtime["last_success_iso"] = now_iso()
    runtime["offline_since_iso"] = None
    runtime["offline_since_monotonic"] = None
    runtime["last_error"] = None

    if previous_status != "online":
        publish_reader_entity(
            supervisor_token=supervisor_token,
            reader=reader,
            runtime=runtime,
            request_timeout=request_timeout,
        )


def validate_global_config(
    poll_interval: int,
    request_timeout: int,
    max_retry_interval: int,
) -> None:
    """Valida os parâmetros globais."""
    if poll_interval < 1:
        raise RuntimeError(
            "poll_interval deve ser igual ou maior que 1"
        )

    if request_timeout < 1:
        raise RuntimeError(
            "request_timeout deve ser igual ou maior que 1"
        )

    if max_retry_interval < poll_interval:
        raise RuntimeError(
            "max_retry_interval não pode ser menor "
            "que poll_interval"
        )


def validate_reader_structure(
    readers: list[dict[str, Any]],
) -> None:
    """
    Valida a estrutura de todos os leitores.

    Duplicidades não são tratadas nesta etapa.
    """
    for reader in readers:
        for required_field in (
            "name",
            "ip",
            "password",
            "direction",
        ):
            value = reader.get(required_field)

            if value is None or str(value).strip() == "":
                raise RuntimeError(
                    f"Campo obrigatório vazio: {required_field}"
                )

        if reader["direction"] not in ("in", "out"):
            raise RuntimeError(
                f"Direção inválida no leitor "
                f"{reader['name']}: {reader['direction']}"
            )

        if not isinstance(
            reader.get("enabled", True),
            bool,
        ):
            raise RuntimeError(
                f"Valor enabled inválido no leitor "
                f"{reader['name']}"
            )


def find_duplicate_values(
    readers: list[dict[str, Any]],
    field: str,
    normalize_lower: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    """Retorna os valores duplicados de determinado campo."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for reader in readers:
        value = str(reader[field]).strip()

        if normalize_lower:
            value = value.lower()

        grouped[value].append(reader)

    return {
        value: matches
        for value, matches in grouped.items()
        if len(matches) > 1
    }


def validate_active_reader_duplicates(
    active_readers: list[dict[str, Any]],
) -> None:
    """
    Impede duplicidades operacionais entre leitores ativos.
    """
    duplicate_ips = find_duplicate_values(
        readers=active_readers,
        field="ip",
    )

    if duplicate_ips:
        duplicate_ip = next(iter(duplicate_ips))

        names = ", ".join(
            reader["name"]
            for reader in duplicate_ips[duplicate_ip]
        )

        raise RuntimeError(
            f"IP duplicado entre leitores ativos: "
            f"{duplicate_ip} ({names})"
        )

    duplicate_names = find_duplicate_values(
        readers=active_readers,
        field="name",
        normalize_lower=True,
    )

    if duplicate_names:
        duplicate_name = next(iter(duplicate_names))

        raise RuntimeError(
            f"Nome duplicado entre leitores ativos: "
            f"{duplicate_name}"
        )


def log_disabled_reader_duplicates(
    active_readers: list[dict[str, Any]],
    disabled_readers: list[dict[str, Any]],
) -> None:
    """
    Registra duplicidades envolvendo leitores desativados.

    Essas situações não impedem a inicialização.
    """
    active_ips = {
        str(reader["ip"]).strip()
        for reader in active_readers
    }

    active_names = {
        str(reader["name"]).strip().lower()
        for reader in active_readers
    }

    warned_ips: set[str] = set()
    warned_names: set[str] = set()

    for reader in disabled_readers:
        reader_ip = str(reader["ip"]).strip()
        reader_name = str(reader["name"]).strip()
        normalized_name = reader_name.lower()

        if (
            reader_ip in active_ips
            and reader_ip not in warned_ips
        ):
            LOGGER.warning(
                "[CONFIG] O IP %s é utilizado por um leitor ativo "
                "e também por um leitor desativado. "
                "Isso é permitido enquanto o segundo permanecer "
                "desativado.",
                reader_ip,
            )
            warned_ips.add(reader_ip)

        if (
            normalized_name in active_names
            and normalized_name not in warned_names
        ):
            LOGGER.warning(
                "[CONFIG] O nome '%s' é utilizado por um leitor ativo "
                "e também por um leitor desativado. "
                "Isso é permitido enquanto o segundo permanecer "
                "desativado.",
                reader_name,
            )
            warned_names.add(normalized_name)

    duplicate_disabled_ips = find_duplicate_values(
        readers=disabled_readers,
        field="ip",
    )

    for duplicate_ip, readers in duplicate_disabled_ips.items():
        names = ", ".join(
            reader["name"]
            for reader in readers
        )

        LOGGER.info(
            "[CONFIG] IP repetido apenas entre leitores desativados: "
            "%s (%s). Nenhum conflito operacional.",
            duplicate_ip,
            names,
        )

    duplicate_disabled_names = find_duplicate_values(
        readers=disabled_readers,
        field="name",
        normalize_lower=True,
    )

    for _, readers in duplicate_disabled_names.items():
        names = ", ".join(
            reader["name"]
            for reader in readers
        )

        LOGGER.info(
            "[CONFIG] Nome repetido apenas entre leitores "
            "desativados: %s. Nenhum conflito operacional.",
            names,
        )


def log_reader_summary(
    active_readers: list[dict[str, Any]],
    disabled_readers: list[dict[str, Any]],
    state: dict[str, Any],
    presence_event: str,
    reader_offline_event: str,
    reader_online_event: str,
    poll_interval: int,
    request_timeout: int,
    max_retry_interval: int,
) -> None:
    """Registra o resumo operacional da inicialização."""
    active_entry_count = sum(
        1
        for reader in active_readers
        if reader["direction"] == "in"
    )

    active_exit_count = sum(
        1
        for reader in active_readers
        if reader["direction"] == "out"
    )

    LOGGER.info(
        "Leitores ativos: %d",
        len(active_readers),
    )

    LOGGER.info(
        "Leitores desativados: %d",
        len(disabled_readers),
    )

    LOGGER.info(
        "Leitores ativos de entrada: %d",
        active_entry_count,
    )

    LOGGER.info(
        "Leitores ativos de saída: %d",
        active_exit_count,
    )

    LOGGER.info(
        "Evento de presença: %s",
        presence_event,
    )

    LOGGER.info(
        "Evento de leitor offline: %s",
        reader_offline_event,
    )

    LOGGER.info(
        "Evento de leitor online: %s",
        reader_online_event,
    )

    LOGGER.info(
        "Polling normal: %ss",
        poll_interval,
    )

    LOGGER.info(
        "Timeout HTTP: %ss",
        request_timeout,
    )

    LOGGER.info(
        "Backoff máximo: %ss",
        max_retry_interval,
    )

    LOGGER.info(
        "Pessoas dentro restauradas: %d",
        len(state["people_inside"]),
    )

    for reader in disabled_readers:
        LOGGER.info(
            "[EVO][%s] %s | direção=%s | "
            "desativado pela configuração",
            reader["name"],
            reader["ip"],
            reader["direction"],
        )

    for reader in active_readers:
        LOGGER.info(
            "[EVO][%s] %s | direção=%s | ativo",
            reader["name"],
            reader["ip"],
            reader["direction"],
        )


def wait_without_active_readers(
    state: dict[str, Any],
    supervisor_token: str,
    request_timeout: int,
) -> None:
    """Mantém o Bridge ativo quando todos estão desativados."""
    LOGGER.warning(
        "Nenhum leitor EVO está ativo. "
        "O Bridge permanecerá em espera."
    )
    started_monotonic = time.monotonic()

    while True:
        publish_operational_entities(
            supervisor_token=supervisor_token,
            readers=[],
            reader_runtime={},
            state=state,
            started_monotonic=started_monotonic,
            request_timeout=request_timeout,
        )
        time.sleep(IDLE_SLEEP_SECONDS)


def run_polling_loop(
    readers: list[dict[str, Any]],
    state: dict[str, Any],
    supervisor_token: str,
    presence_event: str,
    reader_offline_event: str,
    reader_online_event: str,
    poll_interval: int,
    request_timeout: int,
    max_retry_interval: int,
) -> None:
    """Executa o loop principal de monitoramento."""
    last_seen: dict[str, str] = {}

    reader_runtime = {
        reader["ip"]: create_reader_runtime_state()
        for reader in readers
    }
    started_monotonic = time.monotonic()
    last_dashboard_publish = 0.0

    publish_operational_entities(
        supervisor_token=supervisor_token,
        readers=readers,
        reader_runtime=reader_runtime,
        state=state,
        started_monotonic=started_monotonic,
        request_timeout=request_timeout,
    )

    while True:
        loop_started_at = time.monotonic()

        for reader in readers:
            reader_name = reader["name"]
            reader_ip = reader["ip"]
            runtime = reader_runtime[reader_ip]

            if time.monotonic() < runtime["next_check"]:
                continue

            try:
                data = evo_cmd(
                    reader=reader,
                    command="getlog",
                    request_timeout=request_timeout,
                )

                if not data.get("result"):
                    raise RuntimeError(
                        f"getlog retornou falha: {data}"
                    )

                mark_reader_online(
                    reader=reader,
                    runtime=runtime,
                    supervisor_token=supervisor_token,
                    online_event=reader_online_event,
                    request_timeout=request_timeout,
                )

                records = data.get("record", [])

                if not records:
                    LOGGER.debug(
                        "[EVO][%s] Nenhum registro retornado.",
                        reader_name,
                    )
                    continue

                latest = records[0]
                latest_key = record_key(latest)

                if reader_ip not in last_seen:
                    last_seen[reader_ip] = latest_key

                    LOGGER.info(
                        "[EVO][%s] Último log inicial: %s",
                        reader_name,
                        latest,
                    )
                    continue

                if latest_key == last_seen[reader_ip]:
                    LOGGER.debug(
                        "[EVO][%s] Nenhum novo evento.",
                        reader_name,
                    )
                    continue

                last_seen[reader_ip] = latest_key

                LOGGER.debug(
                    "[EVO][%s] Novo log recebido: %s",
                    reader_name,
                    latest,
                )

                if latest.get("event") != 0:
                    LOGGER.warning(
                        "[EVO][%s] Evento não "
                        "autorizado/ignorado: código=%s",
                        reader_name,
                        latest.get("event"),
                    )
                    continue

                presence_payload = handle_authorized_record(
                    reader=reader,
                    record=latest,
                    state=state,
                )

                runtime["last_event"] = {
                    "user_name": presence_payload["user_name"],
                    "action": presence_payload["action"],
                    "time": presence_payload["time"],
                }

                event_sent = safe_fire_ha_event(
                    supervisor_token=supervisor_token,
                    event_type=presence_event,
                    payload=presence_payload,
                    request_timeout=request_timeout,
                )

                publish_operational_entities(
                    supervisor_token=supervisor_token,
                    readers=readers,
                    reader_runtime=reader_runtime,
                    state=state,
                    started_monotonic=started_monotonic,
                    request_timeout=request_timeout,
                )
                last_dashboard_publish = time.monotonic()

                if event_sent:
                    LOGGER.info(
                        "[EVO][%s] %s %s | "
                        "dentro=%d | first=%s | last=%s",
                        reader_name,
                        presence_payload["user_name"],
                        presence_payload["action"],
                        presence_payload[
                            "people_inside_count"
                        ],
                        presence_payload[
                            "is_first_entry"
                        ],
                        presence_payload[
                            "is_last_exit"
                        ],
                    )

            except (
                requests.RequestException,
                json.JSONDecodeError,
                ValueError,
                RuntimeError,
            ) as error:
                mark_reader_offline(
                    reader=reader,
                    runtime=runtime,
                    error=error,
                    poll_interval=poll_interval,
                    max_retry_interval=max_retry_interval,
                    supervisor_token=supervisor_token,
                    offline_event=reader_offline_event,
                    request_timeout=request_timeout,
                )

            except Exception:
                LOGGER.exception(
                    "[EVO][%s] Erro inesperado.",
                    reader_name,
                )

        if (
            time.monotonic() - last_dashboard_publish
            >= DASHBOARD_PUBLISH_INTERVAL
        ):
            publish_operational_entities(
                supervisor_token=supervisor_token,
                readers=readers,
                reader_runtime=reader_runtime,
                state=state,
                started_monotonic=started_monotonic,
                request_timeout=request_timeout,
            )
            last_dashboard_publish = time.monotonic()

        elapsed = time.monotonic() - loop_started_at

        sleep_time = max(
            0.2,
            poll_interval - elapsed,
        )

        time.sleep(sleep_time)


def main() -> None:
    """Inicializa e executa o Seiden EVO Bridge."""
    config = load_config()

    log_level = config.get(
        "log_level",
        DEFAULT_LOG_LEVEL,
    )

    setup_logging(log_level)

    LOGGER.info("Seiden EVO Bridge iniciado.")

    LOGGER.info(
        "Nível de log configurado: %s",
        str(log_level).upper(),
    )

    LOGGER.debug(
        "[CONFIG] Configuração carregada: %s",
        sanitize_config_for_log(config),
    )

    (
        all_readers,
        active_readers,
        disabled_readers,
    ) = build_readers_from_config(config)

    state = load_state()

    poll_interval = int(
        config.get(
            "poll_interval",
            DEFAULT_POLL_INTERVAL,
        )
    )

    request_timeout = int(
        config.get(
            "request_timeout",
            DEFAULT_REQUEST_TIMEOUT,
        )
    )

    max_retry_interval = int(
        config.get(
            "max_retry_interval",
            DEFAULT_MAX_RETRY_INTERVAL,
        )
    )

    presence_event = config.get(
        "ha_event",
        DEFAULT_PRESENCE_EVENT,
    )

    reader_offline_event = config.get(
        "reader_offline_event",
        DEFAULT_READER_OFFLINE_EVENT,
    )

    reader_online_event = config.get(
        "reader_online_event",
        DEFAULT_READER_ONLINE_EVENT,
    )

    validate_global_config(
        poll_interval=poll_interval,
        request_timeout=request_timeout,
        max_retry_interval=max_retry_interval,
    )

    validate_reader_structure(
        readers=all_readers,
    )

    validate_active_reader_duplicates(
        active_readers=active_readers,
    )

    log_disabled_reader_duplicates(
        active_readers=active_readers,
        disabled_readers=disabled_readers,
    )

    supervisor_token = os.environ.get(
        "SUPERVISOR_TOKEN"
    )

    if not supervisor_token:
        raise RuntimeError(
            "SUPERVISOR_TOKEN não encontrado"
        )

    log_reader_summary(
        active_readers=active_readers,
        disabled_readers=disabled_readers,
        state=state,
        presence_event=presence_event,
        reader_offline_event=reader_offline_event,
        reader_online_event=reader_online_event,
        poll_interval=poll_interval,
        request_timeout=request_timeout,
        max_retry_interval=max_retry_interval,
    )

    if not active_readers:
        wait_without_active_readers(
            state=state,
            supervisor_token=supervisor_token,
            request_timeout=request_timeout,
        )
        return

    run_polling_loop(
        readers=active_readers,
        state=state,
        supervisor_token=supervisor_token,
        presence_event=presence_event,
        reader_offline_event=reader_offline_event,
        reader_online_event=reader_online_event,
        poll_interval=poll_interval,
        request_timeout=request_timeout,
        max_retry_interval=max_retry_interval,
    )


if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        if LOGGER.handlers:
            LOGGER.info(
                "Seiden EVO Bridge encerrado."
            )

    except Exception:
        if not LOGGER.handlers:
            setup_logging(DEFAULT_LOG_LEVEL)

        LOGGER.exception(
            "Falha crítica ao iniciar ou executar "
            "o Seiden EVO Bridge."
        )

        sys.exit(1)
