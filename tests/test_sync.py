"""Sync to a private repository, tested without GitHub.

``gh`` is scripted: a fake runner answers Python's calls, and a fake ``gh`` first on
``PATH`` answers the hook. ``git`` is real. The GitHub URLs are redirected to local bare
repositories with ``url.<bare>.insteadOf`` in an isolated global git config, which is also
how a user's own URL rewriting behaves. No real repository is created.
"""

import os
import shutil
import subprocess
import sys

import pytest

from acquaint import sync
from acquaint.resources import data_text
from acquaint.store import AcquaintError

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="the pre-push guard and the fake gh are POSIX shell scripts")
REPO = "example/profiles"
URL = "git@github.com:example/profiles.git"
OTHER_URL = "git@github.com:example/elsewhere.git"


def _git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


@pytest.fixture(autouse=True)
def gitconfig(tmp_path, monkeypatch):
    config = tmp_path / "gitconfig"
    config.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "acquaint tests")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "tests" + "@" + "example.org")
    return config


def _bare(path):
    assert _git("init", "-q", "--bare", str(path)).returncode == 0
    return path


@pytest.fixture
def github(tmp_path, gitconfig):
    """The two GitHub URLs reach local bare repositories: ``{URL: bare, OTHER_URL: bare}``."""
    remotes = {URL: _bare(tmp_path / "profiles.git"), OTHER_URL: _bare(tmp_path / "elsewhere.git")}
    for url, bare in remotes.items():
        _git("config", "--file", str(gitconfig), f"url.{bare.as_posix()}.insteadOf", url)
    return remotes


@pytest.fixture
def scripted_gh(tmp_path, monkeypatch):
    """A ``gh`` executable first on PATH, for the hook; the test can change its answer."""
    answer = tmp_path / "visibility"
    answer.write_text("PRIVATE")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "gh"
    script.write_text(f'#!/bin/sh\ncat "{answer}"\n')
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return answer


class FakeGh:
    """Answers ``gh repo view`` and ``gh repo create``; passes ``git`` through to the real runner."""

    def __init__(self, visibility="PRIVATE", exists=True):
        self.visibility, self.exists, self.calls = visibility, exists, []
        self.collaborators = [REPO.split("/")[0]]

    def __call__(self, args, *, cwd=None):
        args = list(args)
        if args[0] != "gh":
            return sync.run_command(args, cwd=cwd)
        self.calls.append(args)
        done = lambda code, out="": subprocess.CompletedProcess(args, code, out, "" if code == 0 else "not found")  # noqa: E731
        if args[1:3] == ["repo", "create"]:
            assert "--private" in args, "a profile repository is only ever created private"
            self.exists = True
            return done(0)
        if args[1] == "api":
            path = args[2]
            if path.endswith("/collaborators"):
                return done(0, "\n".join(self.collaborators) + "\n")
            if path.endswith("/invitations"):
                return done(0, "")
            return done(0, f"true\t{REPO.split('/')[0]}\tUser\n")
        if args[1:3] == ["repo", "view"]:
            if not self.exists:
                return done(1)
            if ".sshUrl" in args:
                return done(0, URL + "\n")
            return done(0, self.visibility + "\n") if "-q" in args else done(0, "{}")
        raise AssertionError(f"unexpected gh call {args}")


@pytest.fixture
def store_root(tmp_path):
    root = tmp_path / "data"
    (root / "people" / "ada-lovelace").mkdir(parents=True)
    (root / "people" / "ada-lovelace" / "PROFILE.md").write_text("---\nname: Ada Lovelace\n---\n")
    return root


def _commit(folder, name="note.md"):
    (folder / name).write_text("change\n")
    _git("add", "-A", cwd=folder)
    _git("commit", "-q", "-m", "change", cwd=folder)


def _reached(bare, ref="main"):
    return _git("--git-dir", str(bare), "rev-parse", "--verify", ref).returncode == 0


# --------------------------------------------------------------- everywhere


def test_dry_run_plans_and_touches_nothing(store_root):
    def refuse(*args, **kwargs):
        raise AssertionError("a dry run runs nothing")

    result = sync.init(store_root, REPO, dry_run=True, run=refuse)
    assert result["dry_run"] and any("PRIVATE" in step for step in result["plan"])
    assert not (store_root / ".git").exists()


