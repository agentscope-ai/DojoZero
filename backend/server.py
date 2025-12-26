import json
import os
from pathlib import Path
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# Use the actual agentx-store path
DATA_DIR = Path(__file__).parent.parent / "agentx-store" / "trials"


@app.route("/api/trials", methods=["GET"])
def list_trials():
    """List all available betting trials/rooms."""
    trials = []
    if DATA_DIR.exists():
        for trial_dir in DATA_DIR.iterdir():
            if trial_dir.is_dir():
                spec_file = trial_dir / "spec.json"
                status_file = trial_dir / "status.json"
                trial_info = {
                    "id": trial_dir.name,
                    "name": trial_dir.name,
                }
                if spec_file.exists():
                    with open(spec_file) as f:
                        spec = json.load(f)
                        trial_info["metadata"] = spec.get("metadata", {})
                        trial_info["agents"] = [
                            {"actor_id": a.get("actor_id"), "config": a.get("config", {})}
                            for a in spec.get("agents", [])
                        ]
                if status_file.exists():
                    with open(status_file) as f:
                        status = json.load(f)
                        trial_info["phase"] = status.get("phase", "unknown")
                        trial_info["status_metadata"] = status.get("metadata", {})
                trials.append(trial_info)
    return jsonify(trials)


@app.route("/api/trials/<trial_id>", methods=["GET"])
def get_trial(trial_id):
    """Get details for a specific trial."""
    trial_dir = DATA_DIR / trial_id
    if not trial_dir.exists():
        return jsonify({"error": "Trial not found"}), 404

    result = {"id": trial_id}

    spec_file = trial_dir / "spec.json"
    if spec_file.exists():
        with open(spec_file) as f:
            result["spec"] = json.load(f)

    status_file = trial_dir / "status.json"
    if status_file.exists():
        with open(status_file) as f:
            result["status"] = json.load(f)

    return jsonify(result)


@app.route("/api/trials/<trial_id>/events", methods=["GET"])
def get_trial_events(trial_id):
    """Get betting events for a trial (from JSONL file)."""
    trial_dir = DATA_DIR / trial_id
    if not trial_dir.exists():
        return jsonify({"error": "Trial not found"}), 404

    # Look for JSONL files in checkpoints
    checkpoints_dir = trial_dir / "checkpoints"
    events = []

    if checkpoints_dir.exists():
        for f in checkpoints_dir.iterdir():
            if f.suffix == ".jsonl":
                with open(f) as file:
                    for line in file:
                        try:
                            events.append(json.loads(line.strip()))
                        except json.JSONDecodeError:
                            continue

    # Optional: limit number of events
    limit = request.args.get("limit", type=int)
    offset = request.args.get("offset", 0, type=int)

    if limit:
        events = events[offset : offset + limit]
    else:
        events = events[offset:]

    return jsonify(events)


@app.route("/api/trials/<trial_id>/checkpoint", methods=["GET"])
def get_trial_checkpoint(trial_id):
    """Get the checkpoint (agent states) for a trial."""
    trial_dir = DATA_DIR / trial_id
    if not trial_dir.exists():
        return jsonify({"error": "Trial not found"}), 404

    checkpoints_dir = trial_dir / "checkpoints"
    if not checkpoints_dir.exists():
        return jsonify({"error": "No checkpoints found"}), 404

    # Find JSON checkpoint file (not JSONL)
    for f in checkpoints_dir.iterdir():
        if f.suffix == ".json":
            with open(f) as file:
                data = json.load(file)
                # Extract summary info to avoid sending huge file
                summary = {
                    "actor_states": {},
                    "operator_states": data.get("operator_states", {}),
                }
                for actor_id, state in data.get("actor_states", {}).items():
                    summary["actor_states"][actor_id] = {
                        "events": state.get("events", 0),
                        "model_name": state.get("model_name", "unknown"),
                    }
                return jsonify(summary)

    return jsonify({"error": "No checkpoint found"}), 404


@app.route("/api/trials/<trial_id>/agent-logs", methods=["GET"])
def get_agent_logs(trial_id):
    """Get agent action logs from checkpoint."""
    trial_dir = DATA_DIR / trial_id
    if not trial_dir.exists():
        return jsonify({"error": "Trial not found"}), 404

    checkpoints_dir = trial_dir / "checkpoints"
    if not checkpoints_dir.exists():
        return jsonify({"error": "No checkpoints found"}), 404

    limit = request.args.get("limit", 50, type=int)

    for f in checkpoints_dir.iterdir():
        if f.suffix == ".json":
            with open(f) as file:
                data = json.load(file)
                logs = []
                for actor_id, actor_state in data.get("actor_states", {}).items():
                    agent_logs = {
                        "actor_id": actor_id,
                        "model_name": actor_state.get("model_name", "unknown"),
                        "events": actor_state.get("events", 0),
                        "messages": [],
                    }
                    # Extract messages from state
                    # state can be a list of dicts or a dict
                    state_data = actor_state.get("state", [])
                    if isinstance(state_data, list):
                        # state is a list of dicts, each dict has stream_id -> messages
                        for state_item in state_data:
                            if isinstance(state_item, dict):
                                for stream_id, messages in state_item.items():
                                    if isinstance(messages, list):
                                        for msg in messages[:limit]:
                                            if isinstance(msg, dict):
                                                agent_logs["messages"].append(
                                                    {
                                                        "stream": stream_id,
                                                        "role": msg.get("role"),
                                                        "name": msg.get("name"),
                                                        "content": msg.get("content"),
                                                        "timestamp": msg.get("timestamp"),
                                                    }
                                                )
                    elif isinstance(state_data, dict):
                        # state is a dict with stream_id -> messages
                        for stream_id, messages in state_data.items():
                            if isinstance(messages, list):
                                for msg in messages[:limit]:
                                    if isinstance(msg, dict):
                                        agent_logs["messages"].append(
                                            {
                                                "stream": stream_id,
                                                "role": msg.get("role"),
                                                "name": msg.get("name"),
                                                "content": msg.get("content"),
                                                "timestamp": msg.get("timestamp"),
                                            }
                                        )
                    logs.append(agent_logs)
                return jsonify(logs)

    return jsonify({"error": "No checkpoint found"}), 404


@app.route("/api/danmaku", methods=["POST"])
def send_danmaku():
    """Receive danmaku messages from frontend."""
    data = request.get_json()
    message = data.get("message", "")
    trial_id = data.get("trial_id", "")
    timestamp = data.get("timestamp", "")

    # In production, you might save this to a database or broadcast via WebSocket
    print(f"Danmaku received: [{trial_id}] {message} at {timestamp}")

    return jsonify({"status": "ok", "message": message})


if __name__ == "__main__":
    app.run(debug=True, port=5001)
