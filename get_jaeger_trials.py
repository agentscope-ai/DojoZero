import argparse
import asyncio
import json
import aiohttp
from pathlib import Path
from datetime import datetime, timezone, timedelta

# ─────────────────────────────────────────
# 配置（也可用命令行覆盖）
# ─────────────────────────────────────────
JAEGER_URL = "http://localhost:16686"
OUTPUT_DIR = Path("outputs/jaeger")
LOOKBACK_HOURS = 24 * 1        # 最近1天，对齐你的 SLS 脚本
LIMIT_PER_SERVICE = 20000    # 每个服务最多拉多少条 trace（无 trial 过滤时）
LIMIT_TRIAL_TRACES = 1_000_000  # 按 trial 拉时 Jaeger 的 trace 条数上限
MAX_CONCURRENT = 5              # 并发数
FILE_NAME = "dojozero-spans-401810753.jsonl"


def _default_start() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)


# ─────────────────────────────────────────
# Jaeger API Client（对应 SLS 的 reader）
# ─────────────────────────────────────────
class JaegerReader:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._session: aiohttp.ClientSession = None

    async def _session_get(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _get(self, path: str, params: dict = None) -> dict:
        session = await self._session_get()
        url = f"{self.base_url}{path}"
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def list_services(self) -> list[str]:
        """列出所有服务（对应 SLS 的 list_trials）"""
        data = await self._get("/api/services")
        services = data.get("data", [])
        # 过滤掉 jaeger 自身的服务
        return [s for s in services if "jaeger" not in s.lower()]

    async def get_traces(
        self,
        service: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """获取某服务在时间窗内的 traces（对应 SLS 的 get_spans）"""
        start_dt = start or _default_start()
        end_dt = end or datetime.now(timezone.utc)
        start_us = int(start_dt.timestamp() * 1_000_000)
        end_us = int(end_dt.timestamp() * 1_000_000)

        data = await self._get(
            "/api/traces",
            params={
                "service": service,
                "start": start_us,
                "end": end_us,
                "limit": limit if limit is not None else LIMIT_PER_SERVICE,
            },
        )
        return data.get("data") or []

    async def get_traces_for_trial(
        self,
        service: str,
        trial_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = LIMIT_TRIAL_TRACES,
    ) -> list[dict]:
        """按 tag dojozero.trial.id 查询，返回 Jaeger 匹配到的全部 trace（与仓库 JaegerTraceReader 一致）。"""
        start_dt = start or _default_start()
        end_dt = end or datetime.now(timezone.utc)
        start_us = int(start_dt.timestamp() * 1_000_000)
        end_us = int(end_dt.timestamp() * 1_000_000)
        tags = json.dumps({"dojozero.trial.id": trial_id})
        data = await self._get(
            "/api/traces",
            params={
                "service": service,
                "tags": tags,
                "start": start_us,
                "end": end_us,
                "limit": limit,
            },
        )
        return data.get("data") or []

    async def get_trace_by_id(self, trace_id: str) -> dict | None:
        """通过 traceID 获取完整 trace"""
        data = await self._get(f"/api/traces/{trace_id}")
        traces = data.get("data") or []
        return traces[0] if traces else None

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


# ─────────────────────────────────────────
# 阶段一：下载原始 spans（对应你的 download_all_spans）
# ─────────────────────────────────────────
def _jaeger_tag_value(tag: dict) -> str | None:
    """与 dojozero JaegerTraceReader 一致，兼容 value / vStr / OTLP struct。"""
    for k in ("vStr", "vBool", "vInt64", "vFloat64"):
        if k in tag and tag[k] is not None:
            return str(tag[k])
    v = tag.get("value")
    if v is None:
        return None
    if isinstance(v, dict):
        if "stringValue" in v:
            return str(v["stringValue"])
        if "boolValue" in v:
            return str(v["boolValue"])
        if "intValue" in v:
            return str(v["intValue"])
        if "doubleValue" in v:
            return str(v["doubleValue"])
    return str(v)


def _tag_dict(span: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for t in span.get("tags", []) or []:
        k = t.get("key")
        if not k:
            continue
        val = _jaeger_tag_value(t)
        if val is not None:
            out[k] = val
    return out


async def download_all_spans(
    reader: JaegerReader,
    service: str,
    spans_file: Path,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int | None = None,
) -> int:
    """下载 service 的所有原始 spans，保存到 spans_file。返回 span 数量。"""
    traces = await reader.get_traces(service, start=start, end=end, limit=limit)
    if not traces:
        return 0

    total_spans = 0
    with open(spans_file, "w") as f:
        for trace in traces:
            trace_id = trace.get("traceID", "")
            spans = trace.get("spans", [])
            processes = trace.get("processes", {})

            for span in spans:
                # 把 process 信息合并进 span，方便后续处理
                process_id = span.get("processID", "")
                span["_process"] = processes.get(process_id, {})
                span["_service"] = service
                span["_traceID"] = trace_id

                f.write(json.dumps(span, default=str) + "\n")
                total_spans += 1

    return total_spans


async def download_trial_spans(
    reader: JaegerReader,
    service: str,
    trial_id: str,
    spans_file: Path,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    strict_trial_tag: bool = True,
) -> int:
    """
    拉取单场 trial 下所有 span 写入 JSONL。
    Jaeger 按 trace 返回，同 trial 可能分布在多个 traceID；此处合并并可选只保留带该 trial id 的 span。
    """
    traces = await reader.get_traces_for_trial(
        service, trial_id, start=start, end=end
    )
    if not traces:
        return 0

    seen: set[tuple[str, str]] = set()
    total_spans = 0
    with open(spans_file, "w") as f:
        for trace in traces:
            trace_id = trace.get("traceID", "")
            spans = trace.get("spans", [])
            processes = trace.get("processes", {})

            for span in spans:
                if strict_trial_tag:
                    tags = _tag_dict(span)
                    if tags.get("dojozero.trial.id") != trial_id:
                        continue
                sid = span.get("spanID", "")
                key = (trace_id, sid)
                if key in seen:
                    continue
                seen.add(key)

                process_id = span.get("processID", "")
                span["_process"] = processes.get(process_id, {})
                span["_service"] = service
                span["_traceID"] = trace_id

                f.write(json.dumps(span, default=str) + "\n")
                total_spans += 1

    return total_spans


# ─────────────────────────────────────────
# 阶段二：整理 spans → 可读 events（对应你的 process_spans_to_events）
# ─────────────────────────────────────────
def process_spans_to_events(spans_file: Path, events_file: Path) -> int:
    """
    从 spans JSONL 中提取有效 events，保存为整理后的 JSONL。
    对应 SLS 的 deserialize_event_from_span 逻辑。
    """
    events = []

    with open(spans_file) as f:
        for line in f:
            if not line.strip():
                continue

            span = json.loads(line)
            event = _deserialize_event_from_span(span)
            if event is not None:
                events.append(event)

    with open(events_file, "w") as f:
        for event in events:
            f.write(json.dumps(event, default=str) + "\n")

    return len(events)


def _deserialize_event_from_span(span: dict) -> dict | None:
    """
    从单个 span 提取结构化 event。
    根据你自己的业务逻辑修改这里。
    """
    # ── 提取 tags → dict ──────────────────
    tags = {t["key"]: t["value"] for t in span.get("tags", [])}

    # ── 过滤掉不关心的 spans（按需调整）──
    # 比如只保留有 error 或特定 tag 的 span
    # if tags.get("http.status_code", 200) < 400:
    #     return None

    # ── 提取 logs/events ─────────────────
    logs = []
    for log_entry in span.get("logs", []):
        fields = {f["key"]: f["value"] for f in log_entry.get("fields", [])}
        logs.append({
            "timestamp": log_entry.get("timestamp"),
            "fields": fields,
        })

    # ── 组装 event ────────────────────────
    event = {
        "traceID":       span.get("_traceID"),
        "spanID":        span.get("spanID"),
        "service":       span.get("_service"),
        "operationName": span.get("operationName"),
        "startTime":     span.get("startTime"),          # 微秒
        "duration":      span.get("duration"),            # 微秒
        "tags":          tags,
        "logs":          logs,
        "process":       span.get("_process", {}),
        "references":    span.get("references", []),
        "warnings":      span.get("warnings"),
    }

    return event


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="从 Jaeger 拉 dojozero traces / 单场 trial 全量 spans（JSONL）。"
    )
    p.add_argument(
        "--trial-id",
        dest="trial_id",
        default=None,
        help="若指定，则只拉该 dojozero.trial.id 的 spans（推荐）。例: sys1_test_20260423_204330",
    )
    p.add_argument("--jaeger-url", default=JAEGER_URL, help="Jaeger Query 根 URL")
    p.add_argument(
        "--service",
        default="dojozero",
        help="Jaeger service 名（默认 dojozero）",
    )
    p.add_argument(
        "--lookback-hours",
        type=int,
        default=LOOKBACK_HOURS,
        help="查询起始：从现在往前多少小时（默认与脚本 LOOKBACK_HOURS 一致）",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="输出 JSONL 路径；默认 outputs/jaeger/<trial-id>-spans.jsonl 或 FILE_NAME",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="输出文件已存在时仍重新下载",
    )
    return p.parse_args()


# ─────────────────────────────────────────
# 主流程（对应你的 main）
# ─────────────────────────────────────────
async def main():
    args = _parse_args()
    lookback_start = datetime.now(timezone.utc) - timedelta(hours=args.lookback_hours)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    reader = JaegerReader(args.jaeger_url)

    if args.trial_id:
        spans_file = args.output or (OUTPUT_DIR / f"{args.trial_id}-spans.jsonl")
        if spans_file.exists() and not args.overwrite:
            print(f"[跳过] 已存在 {spans_file}（加 --overwrite 强制重拉）")
            await reader.close()
            return

        print(f"=== 按 trial 拉 spans: {args.trial_id} (service={args.service}) ===")
        try:
            n = await download_trial_spans(
                reader,
                args.service,
                args.trial_id,
                spans_file,
                start=lookback_start,
            )
            if n == 0:
                print("[空] 该时间窗内无匹配 trace；可调大 --lookback-hours 或确认 trial 已写入 Jaeger")
            else:
                print(f"已保存 {n} 个 spans → {spans_file}")
        except Exception as e:
            print(f"[失败] {e}")
        await reader.close()
        return

    # ── 无 --trial-id：保留原「按服务 bulk」行为 ───────────────
    services = await reader.list_services()
    print(f"找到 {len(services)} 个服务:")
    for s in services:
        print(f"  {s}")

    print("\n=== 阶段一：下载原始 spans（按服务，未按 trial 过滤）===")

    sem = asyncio.Semaphore(MAX_CONCURRENT)

    async def download_with_sem(service: str):
        async with sem:
            spans_file = OUTPUT_DIR / (args.output.name if args.output else FILE_NAME)

            if spans_file.exists():
                print(f"[跳过] {service} spans 已存在 {spans_file}")
                return

            print(f"[下载] {service} ...")
            try:
                count = await download_all_spans(
                    reader, service, spans_file, start=lookback_start
                )
                if count == 0:
                    print(f"  [空] 没有 spans，跳过")
                else:
                    print(f"  已保存 {count} 个 spans → {spans_file}")
            except Exception as e:
                print(f"  [失败] {service}: {e}")

    await asyncio.gather(*[download_with_sem(s) for s in services])
    await reader.close()

    # # ── 第三步（阶段二）：整理成 events JSONL ────────────────
    # print("\n=== 阶段二：整理成 events JSONL ===")

    # for service in services:
    #     spans_file  = OUTPUT_DIR / f"{service}-spans.jsonl"
    #     events_file = OUTPUT_DIR / f"{service}-events.jsonl"

    #     if not spans_file.exists():
    #         print(f"[跳过] {service} spans 文件不存在")
    #         continue

    #     if events_file.exists():
    #         print(f"[跳过] {service} events 已存在 {events_file}")
    #         continue

    #     print(f"[处理] {service} ...")
    #     count = process_spans_to_events(spans_file, events_file)
    #     print(f"  提取到 {count} 个 events → {events_file}")

    # print("\n完成！")


asyncio.run(main())
