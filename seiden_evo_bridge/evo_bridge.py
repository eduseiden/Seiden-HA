import json
import os
import time
from datetime import datetime, date
from pathlib import Path

import requests

CONFIG_PATH = Path("/data/options.json")
STATE_PATH = Path("/data/occupancy_state.json")


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def today_str():
    return date.today().isoformat()


def load_config():
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_state():
    if not STATE_PATH.exists():
        return {
            "date": today_str(),
            "people_inside": {},
            "first_entry_today": None,
            "last_exit_today": None,
        }

    with STATE_PATH.open("r", encoding="utf-8") as f:
        state = json.load(f)

    if state.get("date") != today_str():
        state["date"] = today_str()
        state["first_entry_today"] = None
        state["last_exit_today"] = None

    state.setdefault("people_inside", {})
    return state


def save_state(state):
    with STATE_PATH.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def evo_cmd(reader, command, **kwargs):
    payload = {
        "password": reader["password"],
        "cmd": command,
    }
    payload.update(kwargs)

    response = requests.post(
        f"http://{reader['ip']}/api",
        json=payload,
        timeout=5,
    )
    response.raise_for_status()
    return response.json()


def fire_ha_event(supervisor_token, event_type, payload):
    headers = {
        "Authorization": f"Bearer {supervisor_token}",
        "Content-Type": "application/json",
    }

    response = requests.post(
        f"http://supervisor/core/api/events/{event_type}",
        headers=headers,
        json=payload,
        timeout=5,
    )
    response.raise_for_status()


def record_key(record):
    return "|".join([
        str(record.get("time")),
        str(record.get("enrollid")),
        str(record.get("event")),
        str(record.get("mode")),
        str(record.get("inout")),
    ])


def build_photo_url(reader, record):
    if record.get("photourl"):
        return f"http://{reader['ip']}{record['photourl']}"
    return None


def handle_authorized_record(reader, record, state):
    user_id = str(record.get("enrollid"))
    user_name = record.get("name") or user_id
    direction = reader.get("direction", "in")
    event_time = record.get("time")
    photo_url = build_photo_url(reader, record)

    people_before = len(state["people_inside"])
    is_first_entry = False
    is_last_exit = False

    if direction == "in":
        user_was_inside = user_id in state["people_inside"]

    if not user_was_inside:
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
        } 
        
        action = "entered"

    elif direction == "out":
        entered_at = None
        if user_id in state["people_inside"]:
            entered_at = state["people_inside"][user_id].get("entered_at")
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

    people_inside = list(state["people_inside"].values())

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
        "is_first_entry": is_first_entry,
        "is_last_exit": is_last_exit,
        "people_inside_count": len(people_inside),
        "people_inside": people_inside,
        "first_entry_today": state.get("first_entry_today"),
        "last_exit_today": state.get("last_exit_today"),
        "raw": record,
    }

    save_state(state)
    return payload


def main():
    config = load_config()
    state = load_state()

    readers = config.get("readers", [])
    poll_interval = config.get("poll_interval", 2)
    ha_event = config.get("ha_event", "seiden_presence")

    supervisor_token = os.environ.get("SUPERVISOR_TOKEN")
    if not supervisor_token:
        raise RuntimeError("SUPERVISOR_TOKEN não encontrado")

    last_seen = {}

    print("Seiden EVO Bridge v0.2.0 iniciado", flush=True)
    print(f"Leitores configurados: {len(readers)}", flush=True)
    print(f"Evento HA: {ha_event}", flush=True)
    print(f"Pessoas dentro restauradas: {len(state['people_inside'])}", flush=True)

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
                    continue

                latest = records[0]
                latest_key = record_key(latest)

                if reader_ip not in last_seen:
                    last_seen[reader_ip] = latest_key
                    print(f"[{reader_name}] Último log inicial: {latest}", flush=True)
                    continue

                if latest_key == last_seen[reader_ip]:
                    continue

                last_seen[reader_ip] = latest_key
                print(f"[{reader_name}] Novo log: {latest}", flush=True)

                if latest.get("event") != 0:
                    print(f"[{reader_name}] Evento não autorizado/ignorado: {latest.get('event')}", flush=True)
                    continue

                payload = handle_authorized_record(reader, latest, state)

                fire_ha_event(
                    supervisor_token=supervisor_token,
                    event_type=ha_event,
                    payload=payload,
                )

                print(
                    f"[{reader_name}] {payload['user_name']} {payload['action']} | "
                    f"dentro={payload['people_inside_count']} | "
                    f"first={payload['is_first_entry']} | last={payload['is_last_exit']}",
                    flush=True,
                )

            except Exception as e:
                print(f"[{reader_name}] Erro: {e}", flush=True)

        time.sleep(poll_interval)


if __name__ == "__main__":
    main()
