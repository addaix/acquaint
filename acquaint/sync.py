"""Private-repository sync for the data root: create the repository private, refuse anything else, guard every push.

``init`` creates the GitHub repository through ``gh`` as **private**, refuses to
continue unless ``gh repo view --json visibility`` reports ``PRIVATE``, makes the data
root its checkout, and installs a ``pre-push`` hook. The hook, and ``push`` and ``pull``
in Python, re-check that:

- the push goes through ``origin``, which has exactly one URL, the one recorded at init;
- no ``pushurl`` differs from it, and no ``url.*.pushInsteadOf`` rewrites it;
- that URL is one of the forms that name the recorded repository on github.com
  (:func:`github_urls`, the same list the hook spells out);
- ``gh`` reports the repository, on github.com whatever ``GH_HOST`` says, as private.

The hook is installed in the repository's shared hooks folder, executable, and
``core.hooksPath`` points at it by absolute path, so linked worktrees are guarded too and
a global ``core.hooksPath`` cannot silently disable it. ``init`` takes over only an empty
folder, or a clone of that same repository; it refuses any other existing repository.

What this does **not** protect, stated plainly:

- a private repository is access control, not encryption: the host can read it all;
- ``git push --no-verify`` skips the hook;
- whoever can change this repository's git config, or which ``gh`` runs on ``PATH``,
  controls what the guard sees;
- moving the data root breaks the absolute hook path until ``sync init
  --existing-only`` runs again (``sync push`` refuses in that state; a raw ``git push``
  does not);
- file names and commit messages carry people's names;
- deleting a folder does not remove it from history, other clones, or backups.

``git-remote-gcrypt``, which would encrypt contents, names and history, is not supported
yet: the guard does not recognise its internal push, so ``init`` refuses ``gcrypt::`` URLs.

Every ``git`` and ``gh`` call goes through ``run``, so tests can script ``gh`` while
``git`` runs for real.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from acquaint.edit import TOMBSTONES
from acquaint.resources import data_text
from acquaint.store import AcquaintError

__all__ = [
    "ACQUAINT",
    "BRANCH",
    "SyncNamespace",
    "GITHUB_HOST",
    "github_urls",
    "init",
    "pull",
    "push",
    "run_command",
    "sole_access",
    "status",
    "visibility",
]

BRANCH = "main"
GITHUB_HOST = "github.com"
_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+$")
_GITIGNORE = "# generated, never synced\n_index/\n.DS_Store\n"
_GITATTRIBUTES = "# one line-ending convention on every machine, so records parse the same everywhere\n* text=auto eol=lf\n"

Runner = Callable[..., Any]


@dataclass(frozen=True)
class SyncNamespace:
    """Everything that names the application using this mechanism: one value, so another app can reuse it unchanged.

    ``name`` prefixes the git config keys (``<name>.repo``, ``<name>.remote``) and the
    default commit messages; ``command`` is what error messages tell the user to run
    (``<command> init``, ``<command> push``); ``subject`` is what the guard refuses to push
    to a non-private repository ("profile data").
    """

    name: str = "acquaint"
    command: str = "acquaint sync"
    subject: str = "profile data"
    store: str = "profile"
    description: str = "acquaint profile store (private)"
    gitignore: str = _GITIGNORE

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", self.name):
            raise AcquaintError(
                f"namespace name {self.name!r} must be letters, digits and '-' (it prefixes git config keys)"
            )
        for field in ("command", "subject", "store"):
            if re.search(r"[\"`$\\\r\n]|__[A-Z]+__", getattr(self, field)):
                raise AcquaintError(
                    f"namespace {field} {getattr(self, field)!r} may not contain quotes, backticks, $, backslashes, line breaks or __TOKEN__s"
                )

    @property
    def repo_key(self) -> str:
        return f"{self.name}.repo"

    @property
    def remote_key(self) -> str:
        return f"{self.name}.remote"

    @property
    def init_message(self) -> str:
        return f"{self.name}: initialise store"

    def sync_message(self, stamp: str, what: str) -> str:
        return f"{self.name}: sync {stamp} ({what})"

    def hook_text(self) -> str:
        """The shipped pre-push guard with this namespace filled in."""
        values = {
            "NAME": self.name,
            "COMMAND": self.command,
            "SUBJECT": self.subject,
            "STORE": self.store,
        }
        return re.sub(
            r"__(NAME|COMMAND|SUBJECT|STORE)__",
            lambda m: values[m.group(1)],
            data_text("hooks/pre-push"),
        )


#: The default: acquaint's own store.
ACQUAINT = SyncNamespace()


def github_urls(repo: str) -> list[str]:
    """Every remote URL accepted for a GitHub repository. The pre-push hook spells out the same six forms.

    >>> github_urls("example/profiles")
    ['git@github.com:example/profiles.git', 'git@github.com:example/profiles', 'ssh://git@github.com/example/profiles.git', 'ssh://git@github.com/example/profiles', 'https://github.com/example/profiles.git', 'https://github.com/example/profiles']
    """
    return [
        f"git@github.com:{repo}.git",
        f"git@github.com:{repo}",
        f"ssh://git@github.com/{repo}.git",
        f"ssh://git@github.com/{repo}",
        f"https://github.com/{repo}.git",
        f"https://github.com/{repo}",
    ]


def _same_repo(url: str, repo: str) -> bool:
    return url.strip().lower() in {form.lower() for form in github_urls(repo)}


def run_command(
    args: Sequence[str], *, cwd: str | os.PathLike | None = None
) -> subprocess.CompletedProcess:
    """Run ``git`` or ``gh`` (``gh`` pinned to github.com), capturing text output."""
    args = list(args)
    env = {**os.environ, "GH_HOST": GITHUB_HOST} if args and args[0] == "gh" else None
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True, env=env)
    except FileNotFoundError as error:
        raise AcquaintError(
            f"{args[0]} is not installed or not on PATH ({error})"
        ) from error


def _ok(run: Runner, args: Sequence[str], *, cwd=None, what: str) -> str:
    result = run(args, cwd=cwd)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        raise AcquaintError(
            f"{what} failed: {' '.join(args)}: {detail[-1] if detail else 'no output'}"
        )
    return (result.stdout or "").strip()


def _lines(root: Path, run: Runner, *args: str) -> list[str]:
    """Non-empty output lines of a git command; a missing config value is simply no lines."""
    return [
        line
        for line in (run(["git", *args], cwd=root).stdout or "").splitlines()
        if line.strip()
    ]


def visibility(repo: str, *, run: Runner = run_command) -> str:
    """``PRIVATE``, ``PUBLIC`` or ``INTERNAL``, as ``gh`` reports it now for the repository on github.com."""
    return _ok(
        run,
        ["gh", "repo", "view", repo, "--json", "visibility", "-q", ".visibility"],
        what="reading visibility",
    ).upper()


def sole_access(repo: str, *, run: Runner = run_command) -> tuple[bool, str]:
    """Whether only the repository's owner can read it, and why not: private, owned by a person (not an organisation), that person its only collaborator, no invitation pending.

    This decides whether profile edits may be committed and pushed without the operator
    reviewing the diff first: nobody else will see them.
    """
    found = run(
        [
            "gh",
            "api",
            f"repos/{repo}",
            "--jq",
            "[.private, .owner.login, .owner.type] | @tsv",
        ]
    )
    if found.returncode != 0:
        return False, f"could not read {repo} through gh"
    fields = (found.stdout or "").strip().split("\t")
    if len(fields) != 3:
        return False, f"unexpected answer from gh about {repo}"
    private, owner, owner_type = fields
    if private != "true":
        return False, f"{repo} is not private"
    if owner_type != "User":
        return (
            False,
            f"{repo} belongs to the organisation {owner}, whose members may read it",
        )
    people = run(
        ["gh", "api", f"repos/{repo}/collaborators", "--paginate", "--jq", ".[].login"]
    )
    if people.returncode != 0:
        return False, f"could not list the collaborators of {repo}"
    others = sorted({p for p in (people.stdout or "").split() if p} - {owner})
    if others:
        return False, f"{repo} has other collaborators: {', '.join(others)}"
    invited = run(["gh", "api", f"repos/{repo}/invitations", "--jq", ".[].invitee.login"])
    if invited.returncode != 0 or (invited.stdout or "").strip():
        return (
            False,
            f"{repo} has a pending invitation, or its invitations could not be read",
        )
    return True, f"{repo} is private and {owner} is its only collaborator"


def _require_private(repo: str, run: Runner, ns: SyncNamespace = ACQUAINT) -> str:
    seen = visibility(repo, run=run)
    if seen != "PRIVATE":
        raise AcquaintError(
            f"{repo} is {seen or 'of unknown visibility'}, not PRIVATE: refusing to put {ns.subject} in it"
        )
    return seen


def _configured(root: Path, run: Runner, ns: SyncNamespace) -> tuple[str, str]:
    if not (root / ".git").exists():
        raise AcquaintError(
            f"{root} is not a synced store yet: run `{ns.command} init --repo <owner>/<name>` first"
        )
    repo = next(iter(_lines(root, run, "config", "--get", ns.repo_key)), "")
    remote = next(iter(_lines(root, run, "config", "--get", ns.remote_key)), "")
    if not repo or not remote:
        raise AcquaintError(
            f"{root} is a git repository but was not set up by `{ns.command} init`"
        )
    if not _same_repo(remote, repo):
        raise AcquaintError(
            f"the recorded remote {remote!r} is not a url of the GitHub repository {repo}; refusing"
        )
    urls = _lines(root, run, "config", "--get-all", "remote.origin.url")
    if urls != [remote]:
        raise AcquaintError(
            f"origin must have exactly one url, {remote!r}, but has {urls}; refusing"
        )
    pushurls = _lines(root, run, "config", "--get-all", "remote.origin.pushurl")
    if pushurls not in ([], [remote]):
        raise AcquaintError(
            f"origin has a pushurl {pushurls} that differs from {remote!r}; refusing"
        )
    fetching = _lines(root, run, "remote", "get-url", "--all", "origin")
    pushing = _lines(root, run, "remote", "get-url", "--push", "--all", "origin")
    if pushing != fetching:
        raise AcquaintError(
            f"git would push origin to {pushing} but fetch from {fetching} (a url.*.pushInsteadOf rewrite?); refusing"
        )
    return repo, remote


# A conflicted file holds all three markers; requiring all of them spares a record that merely quotes one.
_MARKER_PATTERNS = (r"^<{7}( |$)", r"^={7}$", r"^>{7}( |$)")


def _conflicted(root: Path, run: Runner, *, markers: bool = True) -> list[str]:
    """Paths left unresolved in the working tree: unmerged index entries, and (with ``markers``) text files that still hold conflict markers."""
    unmerged = _lines(
        root, run, "-c", "core.quotePath=false", "diff", "--name-only", "--diff-filter=U"
    )
    if not markers:
        return sorted(unmerged)
    grep = [e for pattern in _MARKER_PATTERNS for e in ("-e", pattern)]
    found = run(
        [
            "git",
            "-c",
            "core.quotePath=false",
            "grep",
            "--untracked",
            "-I",
            "-l",
            "--all-match",
            "-E",
            *grep,
        ],
        cwd=root,
    )
    if (
        found.returncode > 1
    ):  # 1 is "no match"; anything else means the check itself failed
        raise AcquaintError(
            f"could not scan {root} for conflict markers: {(found.stderr or '').strip()}"
        )
    marked = [line for line in (found.stdout or "").splitlines() if line.strip()]
    return sorted({*unmerged, *marked})


def _refuse_conflicts(
    root: Path, run: Runner, *, doing: str, markers: bool = True
) -> None:
    paths = _conflicted(root, run, markers=markers)
    if paths:
        raise AcquaintError(
            f"refusing to {doing}: unresolved conflicts in {', '.join(paths)}. "
            "Resolve them in the data root (a kept `git stash` may hold your edits), then try again"
        )


def _hook_path(root: Path, run: Runner) -> Path:
    effective = Path(
        _ok(
            run,
            ["git", "rev-parse", "--git-path", "hooks/pre-push"],
            cwd=root,
            what="locating the hook",
        )
    )
    return effective if effective.is_absolute() else root / effective


def _hook_active(root: Path, run: Runner, ns: SyncNamespace) -> bool:
    """Whether git will run the shipped guard here: the effective hook path holds it, unchanged and executable."""
    hook = _hook_path(root, run)
    if not hook.is_file() or hook.read_text(encoding="utf-8") != ns.hook_text():
        return False
    return sys.platform == "win32" or os.access(hook, os.X_OK)


def _install_hook(root: Path, run: Runner, ns: SyncNamespace) -> Path:
    common = Path(
        _ok(
            run,
            ["git", "rev-parse", "--git-common-dir"],
            cwd=root,
            what="locating the repository",
        )
    )
    hooks = (common if common.is_absolute() else root / common).resolve() / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-push"
    hook.write_text(ns.hook_text(), encoding="utf-8", newline="\n")
    hook.chmod(0o755)
    # Absolute on purpose: a relative core.hooksPath is not found from a linked worktree.
    _ok(
        run,
        ["git", "config", "core.hooksPath", hooks.as_posix()],
        cwd=root,
        what="pointing git at the pre-push guard",
    )
    return hook


def _check_existing_checkout(
    root: Path, repo: str, run: Runner, ns: SyncNamespace
) -> None:
    """Refuse to take over a repository that is neither this store nor a clone of ``repo``."""
    if not (root / ".git").exists():
        return
    recorded = next(iter(_lines(root, run, "config", "--get", ns.repo_key)), "")
    if recorded:
        if recorded.lower() != repo.lower():
            raise AcquaintError(
                f"{root} already syncs to {recorded}; refusing to point it at {repo}"
            )
        return
    origins = _lines(root, run, "config", "--get-all", "remote.origin.url")
    if not (len(origins) == 1 and _same_repo(origins[0], repo)):
        found = f"origin {', '.join(origins)}" if origins else "no origin"
        raise AcquaintError(
            f"{root} is already a git repository ({found}); {ns.name} takes over only an empty folder or a clone of {repo}"
        )


def init(
    root: str | os.PathLike,
    repo: str,
    *,
    remote_url: str | None = None,
    create: bool = True,
    dry_run: bool = False,
    namespace: SyncNamespace = ACQUAINT,
    run: Runner = run_command,
) -> dict[str, Any]:
    """Make ``root`` a checkout of the private GitHub repository ``repo``, creating it (private) if needed."""
    if not _REPO_RE.match(repo):
        raise AcquaintError(f"repo is written owner/name, got {repo!r}")
    if remote_url is not None:
        if remote_url.strip().lower().startswith("gcrypt::"):
            raise AcquaintError(
                "gcrypt remotes are not supported yet: the pre-push guard does not recognise git-remote-gcrypt's own push"
            )
        if not _same_repo(remote_url, repo):
            raise AcquaintError(
                f"--remote-url {remote_url!r} is not a url of the GitHub repository {repo} (one of: {', '.join(github_urls(repo))}); "
                "the visibility check must cover the repository that receives the data"
            )
    root = Path(root)
    _check_existing_checkout(root, repo, run, namespace)
    plan = [
        f"gh repo view {repo} (create it with `gh repo create {repo} --private` if missing)"
        if create
        else f"gh repo view {repo}",
        f"refuse unless gh reports {repo} on github.com as PRIVATE",
        f"git init {root} (branch {BRANCH}), or adopt an existing clone of {repo}; origin = {remote_url or 'the repository SSH URL from gh'}",
        f"record {namespace.repo_key} and {namespace.remote_key} in the repository's git config",
        "install the pre-push guard (absolute core.hooksPath); commit and push the current store",
    ]
    if dry_run:
        return {"repo": repo, "root": str(root), "dry_run": True, "plan": plan}

    exists = run(["gh", "repo", "view", repo, "--json", "visibility"]).returncode == 0
    if not exists:
        if not create:
            raise AcquaintError(
                f"{repo} does not exist (drop --existing-only to create it, private)"
            )
        _ok(
            run,
            [
                "gh",
                "repo",
                "create",
                repo,
                "--private",
                "--description",
                namespace.description,
            ],
            what="creating the repository",
        )
    seen = _require_private(repo, run, namespace)
    url = remote_url or _ok(
        run,
        ["gh", "repo", "view", repo, "--json", "sshUrl", "-q", ".sshUrl"],
        what="reading the SSH URL",
    )
    if not _same_repo(url, repo):
        raise AcquaintError(f"gh reported {url!r} as the URL of {repo}; refusing")

    root.mkdir(parents=True, exist_ok=True)
    if not (root / ".git").exists():
        _ok(run, ["git", "init", "-q"], cwd=root, what="git init")
        _ok(
            run,
            ["git", "symbolic-ref", "HEAD", f"refs/heads/{BRANCH}"],
            cwd=root,
            what="setting the branch",
        )
    if _lines(root, run, "config", "--get-all", "remote.origin.url"):
        run(["git", "config", "--unset-all", "remote.origin.url"], cwd=root)
        _ok(
            run,
            ["git", "config", "remote.origin.url", url],
            cwd=root,
            what="setting origin",
        )
    else:
        _ok(run, ["git", "remote", "add", "origin", url], cwd=root, what="adding origin")
    run(["git", "config", "--unset-all", "remote.origin.pushurl"], cwd=root)
    _ok(
        run,
        ["git", "config", namespace.repo_key, repo],
        cwd=root,
        what="recording the repository",
    )
    _ok(
        run,
        ["git", "config", namespace.remote_key, url],
        cwd=root,
        what="recording the remote",
    )
    for name, text in (
        (".gitignore", namespace.gitignore),
        (".gitattributes", _GITATTRIBUTES),
    ):
        if not (root / name).exists():
            (root / name).write_text(text, encoding="utf-8", newline="\n")
    hook = _install_hook(root, run, namespace)
    try:
        pushed = push(
            root,
            message=namespace.init_message,
            everything=True,
            namespace=namespace,
            run=run,
        )
    except AcquaintError as error:
        raise AcquaintError(
            f"the store is set up for {repo}, but the first push failed: {error}. Fix that, then run `{namespace.command} push`"
        ) from error
    return {
        "repo": repo,
        "root": str(root),
        "remote": url,
        "visibility": seen,
        "hook": str(hook),
        **{k: pushed[k] for k in ("committed", "pushed")},
    }


#: Files at the top of the store that belong to no record; a scoped push carries them when they changed.
STORE_FILES = (TOMBSTONES,)


def _changed_paths(root: Path, run: Runner) -> list[str]:
    """Every changed or untracked file, one path each (``-uall`` lists the files inside a new folder)."""
    done = run(
        ["git", "-c", "core.quotePath=false", "status", "--porcelain", "-z", "-uall"],
        cwd=root,
    )
    if done.returncode != 0:
        raise AcquaintError(f"git status failed: {(done.stderr or '').strip()}")
    paths: list[str] = []
    entries = iter(
        (done.stdout or "").split("\0")
    )  # not stripped: a leading space is part of the first entry
    for entry in entries:
        if len(entry) < 4:
            continue
        paths.append(entry[3:])
        if (
            entry[0] in "RC" or entry[1] in "RC"
        ):  # the next entry is the rename's old name
            next(entries, None)
    return paths


def _entity_of(path: str) -> str | None:
    """The record folder a path belongs to (``people/ada-lovelace``), or ``None`` for a file at the top of the store."""
    parts = path.split("/")
    return "/".join(parts[:2]) if len(parts) > 2 else None


def _is_named(entity: str, message: str) -> bool:
    """Whether the record's id appears in ``message`` as a whole word (``ada`` is not named by ``canada``)."""
    slug = re.escape(entity.rsplit("/", 1)[-1])
    return re.search(rf"(?<![\w-]){slug}(?![\w-])", message, re.I) is not None


