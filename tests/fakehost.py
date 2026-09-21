"""Stand-in for the codex, claude, and opencode CLIs used by dispatch tests."""
import json
import os
import sys
import time

host, args = sys.argv[1], sys.argv[2:]
prompt = sys.stdin.read()
mode = os.environ.get(f"FAKE_{host.upper()}", "ok")
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"host": host, "args": args, "prompt": prompt}) + "\n")
if mode == "fail":
    sys.exit(3)
if mode == "autherror":
    print(json.dumps({"type": "error", "error": {"name": "UnknownError", "data": {"message": "Token refresh failed: 401"}}}))
    sys.exit(1)
if mode == "sleep":
    time.sleep(30)
model = args[args.index("--model") + 1] if "--model" in args else "default-model"
if mode == "mismatch":
    model = "some-other-model"
report = "VERDICT: Accepted\nNo blocking findings." if "reviewer" in prompt else "status: done"
if host == "claude":
    print(json.dumps({"type": "result", "result": report, "usage": {"input_tokens": 10, "output_tokens": 5},
                      "modelUsage": {f"claude-{model}-4": {"inputTokens": 10}}}))
elif host == "codex":
    print(json.dumps({"type": "thread.started"}))
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": report}, "model": model}))
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 7, "cached_input_tokens": 2, "output_tokens": 3}}))
    with open(args[args.index("--output-last-message") + 1], "w", encoding="utf-8") as output:
        output.write(report)
else:
    for chunk in (report[:5], report[5:]):
        print(json.dumps({"type": "text", "part": {"text": chunk}}))
    print(json.dumps({"type": "step_finish", "part": {"tokens": {"input": 4, "output": 2}, "modelID": model.split("/")[-1]}}))
