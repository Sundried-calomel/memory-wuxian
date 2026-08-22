import json


def event(timestamp, outer_type, payload):
    return json.dumps(
        {"timestamp": timestamp, "type": outer_type, "payload": payload},
        ensure_ascii=False,
    ) + "\n"