def _record_folders(root: Path, run: Runner, folders: Sequence[str]) -> list[str]:
    """``folders`` normalised and checked: ``kind/slug``, and present in the working tree or in git."""
    checked = []
    for folder in folders:
        parts = folder.strip("/").split("/")
        if len(parts) != 2 or not all(parts) or {".", ".."} & set(parts):
            raise AcquaintError(
                f"{folder!r} is not a record folder like people/ada-lovelace"
            )
        name = "/".join(parts)
        known = (
            (root / name).exists()
            or _lines(root, run, "ls-files", "--", name)
            or _lines(root, run, "log", "-1", "--format=%H", "--", name)
        )
        if not known:  # a deleted record is known from history, so a push of its deletion can be retried
            raise AcquaintError(f"no record folder {name} in {root} (a typo in the id?)")
        checked.append(name)
    return checked


def _plan(
    root: Path, run: Runner, entities: Sequence[str], message: str | None
) -> dict[str, Any]:
    """What a push would commit: the changed paths, the records they belong to, and those the message leaves unnamed."""
    changed = _changed_paths(root, run)
    if entities:
        inside = tuple(f"{e}/" for e in entities)
        changed = [p for p in changed if p.startswith(inside) or p in STORE_FILES]
    touched = sorted({e for e in map(_entity_of, changed) if e})
    unnamed = [e for e in touched if message and not _is_named(e, message)]
    return {
        "changed": changed,
        "entities": touched,
        "unnamed": [] if entities else unnamed,
    }


