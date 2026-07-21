from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
import json
import os
from pathlib import Path
import sys

from kosui_forge.application.contracts import (
    DoctorRequest,
    OperationResult,
    OperationStatus,
)
from kosui_forge.infrastructure.doctor import build_doctor_service

from . import __version__
from .config import Config, load_config, write_config
from .errors import PartialFailure, RepoToolingError, SafetyError
from .forgejo import ForgejoClient
from .github import GitHubClient
from .journal import StageJournal
from .paths import default_config_path, default_state_dir
from .preflight import run_preflight
from .redaction import redact
from .workflow import RepositoryManager


def _add_batch_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true", help="apply the displayed plan")
    parser.add_argument("--owner", help="only process one Forgejo owner")
    parser.add_argument(
        "--repo",
        dest="repos",
        action="append",
        default=[],
        help="repository name or owner/name",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="repo-bootstrap")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument("--config", type=Path, default=default_config_path())
    subparsers = parser.add_subparsers(dest="command", required=True)

    configure = subparsers.add_parser(
        "configure", help="write the non-secret global policy configuration"
    )
    configure.add_argument("--forgejo-url", required=True)
    configure.add_argument("--forgejo-owner", required=True)
    configure.add_argument("--github-owner", required=True)
    configure.add_argument("--github-host", default="github.com")
    configure.add_argument("--projects-root", required=True, type=Path)
    configure.add_argument("--ssh-alias", required=True)
    configure.add_argument("--mirror-interval", default="8h")
    configure.add_argument("--force", action="store_true")
    configure.add_argument("--yes", action="store_true")

    doctor = subparsers.add_parser(
        "doctor", help="run a comprehensive read-only prerequisite check"
    )
    doctor.add_argument("--name", help="also inspect collisions for this repository")
    doctor.add_argument(
        "--description",
        default="Preflight inspection",
        help="expected description when --name is supplied",
    )
    doctor.add_argument(
        "--no-github",
        action="store_true",
        help="skip repository-specific GitHub collision inspection",
    )

    create = subparsers.add_parser(
        "create", help="create and clone a Forgejo repository"
    )
    create.add_argument("name")
    create.add_argument("--description", required=True)
    create.add_argument(
        "--github", action="store_true", help="also configure a GitHub push mirror"
    )
    create.add_argument(
        "--public", action="store_true", help="request public repositories"
    )
    create.add_argument(
        "--ack-public",
        "--confirm-public",
        dest="ack_public",
        metavar="NAME",
        help="non-interactive acknowledgement used only with --public --yes",
    )
    create.add_argument("--yes", action="store_true", help="apply the displayed plan")
    create.add_argument("--dry-run", action="store_true")

    mirror_all = subparsers.add_parser(
        "mirror-all",
        help="discover accessible Forgejo repositories and fill missing mirrors",
    )
    _add_batch_arguments(mirror_all)
    batch = subparsers.add_parser("batch", help=argparse.SUPPRESS)
    _add_batch_arguments(batch)
    return parser


def requested_visibility(args: argparse.Namespace) -> str:
    acknowledgement = getattr(args, "ack_public", None)
    if not getattr(args, "public", False):
        if acknowledgement is not None:
            raise SafetyError("--ack-public is only valid with --public")
        return "private"
    if acknowledgement is not None and acknowledgement != args.name:
        raise SafetyError("--ack-public value does not match the repository name")
    if getattr(args, "yes", False) and acknowledgement is None:
        raise SafetyError(
            f"non-interactive public creation requires --yes --ack-public {args.name}"
        )
    return "public"


def confirm_plan(
    args: argparse.Namespace,
    plan: Sequence[str],
    *,
    input_fn: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
) -> None:
    output("Plan:")
    for index, item in enumerate(plan, start=1):
        output(f"  {index}. {item}")
    if getattr(args, "yes", False):
        if getattr(args, "public", False):
            acknowledgement = getattr(args, "ack_public", None)
            if acknowledgement != args.name:
                raise SafetyError(
                    "non-interactive public creation requires "
                    f"--yes --ack-public {args.name}"
                )
        return
    if getattr(args, "public", False):
        expected = f"PUBLIC {args.name}"
        if input_fn(f"Type {expected!r} to confirm public visibility: ") != expected:
            raise SafetyError("public creation was not confirmed")
    try:
        answer = input_fn("Apply this plan? Type 'yes' to continue: ")
    except EOFError as exc:
        raise SafetyError(
            "plan was not confirmed; use --yes for non-interactive use"
        ) from exc
    if answer != "yes":
        raise SafetyError("plan was not confirmed")


