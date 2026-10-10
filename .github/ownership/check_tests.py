"""Regression tests for ownership authorization and untrusted PR metadata."""

import base64
import unittest
from pathlib import Path
from unittest.mock import Mock

from check import (
  CORE_TEAM,
  Access,
  Checker,
  GitHub,
  changed_paths,
  evaluate,
  parse_owners,
)


def review(login: str, state: str = "APPROVED", sha: str = "head", number: int = 1) -> dict:
  """Create a review fixture with a stable author and commit."""
  return {
    "id": number,
    "user": {"login": login, "type": "User"},
    "state": state,
    "commit_id": sha,
  }


def pull() -> dict:
  """Create a PR fixture containing only fields used by the checker."""
  return {
    "number": 1,
    "state": "open",
    "user": {"login": "alice", "id": 10},
    "head": {"sha": "head"},
    "base": {"ref": "main", "repo": {"full_name": "PyLabRobot/pylabrobot"}},
    "changed_files": 1,
  }


class OwnershipTests(unittest.TestCase):
  """Cover author exemptions, review state, matching, and cross-owner changes."""

  def setUp(self) -> None:
    """Use two independent owners and a core-team member."""
    self.owners = parse_owners(
      f"* {CORE_TEAM}\n/frontend/ @alice\n/backend/ @bob\n"
      "/frontend/shared/ @alice @bob # @ignored\n"
    )
    self.writers = {"alice", "bob", "core"}

  def can_write(self, login: str) -> bool:
    """Model current collaborator access."""
    return login.lower() in self.writers

  def owns(self, login: str, owner: str) -> bool:
    """Model direct owners and core-team membership."""
    return (owner.lower() == "@" + login.lower()) or (
      owner == CORE_TEAM and login.lower() == "core"
    )

  def result(
    self,
    paths: set,
    author: str = "alice",
    confirmed: bool = True,
    reviews: list = (),
  ) -> bool:
    """Evaluate a fixture through the real authorization policy."""
    return evaluate(
      self.owners, paths, author, "head", confirmed, list(reviews), self.can_write, self.owns
    )[0]

  def test_owner_can_merge_self_authored_changes(self) -> None:
    """An authenticated author action covers only that author's paths."""
    self.assertTrue(self.result({"frontend/a.py"}))
    self.assertFalse(self.result({"backend/b.py"}))
    self.assertFalse(self.result({"frontend/a.py", "backend/b.py"}))

  def test_cross_owner_pr_needs_current_owner_approval(self) -> None:
    """A second owner's current approval covers a mixed-path PR."""
    paths = {"frontend/a.py", "backend/b.py"}
    self.assertTrue(self.result(paths, reviews=[review("bob")]))
    self.assertFalse(self.result(paths, reviews=[review("bob", sha="old")]))

  def test_other_persons_pr_can_be_approved(self) -> None:
    """An owner can authorize a contributor's PR through the normal review UI."""
    self.assertTrue(self.result({"frontend/a.py"}, author="outsider", reviews=[review("alice")]))
    self.assertFalse(self.result({"frontend/a.py"}, author="outsider", reviews=[review("bob")]))

  def test_third_party_push_does_not_inherit_authorization(self) -> None:
    """The PR author's name alone never grants authority on an unconfirmed head."""
    self.assertFalse(self.result({"frontend/a.py"}, confirmed=False))
    self.assertFalse(self.result({"frontend/a.py"}, confirmed=False, reviews=[review("alice")]))

  def test_core_team_retains_repository_wide_authority(self) -> None:
    """Core authors and reviewers can authorize changes across ownership boundaries."""
    self.assertTrue(self.result({"backend/b.py"}, author="core"))
    self.assertTrue(self.result({"backend/b.py"}, reviews=[review("core")]))

  def test_read_only_owner_cannot_authorize(self) -> None:
    """Removing write access invalidates ownership immediately on evaluation."""
    self.writers.remove("alice")
    self.assertFalse(self.result({"frontend/a.py"}))
    self.assertFalse(self.result({"frontend/a.py"}, author="outsider", reviews=[review("alice")]))

  def test_comments_dismissals_and_change_requests(self) -> None:
    """Comments preserve decisions; dismissals and change requests remove approval."""
    paths = {"backend/b.py"}
    self.assertTrue(
      self.result(paths, reviews=[review("bob"), review("bob", "COMMENTED", number=2)])
    )
    self.assertFalse(
      self.result(paths, reviews=[review("bob"), review("bob", "DISMISSED", number=2)])
    )
    self.assertFalse(
      self.result(paths, author="core", reviews=[review("bob", "CHANGES_REQUESTED")])
    )
    self.assertTrue(
      self.result(paths, author="core", reviews=[review("outsider", "CHANGES_REQUESTED")])
    )

  def test_bot_review_does_not_authorize(self) -> None:
    """Only a human owner can authorize changes."""
    bot = review("bob")
    bot["user"]["type"] = "Bot"
    self.assertFalse(self.result({"backend/b.py"}, reviews=[bot]))

  def test_no_owner_is_not_an_approval_exemption(self) -> None:
    """An empty owner rule requires the core team instead of granting everyone access."""
    self.owners = parse_owners("* @alice\n/private/\n")
    self.assertFalse(self.result({"private/a.py"}))
    self.assertTrue(self.result({"private/a.py"}, author="core"))

  def test_matching_precedence_boundaries_case_and_inline_comments(self) -> None:
    """Matching cannot spill into adjacent paths or interpret comment mentions as owners."""
    self.assertEqual(self.owners.of("frontend/shared/a.py"), ["@alice", "@bob"])
    self.assertEqual(self.owners.of("frontend-extra/a.py"), [CORE_TEAM])
    self.assertEqual(self.owners.of("Frontend/a.py"), [CORE_TEAM])
    self.assertEqual(self.owners.of("other/frontend/a.py"), [CORE_TEAM])
    owners = parse_owners("* @core\n/docs/* @alice\n/src/STAR* @bob\n")
    self.assertEqual(owners.of("docs/a.md"), ["@alice"])
    self.assertEqual(owners.of("docs/nested/a.md"), ["@core"])
    self.assertEqual(owners.of("src/STAR_tests.py"), ["@bob"])
    self.assertEqual(owners.of("src/STAR/device.py"), ["@bob"])

  def test_unsupported_patterns_and_owner_formats_fail_closed(self) -> None:
    """Unknown syntax must not silently discard an access restriction."""
    for text in (
      "",
      "!private @alice",
      "/[ab]/ @alice",
      "/**/ @alice",
      "unanchored/ @alice",
      "/a\\ b @alice",
      "/a a@example.com",
      "/a not-an-owner",
      "/a/../b @alice",
    ):
      with self.subTest(text=text), self.assertRaises(ValueError):
        parse_owners(text)

  def test_real_repository_assignments(self) -> None:
    """Keep the real path assignments compatible with the dependency-free matcher."""
    owners = parse_owners(Path(__file__).parents[1].joinpath("CODEOWNERS").read_text())
    for path, expected in (
      ("README.md", CORE_TEAM),
      (".github/workflows/ownership-policy.yml", CORE_TEAM),
      ("pylabrobot/liquid_handling/backends/hamilton/STAR_tests.py", "@BioCam"),
      ("pylabrobot/legacy/plate_reading/tecan/infinite_backend_tests.py", "@hazlamshamin"),
      ("pylabrobot/li_cor/odyssey/backend.py", "@vcjdeboer"),
      ("pylabrobot/thermo_fisher/nanodrop_1000/backend.py", "@vcjdeboer"),
    ):
      with self.subTest(path=path):
        self.assertEqual(owners.of(path), [expected])

  def test_rename_and_delete_require_original_path_authority(self) -> None:
    """Renaming someone else's file into an owned directory must remain blocked."""
    paths = changed_paths(
      [{"filename": "frontend/a.py", "previous_filename": "backend/a.py", "status": "renamed"}],
      1,
    )
    self.assertFalse(self.result(paths))
    self.assertTrue(self.result(paths, reviews=[review("bob")]))
    deleted = changed_paths([{"filename": "backend/a.py", "status": "removed"}], 1)
    self.assertFalse(self.result(deleted))

  def test_truncated_and_empty_file_lists_fail_closed(self) -> None:
    """GitHub's file-list limit cannot allow a partially checked PR."""
    with self.assertRaises(ValueError):
      changed_paths([{"filename": "frontend/a.py", "status": "added"}], 3001)
    with self.assertRaises(ValueError):
      changed_paths([], 0)


