import json
import logging
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests


CONFIG_PATH = Path("/data/options.json")
STATE_PATH = Path("/data/occupancy_state.json")

DEFAULT_POLL_INTERVAL = 2
DEFAULT_REQUEST_TIMEOUT = 5
DEFAULT_MAX_RETRY_INTERVAL = 300
DEFAULT_LOG_LEVEL = "INFO"

DEFAULT_PRESENCE_EVENT = "seiden_presence"
DEFAULT_READER_OFFLINE_EVENT = "seiden_reader_offline"
DEFAULT_READER_ONLINE_EVENT = "seiden_reader_online"

LOGGER = logging.getLogger("seiden_evo_bridge")


def setup_logging(log_level: str) -> None:
    """Configura o sistema de logs do Seiden EVO Bridge."""
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
            fmt=(
                "[%(asctime)s] "
                "[%(levelname)-7s] "
                "%(message)s"
            ),
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    LOGGER.handlers.clear()
    LOGGER.addHandler(handler)
    LOGGER.setLevel(numeric_level)
    LOGGER.propagate = False


def now_iso() -> str:
    """Retorna o horário local atual no formato ISO."""
    return datetime.now().isoformat(timespec="seconds")


def today_str() -> str:
    """Retorna a data local atual."""
    return date.today().isoformat()