def _configure(args: argparse.Namespace) -> int:
    config = Config(
        forgejo_url=args.forgejo_url,
        forgejo_owner=args.forgejo_owner,
        github_owner=args.github_owner,
        github_host=args.github_host,
        projects_root=args.projects_root.expanduser().absolute(),
        ssh_alias=args.ssh_alias,
        owner_map={args.forgejo_owner: args.github_owner},
        mirror_interval=args.mirror_interval,
    )
    action = "replace" if args.force and args.config.exists() else "write"
    plan = [
        f"{action} non-secret policy at {args.config}",
        "enforce private-by-default visibility and per-repository deploy keys",
        "set owner-only permissions on the config file",
    ]
    confirm_plan(args, plan)
    write_config(args.config, config, force=args.force)
    print(f"configuration written: {args.config}")
    print(
        "next: load the Forgejo token from a protected source and run repo-bootstrap doctor"
    )
    return 0


def _create_plan(
    config: Config, name: str, *, private: bool, with_github: bool
) -> list[str]:
    visibility = "private" if private else "PUBLIC"
    plan = [
        f"ensure {visibility} Forgejo repository {config.forgejo_owner}/{name}",
        f"clone or verify {config.projects_root / name} via SSH alias {config.ssh_alias}",
    ]
    if with_github:
        plan.extend(
            (
                f"ensure {visibility} GitHub repository {config.github_owner}/{name}",
                "disable GitHub Actions for the mirror-only repository",
                "create/reuse a Forgejo SSH push mirror and unique writable deploy key",
                "trigger synchronization and require a present empty last_error",
            )
        )
    return plan


def _record_journal_failure(
    journal: StageJournal | None,
    detail: object,
    *,
    token: str | None = None,
) -> None:
    if journal is None:
        return
    try:
        journal.record("operation", "failed", detail=detail)
    except OSError as exc:
        print(
            f"warning: could not update resume journal: {redact(exc, (token or '',))}",
            file=sys.stderr,
        )


