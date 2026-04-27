#!/usr/bin/env bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════════
# 批量 Scoring System 实验编排脚本
# 支持多场比赛 × 多套评分规则的全自动串行回测
#
# 用法：
#   ./run_scoring_experiments.sh              # 全量跑
#   START_GAME=2 ./run_scoring_experiments.sh # 从第2场开始
#   START_GAME=2 START_SYS=3 ./run_scoring_experiments.sh  # 从第2场第3套开始
# ═══════════════════════════════════════════════════════════════

# ──────────── 比赛列表 ────────────
# 格式："espn_game_id|sls_event_url|game_label"
#   espn_game_id  : ESPN 比赛 ID（注入 YAML params 及 trial_id）
#   sls_event_url : 传给 --events 的 SLS URL
#   game_label    : 用于文件命名的简短标识（字母/数字/连字符，不含空格）
GAMES=(
    # "401869368|sls://nba-game-401869368-683d29eb|okc-phx-g1"
    # 添加更多比赛，例如：
    "401869399|sls://nba-game-401869399-def5d4ce|den-min-g2"
    "401869391|sls://nba-game-401869391-12c6553d|ny-atl-g3"
    "401869414|sls://nba-game-401869414-69c583f3|det-olr-g3"

)

# ──────────── 评分规则列表 ────────────
SYSTEMS=(sys1 sys2 sys3 sys4)

# ──────────── 断点续跑控制 ────────────
# START_GAME : 从第几场开始（1-indexed），之前的场次全部跳过
# START_SYS  : 在 START_GAME 那场从第几套规则开始（1-indexed）
#              之后的场次始终从 sys1 开始
START_GAME=${START_GAME:-1}
START_SYS=${START_SYS:-1}

# ──────────── 回测参数 ────────────
SPEED=1.5
MAX_SLEEP=5
SERVICE_NAME="dojozero"
LOOKBACK_HOURS=48

# ──────────── 路径 ────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
OUTPUT_DIR="$SCRIPT_DIR/outputs/jaeger"
LOG_DIR="$SCRIPT_DIR/logs"
PARAMS_TEMPLATE_DIR="$SCRIPT_DIR/trial_params"

mkdir -p "$OUTPUT_DIR" "$LOG_DIR"

# ──────────── 工具函数 ────────────
timestamp() { date "+%Y-%m-%d %H:%M:%S"; }
log()    { echo "[$(timestamp)] $*"; }
notify() { log "📢 $*"; }

wait_for_jaeger() {
    local max_wait=30 waited=0
    log "等待 Jaeger 就绪..."
    while ! curl -sf http://localhost:16686/api/services > /dev/null 2>&1; do
        sleep 1
        waited=$((waited + 1))
        if [ $waited -ge $max_wait ]; then
            log "ERROR: Jaeger 未能在 ${max_wait}s 内就绪"
            return 1
        fi
    done
    log "Jaeger 就绪 (${waited}s)"
}

start_jaeger() {
    log "启动 Jaeger..."
    docker run -d --name jaeger \
        -p 16686:16686 \
        -p 4318:4318 \
        jaegertracing/all-in-one:latest > /dev/null 2>&1
    wait_for_jaeger
}

stop_jaeger() {
    log "停止 Jaeger..."
    docker stop jaeger > /dev/null 2>&1 || true
    docker rm   jaeger > /dev/null 2>&1 || true
}

# ──────────── 生成临时 params 文件 ────────────
# 将模板 YAML 中的 espn_game_id 替换为当前比赛 ID，写到临时路径
make_params_file() {
    local sys_name="$1"
    local game_id="$2"
    local template="$PARAMS_TEMPLATE_DIR/nba-moneyline-scoring-${sys_name}.yaml"
    local tmp_file="$PARAMS_TEMPLATE_DIR/.tmp_${game_id}_${sys_name}.yaml"

    if [ ! -f "$template" ]; then
        log "ERROR: 模板文件不存在: $template"
        return 1
    fi

    # 替换 espn_game_id 字段（只替换带引号的数字值，不误伤其他行）
    sed "s/espn_game_id: '[^']*'/espn_game_id: '${game_id}'/" "$template" > "$tmp_file"
    echo "$tmp_file"
}

