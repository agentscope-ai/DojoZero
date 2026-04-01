#!/usr/bin/env python3
"""List distinct trace IDs from SLS logstore in a date range."""

import os
import sys
from datetime import datetime
from pathlib import Path


def load_env() -> None:
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value


def main() -> int:
    load_env()

    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <from> <to>")
        print(f"  e.g. {sys.argv[0]} '2026-03-17' '2026-03-21'")
        return 1

    from_dt = datetime.strptime(sys.argv[1], "%Y-%m-%d")
    to_dt = datetime.strptime(sys.argv[2], "%Y-%m-%d")
    from_ts = int(from_dt.timestamp())
    to_ts = int(to_dt.timestamp()) + 86400  # end of day

    from aliyun.log import GetLogsRequest, LogClient

    client = LogClient(
        os.environ["DOJOZERO_SLS_ENDPOINT"],
        os.environ["ALIBABA_CLOUD_ACCESS_KEY_ID"],
        os.environ["ALIBABA_CLOUD_ACCESS_KEY_SECRET"],
    )
    req = GetLogsRequest(
        project=os.environ["DOJOZERO_SLS_PROJECT"],
        logstore=os.environ["DOJOZERO_SLS_LOGSTORE"],
        fromTime=from_ts,
        toTime=to_ts,
        query="* | select distinct(_trace_id) as trace_id order by trace_id limit 1000",
    )
    resp = client.get_logs(req)
    if resp is None:
        print("No response from SLS")
        return 1
    for log in resp.get_logs():
        tid = log.get_contents().get("trace_id", "")
        if tid:
            print(tid)

    return 0


if __name__ == "__main__":
    sys.exit(main())
