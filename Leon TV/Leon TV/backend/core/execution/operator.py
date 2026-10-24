"""Local operator commands. No command constructs a live runtime or calls models."""
import argparse
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

from core.creator.store import CreatorError
from core.execution.control import MAX_CONTROL, _read, load_control, read_control
from core.execution.fees import FeeInbox, FrozenFeeSource

BACKEND = Path(__file__).resolve().parents[2]
STATIC = (BACKEND / "code", BACKEND.parent / "frontend/public")


def selected(path):
    if path:
        return read_control(Path(path).expanduser().absolute(), BACKEND, static_roots=STATIC)
    return load_control(BACKEND, static_roots=STATIC)


def inbox(control):
    return FeeInbox(control.roots["fees"], FrozenFeeSource.model_validate(control.document.fee_source.model_dump()))


def install(source, target):
    original = Path(source).expanduser().absolute()
    control = read_control(original, BACKEND, static_roots=STATIC)
    destination = Path(target).expanduser().absolute()
    if destination != destination.resolve() or destination.is_symlink() or destination.exists():
        raise CreatorError("CONTROL_UNAVAILABLE", "A new private control destination is required", 409)
    if any(destination.is_relative_to(root) or root.is_relative_to(destination) for root in STATIC):
        raise CreatorError("CONTROL_UNAVAILABLE", "Private control destination required", 422)
    destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    parent = destination.parent.stat()
    if parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) & 0o077:
        raise CreatorError("CONTROL_UNAVAILABLE", "Private operator directory required", 422)
    raw = _read(original, maximum=MAX_CONTROL)
    fd, temporary = tempfile.mkstemp(prefix=".control-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        # Hard-link publish avoids replacing an existing operator configuration.
        os.link(temporary, destination)
        Path(temporary).unlink()
        read_control(destination, BACKEND, static_roots=STATIC)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {"installed": True, "mode": control.document.mode, "generation_posts": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description="LeonTV server-owned execution control")
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status")
    status.add_argument("--control")
    importing = sub.add_parser("fees-import")
    importing.add_argument("export")
    importing.add_argument("--control")
    installing = sub.add_parser("install")
    installing.add_argument("bundle")
    installing.add_argument("--to", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            result = install(args.bundle, args.to)
        else:
            control = selected(args.control)
            if control is None:
                if args.command != "status":
                    raise CreatorError("CONTROL_UNAVAILABLE", "No explicit operator scope is configured", 409)
                result = {"mode": "off", "generation_posts": 0}
            elif args.command == "status":
                result = {"mode": control.document.mode, "observer_enabled": control.document.observer_enabled,
                          "model": control.document.policy["model"], "fees": inbox(control).status(), "generation_posts": 0}
            else:
                export_path = Path(args.export).expanduser().absolute()
                if export_path.is_symlink():
                    raise CreatorError("FEE_EXPORT_REJECTED", "Regular private export file required", 422)
                raw = _read(export_path.resolve(), maximum=16 * 1048576, private=False)
                export_hash = inbox(control).import_bytes(raw)
                result = {"imported_export_hash": export_hash, "generation_posts": 0}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except CreatorError as error:
        print(json.dumps({"error": error.code, "generation_posts": 0}))
        return 1
    except Exception:
        print(json.dumps({"error": "CONTROL_UNAVAILABLE", "generation_posts": 0}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