def test_refuses_a_repository_that_is_not_private(store_root):
    with pytest.raises(AcquaintError, match="not PRIVATE"):
        sync.init(store_root, REPO, remote_url=URL, run=FakeGh(visibility="PUBLIC"))
    assert not (store_root / ".git").exists(), "refused before the store became a repository"


@pytest.mark.parametrize(
    "remote",
    [
        OTHER_URL,
        "git@gitlab.com:example/profiles.git",
        "/srv/git/profiles.git",
        "https://github.com/example/profiles.git/",
        "git@github.com/example/profiles.git",
    ],
)
def test_the_remote_must_be_a_url_of_the_repository_whose_visibility_is_checked(store_root, remote):
    with pytest.raises(AcquaintError, match="is not a url of the GitHub repository"):
        sync.init(store_root, REPO, remote_url=remote, run=FakeGh())
    assert not (store_root / ".git").exists()


def test_gcrypt_remotes_are_refused_until_the_guard_supports_them(store_root):
    with pytest.raises(AcquaintError, match="not supported yet"):
        sync.init(store_root, REPO, remote_url="gcrypt::" + URL, dry_run=True)


def test_the_hook_accepts_exactly_the_url_forms_python_does():
    hook = data_text("hooks/pre-push")
    for form in sync.github_urls("$repo"):
        assert f'"{form}"' in hook, form


def test_init_refuses_to_take_over_an_unrelated_repository(store_root):
    assert _git("init", "-q", cwd=store_root).returncode == 0
    _git("remote", "add", "origin", "git@github.com:example/some-app.git", cwd=store_root)
    with pytest.raises(AcquaintError, match="already a git repository"):
        sync.init(store_root, REPO, remote_url=URL, run=FakeGh())
    assert _git("config", "--get-all", "remote.origin.url", cwd=store_root).stdout.split() == ["git@github.com:example/some-app.git"]


def test_rejects_a_malformed_repo_name(store_root):
    with pytest.raises(AcquaintError, match="owner/name"):
        sync.init(store_root, "profiles", dry_run=True)


def _answers(**by_path):
    """A runner answering ``gh api <path>`` from a table: path suffix -> (exit code, stdout)."""

    def run(args, *, cwd=None):
        path = args[2]
        code, out = next(v for k, v in by_path.items() if path.endswith(k))
        return subprocess.CompletedProcess(args, code, out, "")

    return run


SOLE = {"/invitations": (0, ""), "/collaborators": (0, "ada\n"), "/vault": (0, "true\tada\tUser\n")}


def test_sole_access_only_for_a_private_personal_repository_with_one_collaborator():
    assert sync.sole_access("ada/vault", run=_answers(**SOLE)) == (
        True,
        "ada/vault is private and ada is its only collaborator",
    )
    cases = {
        "not private": {**SOLE, "/vault": (0, "false\tada\tUser\n")},
        "organisation": {**SOLE, "/vault": (0, "true\tada\tOrganization\n")},
        "other collaborators: grace": {**SOLE, "/collaborators": (0, "ada\ngrace\n")},
        "pending invitation": {**SOLE, "/invitations": (0, "grace\n")},
        "could not read": {**SOLE, "/vault": (1, "")},
    }
    for reason, table in cases.items():
        sole, why = sync.sole_access("ada/vault", run=_answers(**table))
        assert not sole and reason in why, (reason, why)


def test_status_of_a_store_that_is_not_synced(store_root):
    assert sync.status(store_root, run=FakeGh())["synced"] is False


# ------------------------------------------------------------------ POSIX


@posix_only
def test_gh_is_pinned_to_github_com(scripted_gh, monkeypatch):
    scripted_gh.parent.joinpath("bin", "gh").write_text('#!/bin/sh\necho "$GH_HOST"\n')
    monkeypatch.setenv("GH_HOST", "enterprise.example.org")
    assert sync.run_command(["gh", "repo", "view"]).stdout.strip() == "github.com"


