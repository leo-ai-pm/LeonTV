import argparse
import json

from .runtime import Harness


def main():
    parser = argparse.ArgumentParser(description="Isolated deterministic Harness PoC; no model or publishing")
    parser.add_argument("action", choices=["run", "resume", "status", "approve", "reject"])
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--fault", choices=["missing_clip", "omit_clip", "none"], default="missing_clip")
    parser.add_argument("--max-calls", type=int, default=6)
    parser.add_argument("--max-repairs", type=int, default=1)
    parser.add_argument("--tool-timeout", type=float, default=30)
    parser.add_argument("--total-seconds", type=float, default=120)
    parser.add_argument("--pause-after-tools", type=int)
    parser.add_argument("--artifact-sha", default="")
    parser.add_argument("--reviewer", default="")
    args = parser.parse_args()
    try:
        with Harness(args.run_dir, resume=args.action != "run", fault=args.fault,
                     max_calls=args.max_calls, max_repairs=args.max_repairs,
                     tool_timeout=args.tool_timeout, total_seconds=args.total_seconds) as harness:
            if args.action in {"run", "resume"}:
                state = harness.run(args.pause_after_tools)
            elif args.action in {"approve", "reject"}:
                state = harness.human_decision(args.action == "approve", args.artifact_sha, args.reviewer)
            else:
                state = harness.state
            print(json.dumps(state, ensure_ascii=False, indent=2))
            return 0 if state["status"] in {"paused", "waiting_human_review", "approved", "rejected"} else 2
    except Exception as exc:
        print(json.dumps({"status": "refused", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