class IntegrationTests(unittest.TestCase):
  """Exercise pagination, trusted receipts, API errors, and snapshot races."""

  def test_pagination(self) -> None:
    """Never stop after the first 100 files, reviews, or team members."""
    api = GitHub("unused")
    api.request = Mock(side_effect=[list(range(100)), [100]])
    self.assertEqual(api.pages("/test?existing=yes"), list(range(101)))
    self.assertIn("&per_page=100&page=2", api.request.call_args.args[0])

  def test_author_receipt_rejects_other_workflows_and_other_senders(self) -> None:
    """Only a real target-PR event from its author creates an authorization receipt."""
    api = Mock()
    checker = Checker(api, 42)
    event = {"action": "synchronize", "pull_request": pull(), "sender": {"id": 10, "type": "User"}}
    checker.record_author_event("workflow_dispatch", event)
    api.request.assert_not_called()
    event["sender"]["id"] = 99
    checker.record_author_event("pull_request_target", event)
    api.request.assert_not_called()
    event["sender"]["id"] = 10
    checker.record_author_event("pull_request_target", event)
    payload = api.request.call_args.args[2]
    self.assertEqual(payload["head_sha"], "head")
    self.assertEqual(payload["external_id"], "author:10:main")

  def test_receipts_are_pinned_to_app_author_and_head(self) -> None:
    """A forged check name from GitHub Actions must never satisfy author authorization."""
    api = Mock()
    checker = Checker(api, 42)
    receipt = {
      "app": {"id": 42},
      "head_sha": "head",
      "external_id": "author:10:main",
      "conclusion": "neutral",
    }
    api.pages.return_value = [receipt]
    self.assertTrue(checker.author_confirmed(pull()))
    for key, value in (
      ("app", {"id": 15368}),
      ("head_sha", "old"),
      ("external_id", "author:99:main"),
    ):
      with self.subTest(key=key):
        api.pages.return_value = [dict(receipt, **{key: value})]
        self.assertFalse(checker.author_confirmed(pull()))

  def test_membership_failure_does_not_grant_access(self) -> None:
    """An unavailable team API is an error, never an empty restriction."""
    api = Mock()
    api.request.side_effect = [{"permission": "write"}, RuntimeError("API unavailable")]
    with self.assertRaises(RuntimeError):
      Access(api).owns("alice", "@PyLabRobot/core-team")

  def test_team_permission_media_type_and_membership(self) -> None:
    """Request permission JSON instead of GitHub's default empty 204 response."""
    api = Mock()
    api.request.side_effect = [{"permission": "write"}, {"permissions": {"push": True}}]
    api.pages.return_value = [{"login": "alice"}]
    self.assertTrue(Access(api).owns("alice", "@PyLabRobot/core-team"))
    self.assertEqual(
      api.request.call_args.kwargs["accept"], "application/vnd.github.v3.repository+json"
    )

  def test_head_changes_during_evaluation_fail_closed(self) -> None:
    """An owner approval cannot be reused after a concurrent push."""
    api = Mock()
    checker = Checker(api, 42)
    checker.author_confirmed = Mock(return_value=True)
    checker.access = Mock()
    checker.access.can_write.return_value = True
    checker.access.owns.return_value = True
    changed = dict(pull(), head={"sha": "new-head"})
    api.request.side_effect = [pull(), changed, {"object": {"sha": "base"}}]
    api.pages.side_effect = [[{"filename": "frontend/a.py", "status": "added"}], [], []]
    _, passed, _ = checker.inspect(1, "base", parse_owners("* @alice"))
    self.assertFalse(passed)

  def test_failed_evaluation_publishes_failure(self) -> None:
    """Unexpected errors must finish the check as failure rather than success or neutral."""
    api = Mock()
    api.pages.return_value = [pull()]
    api.request.side_effect = [
      {"object": {"sha": "base"}},
      {"encoding": "base64", "content": base64.b64encode(b"* @alice").decode()},
      {"id": 7},
      {},
    ]
    checker = Checker(api, 42)
    checker.inspect = Mock(side_effect=RuntimeError("API unavailable"))
    checker.run("workflow_dispatch", {})
    self.assertEqual(api.request.call_args.args[2]["conclusion"], "failure")

  def test_shared_head_aggregates_all_prs(self) -> None:
    """A passing PR must not overwrite a failing PR's check on the same commit."""
    api = Mock()
    api.pages.return_value = [pull(), dict(pull(), number=2)]
    api.request.side_effect = [
      {"object": {"sha": "base"}},
      {"encoding": "base64", "content": base64.b64encode(b"* @alice").decode()},
      {"id": 7},
      {},
    ]
    checker = Checker(api, 42)
    checker.inspect = Mock(side_effect=[(pull(), True, "allowed"), (pull(), False, "blocked")])
    checker.run("workflow_dispatch", {}, only_pr=1)
    self.assertEqual(checker.inspect.call_count, 2)
    self.assertEqual(api.request.call_args.args[2]["conclusion"], "failure")


if __name__ == "__main__":
  unittest.main()
