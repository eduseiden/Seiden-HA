import json
import time
import requests
from pathlib import Path

CONFIG_PATH = Path("/data/options.json")
SUPERVISOR_TOKEN = None


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


def fire_ha_event(event_type, payload):
    headers = {
        "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
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


def main():
    global SUPERVISOR_TOKEN

    config = load_config()
    readers = config.get("readers", [])
    poll_interval = config.get("poll_interval", 2)
    ha_event = config.get("ha_event", "seiden_evo_access")

    SUPERVISOR_TOKEN = Path("/var/run/secrets/supervisor_token").read_text().strip()

    last_seen = {}

    print("Seiden EVO Bridge iniciado")
    print(f"Leitores configurados: {len(readers)}")
    print(f"Evento HA: {ha_event}")

    while True:
        for reader in readers:
            reader_key = reader["ip"]

            try:
                data = evo_cmd(reader, "getlog")

                if not data.get("result"):
                    print(f"[{reader['name']}] getlog falhou: {data}")
                    continue

                records = data.get("record", [])
                if not records:
                    continue

                latest = records[0]
                latest_key = f"{latest.get('time')}|{latest.get('enrollid')}|{latest.get('event')}|{latest.get('photourl')}"

                if reader_key not in last_seen:
                    last_seen[reader_key] = latest_key
                    print(f"[{reader['name']}] Último log inicial: {latest}")
                    continue

                if latest_key != last_seen[reader_key]:
                    last_seen[reader_key] = latest_key
                    payload = normalize_record(reader, latest)

                    print(f"[{reader['name']}] Novo evento: {payload}")
                    fire_ha_event(ha_event, payload)

            except Exception as e:
                print(f"[{reader.get('name', reader.get('ip'))}] Erro: {e}")

        time.sleep(poll_interval)


if __name__ == "__main__":
    main()