# ──────────── 单次实验（一场比赛 × 一套规则）────────────
run_single_experiment() {
    local sys_name="$1"
    local game_id="$2"
    local event_url="$3"
    local game_label="$4"

    local trial_id="${game_label}_${sys_name}_$(date +%Y%m%d_%H%M%S)"
    local log_file="$LOG_DIR/${trial_id}.log"
    local params_file
    params_file="$(make_params_file "$sys_name" "$game_id")"

    log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    log "开始实验: 比赛=$game_label  规则=$sys_name"
    log "  Game ID  : $game_id"
    log "  Event URL: $event_url"
    log "  Trial ID : $trial_id"
    log "  日志文件 : $log_file"
    log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    # 1) 启动 Jaeger（先清理残留）
    stop_jaeger
    start_jaeger

    # 2) 运行 backtest
    notify "实验 [$game_label/$sys_name] 开始 (trial: $trial_id)"
    local start_time=$SECONDS

    set +e
    python -m dojozero.cli backtest \
        --events   "$event_url"   \
        --params   "$params_file" \
        --speed    "$SPEED"       \
        --max-sleep "$MAX_SLEEP"  \
        --emit-traces             \
        --trace-backend jaeger    \
        --trial-id "$trial_id"    \
        2>&1 | tee "$log_file"
    local backtest_exit=$?
    set -e

    local elapsed=$(( (SECONDS - start_time) / 60 ))
    log "Backtest 完成 (耗时 ${elapsed} 分钟, exit code: $backtest_exit)"

    if [ $backtest_exit -ne 0 ]; then
        notify "⚠️ [$game_label/$sys_name] backtest 异常退出 (code=$backtest_exit)，仍尝试提取 spans"
    fi

    # 3) 提取 spans
    log "提取 Jaeger spans (trial: $trial_id)..."
    sleep 5

    set +e
    python get_jaeger_trials.py \
        --trial-id       "$trial_id"       \
        --service        "$SERVICE_NAME"   \
        --lookback-hours "$LOOKBACK_HOURS" \
        --overwrite
    local extract_exit=$?
    set -e

    local spans_file="$OUTPUT_DIR/${trial_id}-spans.jsonl"
    local span_count=0
    if [ $extract_exit -eq 0 ] && [ -f "$spans_file" ]; then
        span_count=$(wc -l < "$spans_file")
        log "Spans 提取完成: $spans_file ($span_count spans)"
        notify "✅ [$game_label/$sys_name] 完成 ($span_count spans, ${elapsed}min)"
    elif [ $extract_exit -ne 0 ]; then
        log "ERROR: spans 提取失败 (exit code: $extract_exit)"
        notify "❌ [$game_label/$sys_name] spans 提取失败"
    else
        log "WARNING: spans 文件未生成"
        notify "⚠️ [$game_label/$sys_name] spans 文件未生成"
    fi

    # 4) 停止 Jaeger
    stop_jaeger

    # 5) 清理临时 params 文件
    rm -f "$params_file"

    # 6) 写入摘要（追加）
    local summary_file="$LOG_DIR/experiment_summary.txt"
    echo "${game_label}|${sys_name}|${trial_id}|${backtest_exit}|${extract_exit}|${span_count}|${elapsed}min" \
        >> "$summary_file"

    log "实验 [$game_label/$sys_name] 流程结束"
    echo ""
}

# ──────────── 主流程 ────────────
main() {
    local total_games=${#GAMES[@]}
    local total_sys=${#SYSTEMS[@]}
    local total_exps=$(( total_games * total_sys ))

    log "═══════════════════════════════════════════════════"
    log "批量实验开始"
    log "  比赛数: $total_games  ×  规则数: $total_sys  =  $total_exps 组实验"
    log "  断点: START_GAME=$START_GAME  START_SYS=$START_SYS"
    log "═══════════════════════════════════════════════════"
    echo ""

    cd "$SCRIPT_DIR"
    source "$VENV_DIR/bin/activate"
    set -a; source .env; set +a

    local game_idx=0
    local exp_count=0

    for game_entry in "${GAMES[@]}"; do
        game_idx=$((game_idx + 1))

        # 解析 game_entry
        local game_id event_url game_label
        IFS='|' read -r game_id event_url game_label <<< "$game_entry"

        # 跳过已完成的场次
        if [ $game_idx -lt $START_GAME ]; then
            log "跳过比赛 [$game_idx/$total_games] $game_label (game_idx < START_GAME)"
            continue
        fi

        log "╔══════════════════════════════════════════════════"
        log "║ 比赛 [$game_idx/$total_games]: $game_label  (game_id=$game_id)"
        log "╚══════════════════════════════════════════════════"
        echo ""

        local sys_idx=0
        for sys in "${SYSTEMS[@]}"; do
            sys_idx=$((sys_idx + 1))
            exp_count=$((exp_count + 1))

            # 在 START_GAME 这场，跳过 START_SYS 之前的规则
            # 后续场次从 sys1 开始（START_SYS 只作用于续跑的第一场）
            if [ $game_idx -eq $START_GAME ] && [ $sys_idx -lt $START_SYS ]; then
                log "跳过 $sys (sys_idx=$sys_idx < START_SYS=$START_SYS)"
                continue
            fi

            log "── [$exp_count/$total_exps] 比赛=$game_label 规则=$sys ──"
            run_single_experiment "$sys" "$game_id" "$event_url" "$game_label"

            # 同一场的规则之间稍作等待
            if [ $sys_idx -lt $total_sys ]; then
                log "等待 10s 后运行下一套规则..."
                sleep 10
            fi
        done

        echo ""
    done

    log "═══════════════════════════════════════════════════"
    log "全部实验完成！"
    log "═══════════════════════════════════════════════════"
    notify "🎉 全部 ${exp_count} 组实验已完成"

    # 打印摘要表格
    local summary_file="$LOG_DIR/experiment_summary.txt"
    if [ -f "$summary_file" ]; then
        echo ""
        log "实验摘要:"
        printf "  %-20s %-6s %-40s %-8s %-8s %-8s %s\n" \
            "比赛" "规则" "Trial ID" "BT退出码" "SP退出码" "Spans数" "耗时"
        while IFS='|' read -r glabel sys trial bt sp spans elapsed; do
            printf "  %-20s %-6s %-40s %-8s %-8s %-8s %s\n" \
                "$glabel" "$sys" "$trial" "$bt" "$sp" "$spans" "$elapsed"
        done < "$summary_file"
    fi
}

main "$@"
