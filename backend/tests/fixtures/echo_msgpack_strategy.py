import os
import struct
import sys

import msgpack


assert os.environ["STRATEGY_IPC_TRANSPORT"] == "msgpack"


def write_frame(value):
    body = msgpack.packb(value, use_bin_type=True)
    sys.stdout.buffer.write(struct.pack(">I", len(body)) + body)
    sys.stdout.buffer.flush()


write_frame(
    [
        {
            "kind": "ticker_subscription",
            "id": "price",
            "ticker": "TEST",
            "scale": "1d",
            "session": "all",
            "update_scale": None,
            "partial": False,
        }
    ]
)

while True:
    header = sys.stdin.buffer.read(4)
    if not header:
        break
    size = struct.unpack(">I", header)[0]
    payload = sys.stdin.buffer.read(size)
    step = msgpack.unpackb(payload, raw=False)
    write_frame([{"kind": "time_ack", "unixtime": step["unixtime"]}])