def _render_doctor_result(result: OperationResult) -> str:
    lines: list[str] = []
    for check in result.checks:
        status = "PASS" if check.ok else "FAIL"
        line = f"[{status}] {check.name}: {check.detail}"
        if not check.ok and check.guidance:
            line += f"; {check.guidance}"
        lines.append(line)
    return "\n".join(lines)


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    environment = os.environ if environ is None else environ
    token: str | None = None
    journal: StageJournal | None = None
    try:
        args = build_parser().parse_args(argv)
        if args.command == "configure":
            return _configure(args)

        visibility = (
            requested_visibility(args) if args.command == "create" else "private"
        )
        config = load_config(args.config)
        token = environment.get(config.forgejo_token_env)
        token_present = bool(token)
        forgejo = ForgejoClient(
            config.forgejo_url, token or "repo-bootstrap-preflight-missing-credential"
        )
        github = GitHubClient(host=config.github_host)

        if args.command == "doctor":
            service = build_doctor_service(
                environment=environment,
                config_loader=lambda _path: config,
                forgejo_factory=lambda _url, _token: forgejo,
                github_factory=lambda **_kwargs: github,
                preflight_runner=run_preflight,
            )
            doctor_result = service.run(
                DoctorRequest(
                    config_path=args.config,
                    repository_name=args.name,
                    description=args.description if args.name else None,
                    include_github=not args.no_github,
                )
            )
            print(_render_doctor_result(doctor_result))
            return 0 if doctor_result.status is OperationStatus.SUCCEEDED else 2

        if args.command == "create":
            private = visibility == "private"
            report = run_preflight(
                config,
                forgejo,
                github,
                token_present=token_present,
                name=args.name,
                description=args.description,
                private=private,
                with_github=args.github,
            )
            print(report.render())
            if not report.ok:
                raise SafetyError("preflight failed; no create operation was started")
            plan = _create_plan(
                config, args.name, private=private, with_github=args.github
            )
            if args.dry_run:
                print("Plan:")
                for index, item in enumerate(plan, start=1):
                    print(f"  {index}. [DRY-RUN] {item}")
            else:
                confirm_plan(args, plan)
                state_dir = default_state_dir(environ=environment)
                journal = StageJournal(
                    StageJournal.path_for(state_dir, config.forgejo_owner, args.name),
                    command="create",
                    forgejo_repository=f"{config.forgejo_owner}/{args.name}",
                    github_repository=(
                        f"{config.github_owner}/{args.name}" if args.github else None
                    ),
                )
                journal.start(plan)
            manager = RepositoryManager(
                config,
                forgejo,
                github=github,
                git_runner=None,
                reporter=print,
                journal=journal,
            )
            states = manager.create(
                args.name,
                args.description,
                private=private,
                with_github=args.github,
                dry_run=args.dry_run,
            )
            if journal is not None:
                journal.complete()
                print(f"resume journal: {journal.path}")
            print(json.dumps(states, sort_keys=True))
            return 0

        report = run_preflight(
            config,
            forgejo,
            github,
            token_present=token_present,
            name=None,
            description=None,
            private=True,
            with_github=True,
        )
        print(report.render())
        if not report.ok:
            raise SafetyError("preflight failed; mirror discovery was not started")
        preview_manager = RepositoryManager(
            config, forgejo, github=github, git_runner=None, reporter=print
        )
        preview = preview_manager.batch(
            dry_run=True,
            owner_filter=args.owner,
            repository_filters=set(args.repos) or None,
        )
        print(
            "mirror-all preview: "
            f"examined={preview.examined} ready={preview.ready} "
            f"would_change={preview.would_change} skipped={preview.skipped} "
            f"blockers={len(preview.blockers)}"
        )
        if args.dry_run:
            return 4 if preview.blockers else 0
        if preview.blockers:
            raise SafetyError(
                "mirror-all inspection found blockers; no writes were started"
            )
        plan = [
            f"reuse {preview.ready} already-compliant private mirrors",
            f"create or complete {preview.would_change} private GitHub mirrors",
            "use one unique Forgejo-generated writable deploy key per repository",
            "disable GitHub Actions, synchronize, and require empty last_error",
        ]
        confirm_plan(args, plan)
        state_dir = default_state_dir(environ=environment)
        journal = StageJournal(
            StageJournal.path_for(state_dir, config.forgejo_owner, "mirror-all"),
            command="mirror-all",
            forgejo_repository=f"{config.forgejo_owner}/*",
            github_repository=f"{config.github_owner}/*",
        )
        journal.start(plan)
        manager = RepositoryManager(
            config,
            forgejo,
            github=github,
            git_runner=None,
            reporter=print,
            journal=journal,
        )
        result = manager.batch(
            dry_run=False,
            owner_filter=args.owner,
            repository_filters=set(args.repos) or None,
        )
        print(
            "mirror-all summary: "
            f"examined={result.examined} ready={result.ready} "
            f"would_change={result.would_change} changed={result.changed} "
            f"skipped={result.skipped} blockers={len(result.blockers)}"
        )
        if result.blockers:
            _record_journal_failure(journal, "; ".join(result.blockers), token=token)
        else:
            journal.complete()
        print(f"resume journal: {journal.path}")
        return 4 if result.blockers else 0
    except PartialFailure as exc:
        _record_journal_failure(journal, exc, token=token)
        print(f"partial failure: {redact(exc, (token or '',))}", file=sys.stderr)
        if journal is not None:
            print(f"resume journal: {journal.path}", file=sys.stderr)
        return 3
    except (RepoToolingError, ValueError, FileExistsError, OSError) as exc:
        _record_journal_failure(journal, exc, token=token)
        print(f"error: {redact(exc, (token or '',))}", file=sys.stderr)
        if journal is not None:
            print(f"resume journal: {journal.path}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
