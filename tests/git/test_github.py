import base64
from types import SimpleNamespace as NS

from fastcore.net import HTTP404NotFoundError

from deployground.config.models import RepoRef, TokenRef
from deployground.git.base import FileChange
from deployground.git.github import GitHubClient, branch_name

REPO = RepoRef(owner="acme", name="gitops", branch="main", authRef=TokenRef(tokenEnv="GH_TOKEN"))


class FakeApi:
    """Records every call; mimics the ghapi surface GitHubClient uses."""

    def __init__(self, files: dict[str, str] | None = None) -> None:
        self.files = dict(files or {})  # base-branch files
        self.calls: list[tuple[str, dict]] = []
        outer = self

        def rec(name, result=None):
            def fn(**kw):
                outer.calls.append((name, kw))
                return result

            return fn

        def get_content(**kw):
            outer.calls.append(("get_content", kw))
            if kw["path"] not in outer.files:
                raise HTTP404NotFoundError("http://x", {}, None)
            return NS(sha=f"sha-{kw['path']}")

        tree = [NS(path=p, type="blob", sha=f"sha-{p}") for p in self.files]
        tree.append(NS(path="dir", type="tree", sha="t"))
        self.git = NS(
            get_ref=rec("get_ref", NS(object=NS(sha="base-sha"))),
            create_ref=rec("create_ref"),
            get_tree=rec("get_tree", NS(tree=tree)),
            get_blob=lambda **kw: NS(
                content=base64.b64encode(self.files[kw["file_sha"][4:]].encode()).decode()
            ),
        )
        self.repos = NS(
            get_content=get_content,
            create_or_update_file_contents=rec("put_file"),
            delete_file=rec("delete_file"),
        )
        self.pulls = NS(
            create=rec("create_pr", NS(number=7, html_url="https://github.com/acme/gitops/pull/7")),
            merge=rec("merge_pr"),
        )

    def names(self) -> list[str]:
        return [n for n, _ in self.calls]


def client_with(api: FakeApi) -> GitHubClient:
    client = GitHubClient(env={"GH_TOKEN": "t"})
    client._api = lambda repo: api
    return client


def test_open_pr_batches_all_changes_one_pr():
    api = FakeApi({"a.yaml": "old"})
    changes = [
        FileChange(REPO, "a.yaml", "new-a"),
        FileChange(REPO, "b.yaml", "new-b"),
        FileChange(REPO, "c.yaml", "new-c"),
    ]
    result = client_with(api).open_pr(REPO, "deployground/x-1", "Deploy x", changes, False)

    assert api.names().count("create_ref") == 1
    assert api.names().count("put_file") == 3
    assert api.names().count("create_pr") == 1
    assert "merge_pr" not in api.names()
    ref = next(kw for n, kw in api.calls if n == "create_ref")
    assert ref == {"ref": "refs/heads/deployground/x-1", "sha": "base-sha"}
    puts = [kw for n, kw in api.calls if n == "put_file"]
    assert all(kw["branch"] == "deployground/x-1" for kw in puts)
    assert puts[0]["sha"] == "sha-a.yaml" and "sha" not in puts[1]  # update vs create
    assert base64.b64decode(puts[1]["content"]) == b"new-b"
    pr = next(kw for n, kw in api.calls if n == "create_pr")
    assert pr == {"head": "deployground/x-1", "base": "main", "title": "Deploy x"}
    assert (result.url, result.number, result.merged) == (
        "https://github.com/acme/gitops/pull/7",
        7,
        False,
    )


def test_delete_change_removes_file():
    api = FakeApi({"gone.yaml": "x"})
    client_with(api).open_pr(REPO, "b", "t", [FileChange(REPO, "gone.yaml", None)], False)

    deletes = [kw for n, kw in api.calls if n == "delete_file"]
    assert len(deletes) == 1
    assert deletes[0]["path"] == "gone.yaml"
    assert deletes[0]["sha"] == "sha-gone.yaml"
    assert deletes[0]["branch"] == "b"
    assert "put_file" not in api.names()


def test_auto_merge_merges_when_true():
    api = FakeApi()
    result = client_with(api).open_pr(REPO, "b", "t", [FileChange(REPO, "a", "x")], True)

    assert result.merged is True
    merges = [kw for n, kw in api.calls if n == "merge_pr"]
    assert merges == [{"pull_number": 7}]
    assert api.names().index("create_pr") < api.names().index("merge_pr")


def test_list_files_returns_paths_and_content_under_prefix():
    api = FakeApi({"stacks/a.yaml": "A", "stacks/b.yaml": "B", "other/c.yaml": "C"})
    files = client_with(api).list_files(REPO, "stacks/")

    assert sorted(files) == [("stacks/a.yaml", "A"), ("stacks/b.yaml", "B")]
    tree_call = next(kw for n, kw in api.calls if n == "get_tree")
    assert tree_call["tree_sha"] == "main"


def test_branch_name_is_slugged_and_prefixed():
    name = branch_name("Deploy My Stack!! to dev")
    assert name.startswith("deployground/deploy-my-stack-to-dev-")
    assert branch_name("x") != branch_name("x")
