"""Argument parsing and Click-style usage errors for ``olm_pipeline.py``."""

from __future__ import annotations

import argparse
import os
import re
import sys
import textwrap
from pathlib import Path
from typing import NoReturn

from .constants import (
    DEFAULT_APP,
    DEFAULT_KA_HOST,
    DEFAULT_KONFLUX_SERVER,
    DEFAULT_KONFLUX_UI,
    DEFAULT_LIST_COUNT,
    DEFAULT_NAMESPACE,
    DEFAULT_PRODUCT,
    LIST_SUPPORTED_OCP_MAX_PRS,
    PRODUCT_CHOICES,
)
from .errors import AppError

# When user passes ``--ka-host`` with no URL, read KA_HOST from the environment.
_KA_HOST_FROM_ENV = "__KA_HOST_FROM_ENV__"


class CliHelpFormatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    """Keep epilog layout; append option defaults when helpful (similar intent to Click ``show_default``)."""

    def _get_help_string(self, action: argparse.Action) -> str:
        help_txt = action.help
        if help_txt is None:
            help_txt = ""
        if "%(default)" in help_txt:
            return super()._get_help_string(action)
        optional_value = action.nargs in (None, argparse.OPTIONAL, argparse.ZERO_OR_MORE)
        if (
            action.option_strings
            and optional_value
            and not action.required
            and action.default is not argparse.SUPPRESS
        ):
            if action.default == "":
                return help_txt
            if action.default is None and action.nargs == argparse.OPTIONAL:
                return help_txt
        return super()._get_help_string(action)


def emit_click_style_error(parser: argparse.ArgumentParser | None, message: str, *, usage: bool) -> None:
    if usage and parser is not None:
        parser.print_usage(sys.stderr)
        print(file=sys.stderr)
        print(f"Try '{parser.prog} --help' for help.\n", file=sys.stderr)
    print(f"Error: {message}", file=sys.stderr)


class CliArgumentParser(argparse.ArgumentParser):
    """Emit usage + ``Try '… --help'`` + ``Error:`` on parser failures (Click-style)."""

    def error(self, message: str) -> NoReturn:
        emit_click_style_error(self, message, usage=True)
        self.exit(2)


def make_parser(description: str = "", epilog: str | None = None) -> CliArgumentParser:
    desc = textwrap.dedent(description or "").strip() or "Konflux OLM pipeline CLI."
    epi = None if epilog is None else textwrap.dedent(epilog).strip()
    prog = Path(sys.argv[0]).name if sys.argv else "olm_pipeline.py"
    parser = CliArgumentParser(
        prog=prog,
        formatter_class=CliHelpFormatter,
        description=desc,
        epilog=epi,
    )
    parser.add_argument(
        "--image",
        default="",
        metavar="REF",
        help="FBC/catalog image; empty = resolve automatically",
    )
    parser.add_argument("--app", default=DEFAULT_APP, help="Konflux application name")
    parser.add_argument("--namespace", "-n", default=DEFAULT_NAMESPACE, help="Tenant namespace")
    parser.add_argument(
        "--konflux-ui",
        metavar="URL",
        default=os.environ.get("KONFLUX_UI", DEFAULT_KONFLUX_UI),
        help="Konflux UI base (env KONFLUX_UI; else inferred on hosted clusters)",
    )
    parser.add_argument(
        "--ka-host",
        nargs="?",
        metavar="URL",
        const=_KA_HOST_FROM_ENV,
        default=os.environ.get("KA_HOST", DEFAULT_KA_HOST),
        help="KubeArchive API URL; bare flag uses env KA_HOST",
    )
    parser.add_argument(
        "--konflux-server",
        metavar="URL",
        default=os.environ.get("KONFLUX_SERVER", DEFAULT_KONFLUX_SERVER),
        help="Konflux API URL for oc login fallback (env KONFLUX_SERVER)",
    )
    parser.add_argument("--konflux-repo", metavar="URL", default="", help="ITS scripts repo URL (needs yq)")
    parser.add_argument("--konflux-branch", metavar="REF", default="", help="ITS scripts revision (needs yq)")
    parser.add_argument(
        "--channel",
        metavar="NAME",
        default="",
        help="ITS UPDATE_CHANNEL",
    )
    parser.add_argument(
        "--product",
        default=DEFAULT_PRODUCT,
        choices=PRODUCT_CHOICES,
        help="rhoai or odh (catalog / ITS wiring)",
    )
    parser.add_argument(
        "--version",
        "--rhoai-version",
        dest="version",
        metavar="VER",
        default="",
        help="RHOAI only: resolve FBC by rhoai-v* app label",
    )
    parser.add_argument(
        "--ocp-version",
        metavar="X.Y",
        default="",
        help="EaaS cluster minor (e.g. 4.19); with --list-supported-ocp, assert minor is listed",
    )
    parser.add_argument(
        "--watch",
        nargs="?",
        const="",
        default=None,
        metavar="PIPELINERUN",
        help="Watch latest owned run or PIPELINERUN (archive if pruned)",
    )
    parser.add_argument(
        "--list-pipelines",
        "--list",
        nargs="?",
        const=str(DEFAULT_LIST_COUNT),
        default=None,
        metavar="N",
        help=f"List last N PipelineRuns for --app (default N={DEFAULT_LIST_COUNT})",
    )
    parser.add_argument(
        "--list-supported-ocp",
        action="store_true",
        help=f"Print supported OCP minors from logs (≤{LIST_SUPPORTED_OCP_MAX_PRS} runs); optional --ocp-version",
    )
    return parser