@posix_only
def test_init_creates_private_pushes_and_guards(store_root, github, scripted_gh):
    gh = FakeGh(exists=False)
    result = sync.init(store_root, REPO, run=gh)
    assert result["pushed"] and result["visibility"] == "PRIVATE" and result["remote"] == URL
    assert ["gh", "repo", "create", REPO, "--private", "--description", "acquaint profile store (private)"] in gh.calls
    assert "people/ada-lovelace/PROFILE.md" in _git("--git-dir", str(github[URL]), "ls-tree", "-r", "--name-only", "main").stdout
    assert os.path.isabs(_git("config", "core.hooksPath", cwd=store_root).stdout.strip())
    assert (store_root / ".gitattributes").is_file()

    status = sync.status(store_root, run=gh)
    assert (status["hook_installed"], status["visibility"], status["ahead"], status["behind"]) == (True, "PRIVATE", 0, 0)
    assert status["auto_commit"] is True, status["auto_commit_reason"]


@posix_only
def test_a_clone_of_the_same_repository_can_be_adopted(tmp_path, store_root, github, scripted_gh):
    sync.init(store_root, REPO, run=FakeGh())
    server = tmp_path / "server-data"
    assert _git("clone", "-q", "--branch", "main", URL, str(server)).returncode == 0
    assert sync.init(server, REPO, create=False, run=FakeGh())["pushed"]
    assert sync.status(server, run=FakeGh())["hook_installed"]


@posix_only
def test_push_and_pull_round_trip(tmp_path, store_root, github, scripted_gh):
    gh = FakeGh()
    sync.init(store_root, REPO, run=gh)
    (store_root / "people" / "ada-lovelace" / "PROFILE.md").write_text("---\nname: Ada Lovelace\naka: [Ada]\n---\n")
    assert sync.push(store_root, run=gh)["committed"]

    other = tmp_path / "other-clone"
    assert _git("clone", "-q", "--branch", "main", URL, str(other)).returncode == 0
    (other / "people" / "grace-example").mkdir(parents=True)
    _commit(other / "people" / "grace-example", "PROFILE.md")
    assert _git("push", "-q", "origin", "main", cwd=other).returncode == 0

    assert sync.pull(store_root, run=gh)["pulled"]
    assert (store_root / "people" / "grace-example" / "PROFILE.md").is_file()


@posix_only
def test_the_hook_refuses_every_other_destination_and_lost_privacy(store_root, github, scripted_gh):
    sync.init(store_root, REPO, run=FakeGh())
    _commit(store_root)

    scripted_gh.write_text("PUBLIC")
    refused = _git("push", "origin", "HEAD:main", cwd=store_root)
    assert refused.returncode != 0 and "not PRIVATE" in refused.stderr
    scripted_gh.write_text("PRIVATE")

    to_url = _git("push", str(github[OTHER_URL]), "HEAD:main", cwd=store_root)
    assert to_url.returncode != 0 and "origin only" in to_url.stderr

    _git("remote", "add", "mirror", OTHER_URL, cwd=store_root)
    to_other_remote = _git("push", "mirror", "HEAD:main", cwd=store_root)
    assert to_other_remote.returncode != 0 and "origin only" in to_other_remote.stderr

    _git("config", "remote.origin.pushurl", OTHER_URL, cwd=store_root)
    via_pushurl = _git("push", "origin", "HEAD:main", cwd=store_root)
    assert via_pushurl.returncode != 0 and "pushurl" in via_pushurl.stderr
    _git("config", "--unset-all", "remote.origin.pushurl", cwd=store_root)

    _git("remote", "set-url", "origin", OTHER_URL, cwd=store_root)
    _git("config", "acquaint.remote", OTHER_URL, cwd=store_root)
    repointed = _git("push", "origin", "HEAD:main", cwd=store_root)
    assert repointed.returncode != 0 and "is not a url of the GitHub repository" in repointed.stderr
    assert not _reached(github[OTHER_URL]), "nothing reached the other repository"


@posix_only
def test_a_second_origin_url_is_refused_by_the_hook_and_by_python(store_root, github, scripted_gh):
    sync.init(store_root, REPO, run=FakeGh())
    _commit(store_root)
    _git("config", "--unset-all", "remote.origin.url", cwd=store_root)
    _git("config", "--add", "remote.origin.url", OTHER_URL, cwd=store_root)
    _git("config", "--add", "remote.origin.url", URL, cwd=store_root)

    refused = _git("push", "origin", "HEAD:main", cwd=store_root)
    assert refused.returncode != 0 and "exactly one url" in refused.stderr
    with pytest.raises(AcquaintError, match="exactly one url"):
        sync.push(store_root, run=FakeGh())
    assert not _reached(github[OTHER_URL]), "nothing reached the other repository"


