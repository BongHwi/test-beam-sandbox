"""TrackSim command-line entry point: preflight, generate and verify."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tracksim", description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    preflight = subs.add_parser(
        "preflight", help="validate a configuration and print runtime identities"
    )
    preflight.add_argument("--config", type=Path, required=True)
    gen = subs.add_parser(
        "generate",
        help="run transport, response, readout and the writer into a new directory",
    )
    gen.add_argument("--config", type=Path, required=True)
    gen.add_argument("--out", type=Path, required=True)
    gen.add_argument("--events", type=int)
    gen.add_argument("--seed", type=int)
    verify = subs.add_parser("verify", help="verify a finished run directory")
    verify.add_argument("run", type=Path)
    return parser


def _preflight(args: argparse.Namespace) -> None:
    from .provenance import python_identity
    from .specs import normalize_config, validate_capabilities
    from .transport import backend_for

    spec = normalize_config(json.loads(args.config.read_text()))
    validate_capabilities(spec)
    identities = {
        "python": python_identity(),
        "backend": spec.backend,
        **backend_for(spec).preflight_identity(spec),
    }
    print(json.dumps(identities, indent=2))


def _generate(args: argparse.Namespace) -> None:
    from .lifecycle import generate

    settings = json.loads(args.config.read_text())
    if not isinstance(settings, dict):
        raise ValueError("configuration root must be an object")
    for key in ("events", "seed"):
        if getattr(args, key) is not None:
            settings[key] = getattr(args, key)
    manifest = generate(args.out, settings)
    print(
        json.dumps(
            {
                "run": str(args.out.resolve()),
                "complete": manifest["complete"],
                "acceptance_level": manifest["acceptance_level"],
            },
            indent=2,
        )
    )


def _verify(args: argparse.Namespace) -> None:
    from .lifecycle import MANIFEST_SCHEMA, verify

    out = args.run.resolve()
    manifest = json.loads((out / "manifest.json").read_text())
    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValueError("unsupported manifest schema")
    print(json.dumps(verify(out, manifest), indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    try:
        {"preflight": _preflight, "generate": _generate, "verify": _verify}[
            args.command
        ](args)
    except (ValueError, RuntimeError, OSError, ArithmeticError) as exc:
        print(f"tracksim: error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
