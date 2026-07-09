import json
import os
import time
from pathlib import Path

import requests

CONFIG_PATH = Path("/data/options.json")


def load_config():
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def evo_cmd(reader, command, **kwargs):
    payload = {
        "password": reader["password"],
        "cmd": command,
    }
    payload.update(kwargs)

    url = f"http://{reader['ip']}/api"

    response = requests.post(url, json=payload, timeout=5)
    response.raise_for_status()

    return response.json()


def fire_ha_event(supervisor_token, event_type, payload):
    headers = {
        "Authorization": f"Bearer {supervisor_token}",
        "Content-Type": "application/json",
    }

    url = f"http://supervisor/core/api/events/{event_type}"

    response = requests.post(url, headers=headers, json=payload, timeout=5)
    response.raise_for_status()


def normalize_record(reader, record):
    event_code = record.get("event")
    authorized = event_code == 0

    photo_url = None
    if record.get("photourl"):
        photo_url = f"http://{reader['ip']}{record['photourl']}"

    return {
        "provider": "evo",
        "reader_name": reader["name"],
        "reader_ip": reader["ip"],
        "user_id": record.get("enrollid"),
        "user_name": record.get("name"),
        "authorized": authorized,
        "event_code": event_code,
        "mode": record.get("mode"),
        "inout": record.get("inout"),
        "time": record.get("time"),
        "photo_url": photo_url,
        "raw": record,
    }


def make_record_key(record):
    return "|".join(
        [
            str(record.get("time")),
            str(record.get("enrollid")),
            str(record.get("event")),
            str(record.get("photourl")),
        ]
    )


def main():
    config = load_config()

    readers = config.get("readers", [])
    poll_interval = config.get("poll_interval", 2)
    ha_event = config.get("ha_event", "seiden_evo_access")

    supervisor_token = os.environ.get("SUPERVISOR_TOKEN")

    if not supervisor_token:
        raise RuntimeError("SUPERVISOR_TOKEN não encontrado")

    last_seen = {}

    print("Seiden EVO Bridge iniciado", flush=True)
    print(f"Leitores configurados: {len(readers)}", flush=True)
    print(f"Evento HA: {ha_event}", flush=True)

    while True:
        for reader in readers:
            reader_name = reader.get("name", reader.get("ip"))
            reader_ip = reader.get("ip")

            try:
                data = evo_cmd(reader, "getlog")

                if not data.get("result"):
                    print(f"[{reader_name}] getlog falhou: {data}", flush=True)
                    continue

                records = data.get("record", [])

                if not records:
                    print(f"[{reader_name}] Nenhum registro encontrado", flush=True)
                    continue

                latest = records[0]
                latest_key = make_record_key(latest)

                if reader_ip not in last_seen:
                    last_seen[reader_ip] = latest_key
                    print(f"[{reader_name}] Último log inicial: {latest}", flush=True)
                    continue

                if latest_key != last_seen[reader_ip]:
                    last_seen[reader_ip] = latest_key

                    payload = normalize_record(reader, latest)

                    print(f"[{reader_name}] Novo evento: {payload}", flush=True)

                    fire_ha_event(
                        supervisor_token=supervisor_token,
                        event_type=ha_event,
                        payload=payload,
                    )

                    print(f"[{reader_name}] Evento enviado ao HA: {ha_event}", flush=True)

            except Exception as e:
                print(f"[{reader_name}] Erro: {e}", flush=True)

        time.sleep(poll_interval)


if __name__ == "__main__":
    main()