@posix_only
def test_a_push_insteadof_redirect_is_refused_by_the_hook_and_by_python(store_root, github, gitconfig, scripted_gh):
    sync.init(store_root, REPO, run=FakeGh())
    _commit(store_root)
    _git("config", "--file", str(gitconfig), f"url.{github[OTHER_URL].as_posix()}.pushInsteadOf", URL)

    refused = _git("push", "origin", "HEAD:main", cwd=store_root)
    assert refused.returncode != 0 and "pushInsteadOf" in refused.stderr
    with pytest.raises(AcquaintError, match="pushInsteadOf"):
        sync.push(store_root, run=FakeGh())
    assert not _reached(github[OTHER_URL])


@posix_only
def test_a_linked_worktree_is_guarded_too(tmp_path, store_root, github, scripted_gh):
    sync.init(store_root, REPO, run=FakeGh())
    worktree = tmp_path / "worktree"
    assert _git("worktree", "add", "-q", "-b", "side", str(worktree), cwd=store_root).returncode == 0
    _commit(worktree)

    to_url = _git("push", str(github[OTHER_URL]), "HEAD:refs/heads/side", cwd=worktree)
    assert to_url.returncode != 0 and "origin only" in to_url.stderr
    scripted_gh.write_text("PUBLIC")
    refused = _git("push", "origin", "HEAD:refs/heads/side", cwd=worktree)
    assert refused.returncode != 0 and "not PRIVATE" in refused.stderr


@posix_only
def test_python_push_and_pull_recheck_everything(tmp_path, store_root, github, scripted_gh):
    sync.init(store_root, REPO, run=FakeGh())
    with pytest.raises(AcquaintError, match="not PRIVATE"):
        sync.push(store_root, run=FakeGh(visibility="PUBLIC"))
    with pytest.raises(AcquaintError, match="not PRIVATE"):
        sync.pull(store_root, run=FakeGh(visibility="PUBLIC"))

    _git("config", "core.hooksPath", str(tmp_path / "some-other-hooks"), cwd=store_root)
    with pytest.raises(AcquaintError, match="guard is not active"):
        sync.push(store_root, run=FakeGh())
    sync.init(store_root, REPO, create=False, run=FakeGh())

    hook = sync._hook_path(store_root, FakeGh())
    hook.chmod(0o644)
    assert sync.status(store_root, run=FakeGh())["hook_installed"] is False
    with pytest.raises(AcquaintError, match="guard is not active"):
        sync.push(store_root, run=FakeGh())
    hook.chmod(0o755)
    assert sync.push(store_root, run=FakeGh())["pushed"]

    _git("config", "remote.origin.pushurl", OTHER_URL, cwd=store_root)
    with pytest.raises(AcquaintError, match="pushurl"):
        sync.push(store_root, run=FakeGh())
    _git("config", "--unset-all", "remote.origin.pushurl", cwd=store_root)

    _git("remote", "set-url", "origin", OTHER_URL, cwd=store_root)
    with pytest.raises(AcquaintError, match="refusing"):
        sync.push(store_root, run=FakeGh())


@posix_only
def test_a_conflicting_autostash_makes_pull_raise_and_push_refuse(tmp_path, store_root, github, scripted_gh):
    gh = FakeGh()
    sync.init(store_root, REPO, run=gh)
    profile = store_root / "people" / "ada-lovelace" / "PROFILE.md"
    other = tmp_path / "other-clone"
    assert _git("clone", "-q", "--branch", "main", URL, str(other)).returncode == 0
    (other / "people" / "ada-lovelace" / "PROFILE.md").write_text("---\nname: Ada Lovelace\naka: [Remote]\n---\n")
    _git("commit", "-aqm", "remote edit", cwd=other)
    assert _git("push", "-q", "origin", "main", cwd=other).returncode == 0

    profile.write_text("---\nname: Ada Lovelace\naka: [Local]\n---\n")  # uncommitted, same lines
    with pytest.raises(AcquaintError, match=r"unresolved conflicts in people/ada-lovelace/PROFILE.md"):
        sync.pull(store_root, run=gh)
    assert "<<<<<<<" in profile.read_text()
    with pytest.raises(AcquaintError, match="unresolved conflicts"):
        sync.push(store_root, run=gh)
    assert "<<<<<<<" not in _git("--git-dir", str(github[URL]), "show", "main:people/ada-lovelace/PROFILE.md").stdout