def _commit(
    root: Path,
    run: Runner,
    plan: dict[str, Any],
    *,
    message: str | None,
    scoped: bool,
    ns: SyncNamespace,
) -> None:
    """Commit the planned paths (only those when ``scoped``; literal pathspecs, so no glob in a file name matches more)."""
    changed, touched = plan["changed"], plan["entities"]
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    listed = ", ".join(e.rsplit("/", 1)[-1] for e in touched) or f"{len(changed)} files"
    only = ["--", *changed] if scoped else []
    _ok(run, ["git", "--literal-pathspecs", "add", "-A", *only], cwd=root, what="git add")
    _ok(
        run,
        [
            "git",
            "--literal-pathspecs",
            "commit",
            "-q",
            "-m",
            message or ns.sync_message(stamp, listed),
            *(["--only", *only] if scoped else []),
        ],
        cwd=root,
        what="git commit",
    )


def push(
    root: str | os.PathLike,
    *,
    message: str | None = None,
    entities: Sequence[str] = (),
    everything: bool = False,
    dry_run: bool = False,
    namespace: SyncNamespace = ACQUAINT,
    run: Runner = run_command,
) -> dict[str, Any]:
    """Commit, rebase onto the remote, and push, after re-checking the remote, the guard and the visibility.

    ``entities`` are record folders (``people/ada-lovelace``): only those, plus
    :data:`STORE_FILES` when they changed, are committed; everything else stays
    uncommitted, so one session does not sweep up another's unfinished records. Without
    ``entities`` every change is committed, but when a ``message`` is given it must name
    each record being committed (its id appears in the text as a word) unless
    ``everything`` is set; a message naming only some of them is refused, listing the
    rest. With no ``message`` the default one lists the records. Other sessions' tombstones
    travel with a scoped push, and a renamed record needs both its old and new folder named.
    """
    root = Path(root)
    repo, remote = _configured(root, run, namespace)
    scoped = bool(entities)
    plan = _plan(root, run, _record_folders(root, run, entities), message)
    unnamed = [] if everything else plan["unnamed"]
    if dry_run:
        return {
            "repo": repo,
            "dry_run": True,
            "would_commit": len(plan["changed"]),
            "entities": plan["entities"],
            "unnamed_in_message": unnamed,
            "conflicted": _conflicted(root, run),
        }
    if unnamed:
        raise AcquaintError(
            f"the message does not name {', '.join(unnamed)}, which would be committed with it. "
            "Name them, push only your own records (`{namespace.command} push <id> ...`), or pass --all to commit everything"
        )
    if not _hook_active(root, run, namespace):
        raise AcquaintError(
            f"the pre-push guard is not active in {root} (moved data root, a changed core.hooksPath, or a hook that is not executable); "
            f"run `{namespace.command} init --repo {repo} --existing-only` first"
        )
    _require_private(repo, run, namespace)
    _refuse_conflicts(
        root, run, doing="commit and push"
    )  # nothing is unresolved from here on, so anything after is new
    if plan["changed"]:
        _commit(root, run, plan, message=message, scoped=scoped, ns=namespace)
    if run(["git", "rev-parse", "--verify", "HEAD"], cwd=root).returncode != 0:
        return {
            "repo": repo,
            "committed": False,
            "pushed": False,
            "note": "nothing to push yet",
        }
    if run(["git", "fetch", "-q", "origin", BRANCH], cwd=root).returncode == 0:
        _ok(
            run,
            ["git", "rebase", "-q", "--autostash", f"origin/{BRANCH}"],
            cwd=root,
            what="rebasing onto the remote",
        )
    _ok(
        run,
        ["git", "push", "-q", "-u", "origin", f"HEAD:{BRANCH}"],
        cwd=root,
        what="git push (the pre-push guard may have refused)",
    )
    # `rebase --autostash` exits 0 when re-applying another session's uncommitted edits conflicts.
    _refuse_conflicts(
        root,
        run,
        doing="finish: your commits were pushed, but uncommitted edits now conflict with the remote",
        markers=False,
    )
    return {
        "repo": repo,
        "remote": remote,
        "committed": bool(plan["changed"]),
        "changed_files": len(plan["changed"]),
        "entities": plan["entities"],
        "pushed": True,
    }