def load_config() -> dict[str, Any]:
    """Carrega a configuração definida na interface do App."""
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def default_state() -> dict[str, Any]:
    """Cria o estado inicial do Occupancy Engine."""
    return {
        "date": today_str(),
        "people_inside": {},
        "first_entry_today": None,
        "last_exit_today": None,
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

    state.setdefault("date", today_str())
    state.setdefault("people_inside", {})
    state.setdefault("first_entry_today", None)
    state.setdefault("last_exit_today", None)

    reset_daily_state_if_needed(state)

    return state


def save_state(state: dict[str, Any]) -> None:
    """
    Salva o estado do Occupancy Engine usando escrita atômica.

    O arquivo temporário é escrito primeiro e, em seguida, substitui
    o arquivo de estado definitivo.
    """
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

    As pessoas presentes não são removidas, pois alguém pode
    permanecer no ambiente após a meia-noite.
    """
    current_date = today_str()

    if state.get("date") == current_date:
        return

    previous_date = state.get("date")

    state["date"] = current_date
    state["first_entry_today"] = None
    state["last_exit_today"] = None

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
    """
    Publica um evento no Home Assistant sem encerrar o Bridge
    em caso de falha temporária.
    """
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


def record_key(record: dict[str, Any]) -> str:
    """
    Cria a chave lógica de um evento.

    photourl não participa da chave porque o EVO pode criar o
    registro inicialmente sem foto e atualizar o mesmo registro
    posteriormente com a URL da imagem.
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
    """Monta a URL completa da foto associada ao evento."""
    photo_path = record.get("photourl")

    if not photo_path:
        return None

    return f"http://{reader['ip']}{photo_path}"


def handle_authorized_record(
    reader: dict[str, Any],
    record: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    """
    Processa uma autenticação autorizada e atualiza
    o Occupancy Engine.
    """
    reset_daily_state_if_needed(state)

    user_id = str(record.get("enrollid"))
    user_name = record.get("name") or user_id
    direction = reader.get("direction", "in")
    event_time = record.get("time") or now_iso()
    photo_url = build_photo_url(reader, record)

    people_before = len(state["people_inside"])

    is_first_entry = False
    is_last_exit = False

    was_already_inside = (
        user_id in state["people_inside"]
    )

    was_inside_before_exit = was_already_inside

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

    elif direction == "out":
        if was_inside_before_exit:
            del state["people_inside"][user_id]

            if len(state["people_inside"]) == 0:
                is_last_exit = True

                state["last_exit_today"] = {
                    "user_id": user_id,
                    "user_name": user_name,
                    "time": event_time,
                }

        action = "exited"

    else:
        action = "access"

    people_inside = list(
        state["people_inside"].values()
    )

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
        "was_already_inside": was_already_inside,
        "exit_without_entry": (
            direction == "out"
            and not was_inside_before_exit
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

    save_state(state)

    return payload


def create_reader_runtime_state() -> dict[str, Any]:
    """Cria o estado de disponibilidade de um leitor."""
    return {
        "failures": 0,
        "next_check": 0.0,
        "offline": False,
        "offline_since_iso": None,
        "offline_since_monotonic": None,
        "last_error": None,
    }


def calculate_backoff(
    poll_interval: int,
    failure_count: int,
    max_retry_interval: int,
) -> int:
    """Calcula o próximo intervalo de tentativa."""
    exponential_interval = poll_interval * (
        2 ** max(failure_count - 1, 0)
    )

    return min(
        exponential_interval,
        max_retry_interval,
    )


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
    """
    Atualiza o estado de falha do leitor e agenda
    uma nova tentativa.
    """
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

    reader_name = reader.get(
        "name",
        reader.get("ip"),
    )

    reader_ip = reader.get("ip")

    if not runtime["offline"]:
        runtime["offline"] = True
        runtime["offline_since_iso"] = now_iso()
        runtime["offline_since_monotonic"] = (
            time.monotonic()
        )

        LOGGER.warning(
            "[EVO][%s] Leitor offline: %s",
            reader_name,
            error,
        )

        offline_payload = {
            "provider": "evo",
            "reader_name": reader_name,
            "reader_ip": reader_ip,
            "status": "offline",
            "offline_since": (
                runtime["offline_since_iso"]
            ),
            "failure_count": runtime["failures"],
            "retry_in_seconds": retry_interval,
            "error": str(error),
        }

        safe_fire_ha_event(
            supervisor_token=supervisor_token,
            event_type=offline_event,
            payload=offline_payload,
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
    """Restaura o estado online após uma falha."""
    reader_name = reader.get(
        "name",
        reader.get("ip"),
    )

    reader_ip = reader.get("ip")

    if runtime["offline"]:
        offline_duration = 0

        if (
            runtime["offline_since_monotonic"]
            is not None
        ):
            offline_duration = int(
                time.monotonic()
                - runtime[
                    "offline_since_monotonic"
                ]
            )

        LOGGER.info(
            "[EVO][%s] Leitor online novamente "
            "após %ss.",
            reader_name,
            offline_duration,
        )

        online_payload = {
            "provider": "evo",
            "reader_name": reader_name,
            "reader_ip": reader_ip,
            "status": "online",
            "online_at": now_iso(),
            "offline_since": (
                runtime["offline_since_iso"]
            ),
            "offline_duration_seconds": (
                offline_duration
            ),
            "previous_failure_count": (
                runtime["failures"]
            ),
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
    runtime["offline_since_iso"] = None
    runtime["offline_since_monotonic"] = None
    runtime["last_error"] = None


def validate_config(
    readers: list[dict[str, Any]],
    poll_interval: int,
    request_timeout: int,
    max_retry_interval: int,
) -> None:
    """Valida os parâmetros essenciais do App."""
    if not readers:
        raise RuntimeError(
            "Nenhum leitor EVO foi configurado"
        )

    if poll_interval < 1:
        raise RuntimeError(
            "poll_interval deve ser igual "
            "ou maior que 1"
        )

    if request_timeout < 1:
        raise RuntimeError(
            "request_timeout deve ser igual "
            "ou maior que 1"
        )

    if max_retry_interval < poll_interval:
        raise RuntimeError(
            "max_retry_interval não pode ser menor "
            "que poll_interval"
        )

    configured_ips: set[str] = set()

    for reader in readers:
        for required_field in (
            "name",
            "ip",
            "password",
            "direction",
        ):
            if required_field not in reader:
                raise RuntimeError(
                    "Campo obrigatório ausente "
                    f"no leitor: {required_field}"
                )

        if reader["direction"] not in (
            "in",
            "out",
        ):
            raise RuntimeError(
                "Direção inválida no leitor "
                f"{reader['name']}: "
                f"{reader['direction']}"
            )

        if reader["ip"] in configured_ips:
            raise RuntimeError(
                "IP duplicado na configuração: "
                f"{reader['ip']}"
            )

        configured_ips.add(reader["ip"])


def main() -> None:
    """Inicializa e executa o Seiden EVO Bridge."""
    config = load_config()

    log_level = config.get(
        "log_level",
        DEFAULT_LOG_LEVEL,
    )

    setup_logging(log_level)

    LOGGER.info(
        "Seiden EVO Bridge iniciado."
    )

    LOGGER.info(
        "Nível de log configurado: %s",
        str(log_level).upper(),
    )

    state = load_state()

    readers = config.get("readers", [])

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

    validate_config(
        readers=readers,
        poll_interval=poll_interval,
        request_timeout=request_timeout,
        max_retry_interval=max_retry_interval,
    )

    supervisor_token = os.environ.get(
        "SUPERVISOR_TOKEN"
    )

    if not supervisor_token:
        raise RuntimeError(
            "SUPERVISOR_TOKEN não encontrado"
        )

    last_seen: dict[str, str] = {}

    reader_runtime = {
        reader["ip"]: create_reader_runtime_state()
        for reader in readers
    }

    LOGGER.info(
        "Leitores configurados: %d",
        len(readers),
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

    for reader in readers:
        LOGGER.info(
            "[EVO][%s] %s | direção=%s",
            reader["name"],
            reader["ip"],
            reader["direction"],
        )

    while True:
        loop_started_at = time.monotonic()

        for reader in readers:
            reader_name = reader.get(
                "name",
                reader.get("ip"),
            )

            reader_ip = reader["ip"]
            runtime = reader_runtime[reader_ip]

            if (
                time.monotonic()
                < runtime["next_check"]
            ):
                continue

            try:
                data = evo_cmd(
                    reader=reader,
                    command="getlog",
                    request_timeout=request_timeout,
                )

                if not data.get("result"):
                    raise RuntimeError(
                        "getlog retornou falha: "
                        f"{data}"
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
                        "[EVO][%s] Nenhum registro "
                        "retornado.",
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

                if (
                    latest_key
                    == last_seen[reader_ip]
                ):
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

                presence_payload = (
                    handle_authorized_record(
                        reader=reader,
                        record=latest,
                        state=state,
                    )
                )

                event_sent = safe_fire_ha_event(
                    supervisor_token=supervisor_token,
                    event_type=presence_event,
                    payload=presence_payload,
                    request_timeout=request_timeout,
                )

                if event_sent:
                    LOGGER.info(
                        "[EVO][%s] %s %s | "
                        "dentro=%d | first=%s | last=%s",
                        reader_name,
                        presence_payload[
                            "user_name"
                        ],
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
                    max_retry_interval=(
                        max_retry_interval
                    ),
                    supervisor_token=supervisor_token,
                    offline_event=(
                        reader_offline_event
                    ),
                    request_timeout=request_timeout,
                )

            except Exception:
                LOGGER.exception(
                    "[EVO][%s] Erro inesperado.",
                    reader_name,
                )

        elapsed = (
            time.monotonic() - loop_started_at
        )

        sleep_time = max(
            0.2,
            poll_interval - elapsed,
        )

        time.sleep(sleep_time)


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

        raise