@posix_only
def test_push_refuses_a_record_that_holds_conflict_markers(store_root, github, scripted_gh):
    gh = FakeGh()
    sync.init(store_root, REPO, run=gh)
    (store_root / "people" / "ada-lovelace" / "PROFILE.md").write_text("<<<<<<< Updated upstream\nx\n=======\ny\n>>>>>>> Stashed changes\n")
    with pytest.raises(AcquaintError, match="unresolved conflicts"):
        sync.push(store_root, run=gh)


@posix_only
def test_a_record_that_only_quotes_one_marker_or_is_binary_does_not_block_push(store_root, github, scripted_gh):
    gh = FakeGh()
    sync.init(store_root, REPO, run=gh)
    folder = store_root / "people" / "ada-lovelace"
    (folder / "quote.md").write_text("> >>>>>>> a deeply nested email quote\n")
    (folder / "blob.bin").write_bytes(b"\x00\n<<<<<<< x\n=======\n>>>>>>> y\n")
    assert sync.push(store_root, dry_run=True, run=gh)["conflicted"] == []
    assert sync.push(store_root, run=gh)["pushed"]


def _two_dirty_records(store_root, gh):
    sync.init(store_root, REPO, run=gh)
    for slug in ("ada-lovelace", "grace-example"):
        folder = store_root / "people" / slug
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "PROFILE.md").write_text(f"---\nname: {slug}\nnote: changed\n---\n")
    return store_root / "people"


def _pushed_files(bare):
    return set(_git("--git-dir", str(bare), "ls-tree", "-r", "--name-only", "main").stdout.split())


@posix_only
def test_a_scoped_push_commits_only_the_named_records(store_root, github, scripted_gh):
    gh = FakeGh()
    people = _two_dirty_records(store_root, gh)
    (store_root / "_tombstones.yaml").write_text("tombstones: []\n")  # someone else's tombstone: not carried
    result = sync.push(store_root, entities=["people/ada-lovelace"], message="ada only", run=gh)
    assert result["pushed"] and result["entities"] == ["people/ada-lovelace"]
    files = _pushed_files(github[URL])
    assert "people/ada-lovelace/PROFILE.md" in files and "_tombstones.yaml" not in files
    assert "people/grace-example/PROFILE.md" not in files
    status = _git("status", "--porcelain", cwd=store_root).stdout
    assert "people/grace-example" in status and "people/ada-lovelace" not in status
    assert (people / "grace-example" / "PROFILE.md").read_text().endswith("changed\n---\n")
    # the other record stays out of the way of the next scoped push, and of a pull
    assert sync.push(store_root, entities=["people/grace-example"], run=gh)["pushed"]
    assert "people/grace-example/PROFILE.md" in _pushed_files(github[URL])


@posix_only
def test_an_unscoped_push_refuses_a_message_that_leaves_a_record_unnamed(store_root, github, scripted_gh):
    gh = FakeGh()
    _two_dirty_records(store_root, gh)
    with pytest.raises(AcquaintError, match="does not name people/grace-example"):
        sync.push(store_root, message="ada-lovelace: new aka", run=gh)
    assert "people/grace-example/PROFILE.md" not in _pushed_files(github[URL])
    plan = sync.push(store_root, message="ada-lovelace: new aka", dry_run=True, run=gh)
    assert plan["unnamed_in_message"] == ["people/grace-example"] and plan["entities"] == ["people/ada-lovelace", "people/grace-example"]
    assert sync.push(store_root, message="ada-lovelace: new aka", everything=True, run=gh)["committed"]
    assert sync.push(store_root, message="ada-lovelace and grace-example", run=gh)["pushed"]


@posix_only
def test_the_default_message_lists_the_records_and_needs_no_naming(store_root, github, scripted_gh):
    gh = FakeGh()
    _two_dirty_records(store_root, gh)
    assert sync.push(store_root, run=gh)["committed"]
    assert "ada-lovelace, grace-example" in _git("log", "-1", "--format=%s", cwd=store_root).stdout


@posix_only
def test_a_scoped_push_rejects_anything_but_an_existing_record_folder(store_root, github, scripted_gh):
    gh = FakeGh()
    sync.init(store_root, REPO, run=gh)
    for bad in ("people", "people/ada/../../x", "../people/a", "people/a/b", "people/..", "./people", "people/."):
        with pytest.raises(AcquaintError, match="record folder"):
            sync.push(store_root, entities=[bad], run=gh)
    with pytest.raises(AcquaintError, match="no record folder people/typo"):
        sync.push(store_root, entities=["/people/typo/"], run=gh)
    assert sync.push(store_root, entities=["/people/ada-lovelace/"], run=gh)["entities"] == []