def pull(
    root: str | os.PathLike,
    *,
    dry_run: bool = False,
    namespace: SyncNamespace = ACQUAINT,
    run: Runner = run_command,
) -> dict[str, Any]:
    """Re-check the remote and its visibility, then rebase local work onto it (uncommitted edits are stashed and restored)."""
    root = Path(root)
    repo, remote = _configured(root, run, namespace)
    if dry_run:
        return {
            "repo": repo,
            "dry_run": True,
            "plan": [f"git pull --rebase --autostash origin {BRANCH}"],
        }
    _require_private(repo, run, namespace)
    _refuse_conflicts(root, run, doing="pull")
    _ok(
        run,
        ["git", "pull", "-q", "--rebase", "--autostash", "origin", BRANCH],
        cwd=root,
        what="git pull",
    )
    # `git pull --autostash` exits 0 when re-applying the stash conflicts, leaving markers.
    _refuse_conflicts(root, run, doing="finish the pull")
    head = _ok(
        run, ["git", "rev-parse", "--short", "HEAD"], cwd=root, what="git rev-parse"
    )
    return {"repo": repo, "remote": remote, "pulled": True, "head": head}


def status(
    root: str | os.PathLike,
    *,
    check_visibility: bool = True,
    namespace: SyncNamespace = ACQUAINT,
    run: Runner = run_command,
) -> dict[str, Any]:
    """Whether the store is synced, where to, uncommitted changes, ahead/behind, the guard, and live visibility.

    With ``check_visibility``, ``auto_commit`` says whether edits may be committed and pushed
    without the operator reviewing them first (:func:`sole_access`), and
    ``auto_commit_reason`` says why or why not.
    """
    root = Path(root)
    if not (root / ".git").exists():
        return {
            "synced": False,
            "root": str(root),
            "note": f"not set up: {namespace.command} init --repo <owner>/<name>",
        }
    repo, remote = _configured(root, run, namespace)
    dirty = [
        line
        for line in _ok(
            run, ["git", "status", "--porcelain"], cwd=root, what="git status"
        ).splitlines()
        if line
    ]
    result: dict[str, Any] = {
        "synced": True,
        "root": str(root),
        "repo": repo,
        "remote": remote,
        "uncommitted": len(dirty),
        "hook_installed": _hook_active(root, run, namespace),
    }
    if check_visibility:
        result["visibility"] = visibility(repo, run=run)
        sole, why = sole_access(repo, run=run)
        result.update(auto_commit=sole, auto_commit_reason=why)
        if run(["git", "fetch", "-q", "origin", BRANCH], cwd=root).returncode == 0:
            counts = _ok(
                run,
                ["git", "rev-list", "--left-right", "--count", f"HEAD...origin/{BRANCH}"],
                cwd=root,
                what="comparing with the remote",
            )
            ahead, behind = (int(n) for n in counts.split())
            result.update(ahead=ahead, behind=behind)
    return result