def parse_cli_args(parser: CliArgumentParser, argv: list[str]) -> argparse.Namespace:
    args = parser.parse_args(argv)

    if args.version and args.product != "rhoai":
        raise AppError("--version is supported only with --product rhoai", 2)
    if args.ocp_version:
        if not re.fullmatch(r"\d+\.\d+", args.ocp_version.strip()):
            raise AppError("--ocp-version must be MAJOR.MINOR (e.g. 4.20)", 2)
        args.ocp_version = args.ocp_version.strip()
    if args.ka_host == _KA_HOST_FROM_ENV:
        args.ka_host = os.environ.get("KA_HOST", "")
        if not args.ka_host:
            raise AppError(
                "--ka-host with no URL requires KA_HOST in the environment, or pass "
                "--ka-host https://<kubearchive-host> (see README). "
                "Without KubeArchive, only PipelineRuns still on the apiserver are listed.",
                2,
            )
    if args.konflux_ui and not args.konflux_ui.startswith("https://"):
        raise AppError("--konflux-ui must use https://", 2)
    if args.ka_host and not args.ka_host.startswith("https://"):
        raise AppError("--ka-host must use https://", 2)
    if args.konflux_server and not args.konflux_server.startswith("https://"):
        raise AppError("--konflux-server must use https://", 2)

    if args.list_pipelines is not None:
        try:
            lp = int(args.list_pipelines)
            if lp <= 0:
                raise ValueError
            args.list_pipelines = lp
        except ValueError as exc:
            raise AppError(f"--list-pipelines expects a positive integer (got: {args.list_pipelines})", 2) from exc
    else:
        args.list_pipelines = 0

    list_pipelines_on = bool(args.list_pipelines)
    list_ocp_on = bool(args.list_supported_ocp)
    watch_on = args.watch is not None
    query_modes = sum([list_pipelines_on, list_ocp_on, watch_on])
    if query_modes > 1:
        raise AppError(
            "--list-pipelines / --list, --list-supported-ocp, and --watch are mutually exclusive (pick one query/watch mode).",
            2,
        )

    def _trigger_options_incompatible_with_query() -> list[str]:
        bad: list[str] = []
        if args.image:
            bad.append("--image")
        if args.version:
            bad.append("--version")
        if args.channel:
            bad.append("--channel")
        if args.konflux_repo:
            bad.append("--konflux-repo")
        if args.konflux_branch:
            bad.append("--konflux-branch")
        if args.ocp_version and not list_ocp_on:
            bad.append("--ocp-version")
        return bad

    if query_modes and (bad := _trigger_options_incompatible_with_query()):
        joined = ", ".join(bad)
        raise AppError(
            f"Trigger/install options cannot be used with --list-pipelines, --list-supported-ocp, or --watch: {joined}. "
            "Use only connection/context flags (e.g. -n, --app, --ka-host, --konflux-ui) for list/watch; "
            "with --list-supported-ocp you may add --ocp-version to verify it appears in the supported list.",
            2,
        )

    args.watch_mode = watch_on
    return args