def test_a_record_is_named_only_by_its_whole_id():
    assert sync._is_named("people/ada", "ada: new aka") and sync._is_named("people/ada", "Update for Ada.")
    assert not sync._is_named("people/ada", "trip to canada") and not sync._is_named("people/ada", "ada-lovelace: aka")


@posix_only
def test_a_scoped_push_pushes_before_reporting_a_conflict_in_another_records_edits(tmp_path, store_root, github, scripted_gh):
    gh = FakeGh()
    people = _two_dirty_records(store_root, gh)
    sync.push(store_root, everything=True, run=gh)
    other = tmp_path / "other-clone"
    assert _git("clone", "-q", "--branch", "main", URL, str(other)).returncode == 0
    (other / "people" / "grace-example" / "PROFILE.md").write_text("---\nname: grace-example\nnote: remote\n---\n")
    _git("commit", "-aqm", "remote edit", cwd=other)
    assert _git("push", "-q", "origin", "main", cwd=other).returncode == 0
    (people / "grace-example" / "PROFILE.md").write_text("---\nname: grace-example\nnote: local\n---\n")  # uncommitted
    (people / "ada-lovelace" / "PROFILE.md").write_text("---\nname: Ada Lovelace\nnote: newer\n---\n")
    with pytest.raises(AcquaintError, match="your commits were pushed.*people/grace-example/PROFILE.md"):
        sync.push(store_root, entities=["people/ada-lovelace"], run=gh)
    assert "newer" in _git("--git-dir", str(github[URL]), "show", "main:people/ada-lovelace/PROFILE.md").stdout


@posix_only
def test_the_tool_scopes_by_id_and_a_dry_run_says_when_it_would_refuse(tmp_path, github, scripted_gh, monkeypatch):
    from acquaint import tools

    monkeypatch.setenv("ACQUAINT_DATA_DIR", str(tmp_path / "data"))
    tools.new("person", "Ada Lovelace")
    tools.new("person", "Grace Example")
    gh, real = FakeGh(), sync.run_command
    fake = lambda args, *, cwd=None: gh(args, cwd=cwd) if args[0] == "gh" else real(args, cwd=cwd)  # noqa: E731
    for fn in (sync.init, sync.push, sync.pull, sync.status, sync.visibility, sync.sole_access):
        monkeypatch.setattr(fn, "__kwdefaults__", {**(fn.__kwdefaults__ or {}), "run": fake})
    sync.init(tmp_path / "data", REPO)
    tools.remember("ada-lovelace", "likes tea", kind="preference", source="note")
    tools.remember("grace-example", "likes coffee", kind="preference", source="note")
    plan = tools.sync_push(message="ada-lovelace: tea", dry_run=True)
    assert plan["summary"].startswith("would refuse") and "grace-example" in plan["summary"]
    done = tools.sync_push(["person:ada-lovelace"], message="ada-lovelace: tea")
    assert done["pushed"] and done["entities"] == ["people/ada-lovelace"]
    assert "people/grace-example" in _git("status", "--porcelain", "-uall", cwd=tmp_path / "data").stdout


@posix_only
def test_a_scoped_push_of_a_deleted_record_can_be_retried_and_a_clean_push_is_not_failed_by_old_markers(tmp_path, store_root, github, scripted_gh):
    gh = FakeGh()
    sync.init(store_root, REPO, run=gh)
    shutil.rmtree(store_root / "people" / "ada-lovelace")
    _git("config", "core.hooksPath", str(tmp_path / "x"), cwd=store_root)  # the first attempt fails at the guard
    with pytest.raises(AcquaintError, match="guard is not active"):
        sync.push(store_root, entities=["people/ada-lovelace"], run=gh)
    sync.init(store_root, REPO, create=False, run=gh)  # re-installs the guard; commits the deletion
    assert sync.push(store_root, entities=["people/ada-lovelace"], run=gh)["pushed"]  # known from history
    # a committed file that merely holds markers (pushed with raw git elsewhere) does not fail a clean push
    other = tmp_path / "other-clone"
    assert _git("clone", "-q", "--branch", "main", URL, str(other)).returncode == 0
    (other / "old.md").write_text("<<<<<<< a\nx\n=======\ny\n>>>>>>> b\n")
    _git("add", "-A", cwd=other)
    _git("commit", "-qm", "markers", cwd=other)
    assert _git("push", "-q", "origin", "main", cwd=other).returncode == 0
    (store_root / "people" / "grace-example").mkdir(parents=True)
    (store_root / "people" / "grace-example" / "PROFILE.md").write_text("---\nname: g\n---\n")
    assert sync.push(store_root, entities=["people/grace-example"], run=gh)["pushed"]


OTHER_APP = sync.SyncNamespace(name="notes-app", command="notes sync", subject="notes", description="notes (private)", gitignore="cache/\n")


@posix_only
def test_another_app_can_reuse_the_mechanism_through_a_namespace(store_root, github, scripted_gh):
    gh = FakeGh(exists=False)
    result = sync.init(store_root, REPO, namespace=OTHER_APP, run=gh)
    assert result["pushed"] and ["gh", "repo", "create", REPO, "--private", "--description", "notes (private)"] in gh.calls
    assert _git("config", "--get", "notes-app.repo", cwd=store_root).stdout.strip() == REPO
    assert _git("config", "--get", "acquaint.repo", cwd=store_root).stdout.strip() == ""
    assert _git("log", "-1", "--format=%s", cwd=store_root).stdout.strip() == "notes-app: initialise store"
    assert (store_root / ".gitignore").read_text() == "cache/\n"
    hook = sync._hook_path(store_root, gh).read_text()
    assert "notes-app.repo" in hook and "acquaint" not in hook and "__" not in hook
    assert sync.status(store_root, namespace=OTHER_APP, run=gh)["hook_installed"]
    with pytest.raises(AcquaintError, match="not set up by `acquaint sync init`"):
        sync.status(store_root, run=gh)  # the default namespace does not see another app's configuration


@posix_only
def test_a_namespace_names_its_own_commands_in_errors(store_root, github, scripted_gh):
    with pytest.raises(AcquaintError, match="`notes sync init --repo"):
        sync.push(store_root, namespace=OTHER_APP, run=FakeGh())
    assert "acquaint sync init" in sync.status(store_root)["note"]
    assert "notes sync init" in sync.status(store_root, namespace=OTHER_APP)["note"]
    assert "acquaint.repo" in sync.ACQUAINT.hook_text()


def test_the_default_namespace_renders_the_hook_every_existing_store_has_installed():
    """Stores set up before the namespace existed hold this exact text; a changed byte would mark every guard inactive."""
    from pathlib import Path

    assert sync.ACQUAINT.hook_text() == (Path(__file__).parent / "pre_push_v1.sh").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "fields",
    [
        {"name": "my_app"},
        {"name": "1app"},
        {"subject": "$(touch x)"},
        {"subject": 'a"; touch x; echo "'},
        {"command": "app `sync`"},
        {"store": "line\nbreak"},
        {"subject": "__NAME__"},
    ],
)
def test_a_namespace_cannot_carry_what_the_shell_or_git_would_misread(fields):
    with pytest.raises(AcquaintError):
        sync.SyncNamespace(**fields)


@posix_only
def test_the_tombstone_travels_with_a_deleted_record_only(store_root, github, scripted_gh):
    gh = FakeGh()
    _two_dirty_records(store_root, gh)
    sync.push(store_root, everything=True, run=gh)
    shutil.rmtree(store_root / "people" / "ada-lovelace")
    (store_root / "_tombstones.yaml").write_text("tombstones: [salted]\n")
    (store_root / "people" / "grace-example" / "PROFILE.md").write_text("---\nname: grace-example\nnote: again\n---\n")
    assert sync.push(store_root, entities=["people/grace-example"], run=gh)["entities"] == ["people/grace-example"]
    assert "_tombstones.yaml" not in _pushed_files(github[URL])
    done = sync.push(store_root, entities=["people/ada-lovelace"], run=gh)
    assert done["pushed"] and "_tombstones.yaml" in _pushed_files(github[URL])
    assert "people/ada-lovelace/PROFILE.md" not in _pushed_files(github[URL])
    assert sync.status(store_root, run=gh, check_visibility=False)["uncommitted"] == 0
