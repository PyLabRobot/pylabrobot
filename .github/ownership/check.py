"""Publish CODEOWNERS authorization checks using a dedicated GitHub App."""

import argparse
import base64
import fnmatch
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

REPOSITORY = "PyLabRobot/pylabrobot"
BASE_BRANCH = "main"
CORE_TEAM = "@PyLabRobot/core-team"
CHECK_NAME = "ownership-policy"
OWNER_NAME = re.compile(r"@[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)?\Z")


class GitHub:
  """Access only GitHub's API, with bounded requests and explicit pagination."""

  def __init__(self, token: str):
    """Store the token without logging it."""
    self.token = token

  def request(
    self,
    path: str,
    method: str = "GET",
    data: Optional[dict] = None,
    accept: str = "application/vnd.github+json",
  ) -> Any:
    """Return decoded API data; errors never turn into an authorization success."""
    if not path.startswith("/") or path.startswith("//"):
      raise ValueError("Expected a relative GitHub API path")
    request = Request(
      "https://api.github.com" + path,
      data=None if data is None else json.dumps(data).encode(),
      method=method,
      headers={
        "Authorization": f"Bearer {self.token}",
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
      },
    )
    with urlopen(request, timeout=30) as response:
      return json.load(response)

  def pages(self, path: str, key: Optional[str] = None) -> List[dict]:
    """Fetch every page, including nested check-run result lists."""
    result = []
    separator = "&" if "?" in path else "?"
    for page in range(1, 101):
      response = self.request(f"{path}{separator}per_page=100&page={page}")
      items = response if key is None else response[key]
      if not isinstance(items, list):
        raise ValueError("Unexpected paginated response")
      result.extend(items)
      if len(items) < 100:
        return result
    raise ValueError("API pagination limit reached; refusing partial results")


class CodeOwners:
  """Match the repository's explicit, deliberately limited ownership patterns.

  Supported patterns are the global default ``*``, root-anchored paths, trailing
  slash directories, and ``*`` or ``?`` within a path component. Other syntax
  fails closed so a future CODEOWNERS edit cannot silently broaden access.
  """

  def __init__(self, rules: List[Tuple[str, List[str]]]):
    """Keep ordered rules; the last matching rule takes precedence."""
    self.rules = rules

  def of(self, path: str) -> List[str]:
    """Resolve an exact case-sensitive path without matching wildcards across slashes."""
    result = []
    for pattern, owners in self.rules:
      if pattern == "*":
        result = owners
        continue
      directory = pattern.endswith("/")
      parts = pattern.strip("/").split("/")
      path_parts = path.split("/")
      # GitHub treats a final /* as immediate children; other matches include directories.
      if (directory and len(path_parts) <= len(parts)) or (
        not directory
        and (
          len(path_parts) < len(parts) or (pattern.endswith("/*") and len(path_parts) != len(parts))
        )
      ):
        continue
      if all(fnmatch.fnmatchcase(value, glob) for value, glob in zip(path_parts, parts)):
        result = owners
    return result


def parse_owners(text: str) -> CodeOwners:
  """Validate the supported CODEOWNERS subset and reject all unknown syntax."""
  rules = []
  for number, line in enumerate(text.splitlines(), 1):
    line = line.strip()
    if not line or line.startswith("#"):
      continue
    line = re.split(r"\s+#", line, maxsplit=1)[0]
    pattern, *owners = line.split()
    # Fail closed on unsupported syntax instead of silently skipping an ownership rule.
    if (
      (pattern != "*" and not pattern.startswith("/"))
      or "**" in pattern
      or any(char in pattern for char in "[]\\!")
      or any(part in ("", ".", "..") for part in pattern.strip("/").split("/"))
    ):
      raise ValueError(f"Unsupported CODEOWNERS pattern on line {number}")
    if any(OWNER_NAME.fullmatch(owner) is None for owner in owners):
      raise ValueError(f"Use @user or @organization/team owners on line {number}")
    rules.append((pattern, owners))
  if not rules:
    raise ValueError("CODEOWNERS is empty")
  return CodeOwners(rules)


def changed_paths(files: List[dict], expected_count: int) -> Set[str]:
  """Include both sides of renames and refuse GitHub's truncated file lists."""
  if len(files) != expected_count:
    raise ValueError("Incomplete changed-file list; a core maintainer must investigate")
  paths = set()
  for file in files:
    paths.add(file["filename"])
    if file["status"] == "renamed":
      paths.add(file["previous_filename"])
  if not paths:
    raise ValueError("No changed paths found")
  return paths


def review_states(reviews: List[dict]) -> Dict[str, dict]:
  """Keep each human's latest decisive review; comments don't erase decisions."""
  latest = {}
  for review in sorted(reviews, key=lambda item: item["id"]):
    if review["user"]["type"] != "User":
      continue
    if review["state"] in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
      latest[review["user"]["login"].lower()] = review
  return latest


class Access:
  """Resolve current repository write access and organization team membership."""

  def __init__(self, api: GitHub):
    """Cache access only for the duration of this evaluation."""
    self.api = api
    self.writers: Dict[str, bool] = {}
    self.teams: Dict[str, Set[str]] = {}

  def can_write(self, login: str) -> bool:
    """Require repository write access even for explicitly listed owners."""
    login = login.lower()
    if login not in self.writers:
      try:
        permission = self.api.request(
          f"/repos/{REPOSITORY}/collaborators/{quote(login, safe='')}/permission"
        )
        self.writers[login] = permission["permission"] in ("write", "maintain", "admin")
      except HTTPError as error:
        if error.code != 404:
          raise
        self.writers[login] = False
    return self.writers[login]

  def owns(self, login: str, owner: str) -> bool:
    """Match an individual or a current member of a team with repository write access."""
    login = login.lower()
    if not self.can_write(login):
      return False
    owner = owner.lower()
    if "/" not in owner:
      return owner == "@" + login
    if owner not in self.teams:
      organization, team = owner[1:].split("/")
      path = f"/orgs/{organization}/teams/{team}"
      permission = self.api.request(
        f"{path}/repos/{REPOSITORY}", accept="application/vnd.github.v3.repository+json"
      )
      if not permission["permissions"]["push"]:
        raise ValueError(f"Team {owner} does not have repository write access")
      self.teams[owner] = {member["login"].lower() for member in self.api.pages(f"{path}/members")}
    return login in self.teams[owner]


def evaluate(
  owners: CodeOwners,
  paths: Set[str],
  author: str,
  head_sha: str,
  author_confirmed: bool,
  reviews: List[dict],
  can_write: Callable[[str], bool],
  owns: Callable[[str, str], bool],
) -> Tuple[bool, str]:
  """Require owner authorization for every path, preserving core-team authority."""
  latest = review_states(reviews)
  blockers = [
    login
    for login, review in latest.items()
    if review["state"] == "CHANGES_REQUESTED" and can_write(login)
  ]
  if blockers:
    return False, "Changes requested by: " + ", ".join(sorted(blockers))
  authorized = {
    login
    for login, review in latest.items()
    if review["state"] == "APPROVED"
    and review["commit_id"] == head_sha
    and login != author.lower()
    and can_write(login)
  }
  if author_confirmed and can_write(author):
    authorized.add(author.lower())
  if any(owns(login, CORE_TEAM) for login in authorized):
    return True, "The current changes are authorized by a core-team member."
  missing = []
  for path in sorted(paths):
    file_owners = owners.of(path)
    if not file_owners or not any(
      owns(login, owner) for login in authorized for owner in file_owners
    ):
      missing.append(f"- {json.dumps(path)}: {', '.join(file_owners) or 'no owner assigned'}")
  if missing:
    explanation = "These paths need approval from an owner on the current commit:\n\n" + "\n".join(
      missing[:100]
    )
    if len(missing) > 100:
      explanation += f"\n- …and {len(missing) - 100} more paths."
    if not author_confirmed:
      explanation += (
        "\n\nSelf-authorization requires the PR author to open, reopen, mark ready, or push "
        "the current head after this integration was installed. A different person's push "
        "does not inherit the author's authorization."
      )
    return False, explanation
  return True, "Every changed path is authorized by its author-owner or a current owner approval."


class Checker:
  """Evaluate live PRs without checking out or executing their contents."""

  def __init__(self, api: GitHub, app_id: int, dry_run: bool = False):
    """Use one App identity and one target branch for all checks."""
    self.api = api
    self.app_id = app_id
    self.dry_run = dry_run
    self.access = Access(api)
    self.repo = f"/repos/{REPOSITORY}"

  def record_author_event(self, event_name: str, event: dict) -> None:
    """Persist an App-authenticated receipt for a real action by the PR author."""
    if self.dry_run or event_name != "pull_request_target":
      return
    if event.get("action") not in ("opened", "reopened", "synchronize", "ready_for_review"):
      return
    pr = event["pull_request"]
    if (
      pr["base"]["repo"]["full_name"].lower() != REPOSITORY.lower()
      or pr["base"]["ref"] != BASE_BRANCH
      or event["sender"]["type"] != "User"
      or event["sender"]["id"] != pr["user"]["id"]
    ):
      return
    self.api.request(
      f"{self.repo}/check-runs",
      "POST",
      {
        "name": f"ownership-author-{pr['number']}",
        "head_sha": pr["head"]["sha"],
        "external_id": f"author:{pr['user']['id']}:{BASE_BRANCH}",
        "status": "completed",
        "conclusion": "neutral",
        "output": {
          "title": "PR author confirmed this commit",
          "summary": "Authenticated PR event; ownership and access are checked separately.",
        },
      },
    )

  def author_confirmed(self, pr: dict) -> bool:
    """Trust only this App's receipt for this PR, author, branch, and exact head SHA."""
    query = urlencode({"check_name": f"ownership-author-{pr['number']}", "filter": "all"})
    checks = self.api.pages(
      f"{self.repo}/commits/{pr['head']['sha']}/check-runs?{query}", "check_runs"
    )
    return any(
      check["app"]["id"] == self.app_id
      and check["head_sha"] == pr["head"]["sha"]
      and check["external_id"] == f"author:{pr['user']['id']}:{BASE_BRANCH}"
      and check["conclusion"] == "neutral"
      for check in checks
    )

  def inspect(self, number: int, base_sha: str, owners: CodeOwners) -> Tuple[dict, bool, str]:
    """Fetch complete live data and refuse a moving PR snapshot."""
    pr = self.api.request(f"{self.repo}/pulls/{number}")
    if pr["state"] != "open" or pr["base"]["ref"] != BASE_BRANCH:
      return pr, False, "Pull request is no longer open against main."
    files = self.api.pages(f"{self.repo}/pulls/{number}/files")
    paths = changed_paths(files, pr["changed_files"])
    reviews = self.api.pages(f"{self.repo}/pulls/{number}/reviews")
    success, summary = evaluate(
      owners,
      paths,
      pr["user"]["login"],
      pr["head"]["sha"],
      self.author_confirmed(pr),
      reviews,
      self.access.can_write,
      self.access.owns,
    )
    current = self.api.request(f"{self.repo}/pulls/{number}")
    current_base = self.api.request(f"{self.repo}/git/ref/heads/{BASE_BRANCH}")["object"]["sha"]
    current_reviews = self.api.pages(f"{self.repo}/pulls/{number}/reviews")
    if (
      current["head"]["sha"] != pr["head"]["sha"]
      or current["base"]["ref"] != BASE_BRANCH
      or current["state"] != "open"
      or current_base != base_sha
      or review_states(current_reviews) != review_states(reviews)
    ):
      return (
        pr,
        False,
        "Pull request or reviews changed during evaluation; waiting for a fresh run.",
      )
    return pr, success, summary

  def run(self, event_name: str, event: dict, only_pr: Optional[int] = None) -> None:
    """Publish one aggregate check per SHA so PRs sharing a head cannot overwrite failures."""
    self.record_author_event(event_name, event)
    pulls = self.api.pages(f"{self.repo}/pulls?state=open&base={BASE_BRANCH}")
    if only_pr is not None:
      selected = [pr for pr in pulls if pr["number"] == only_pr]
      if not selected:
        raise ValueError(f"PR #{only_pr} is not open against {BASE_BRANCH}")
      heads = {pr["head"]["sha"] for pr in selected}
      pulls = [pr for pr in pulls if pr["head"]["sha"] in heads]
    groups = defaultdict(list)
    for pr in pulls:
      groups[pr["head"]["sha"]].append(pr["number"])
    base_sha = self.api.request(f"{self.repo}/git/ref/heads/{BASE_BRANCH}")["object"]["sha"]
    content = self.api.request(f"{self.repo}/contents/.github/CODEOWNERS?ref={base_sha}")
    if content["encoding"] != "base64":
      raise ValueError("Unexpected CODEOWNERS encoding")
    owners = parse_owners(base64.b64decode(content["content"]).decode())
    for head, numbers in groups.items():
      check = None
      if not self.dry_run:
        check = self.api.request(
          f"{self.repo}/check-runs",
          "POST",
          {"name": CHECK_NAME, "head_sha": head, "status": "in_progress"},
        )
      success = True
      summaries = []
      try:
        for number in numbers:
          pr, passed, summary = self.inspect(number, base_sha, owners)
          passed = passed and pr["head"]["sha"] == head
          success = success and passed
          summaries.append(f"### PR #{number}\n\n{summary}")
      except Exception as error:
        success = False
        summaries.append(f"Evaluation failed ({type(error).__name__}); no authorization granted.")
        # Do not print API payloads, which could contain sensitive request information.
        print(f"Evaluation error for {numbers}: {type(error).__name__}")
      summary = "\n\n".join(summaries)[:60000]
      print(json.dumps({"prs": numbers, "head": head, "success": success, "summary": summary}))
      if check is not None:
        self.api.request(
          f"{self.repo}/check-runs/{check['id']}",
          "PATCH",
          {
            "status": "completed",
            "conclusion": "success" if success else "failure",
            "output": {
              "title": "Ownership authorized" if success else "Owner authorization required",
              "summary": summary,
            },
          },
        )


def main() -> None:
  """Run with an App token, or use read-only dry-run mode during setup."""
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--dry-run", action="store_true")
  parser.add_argument("--pr", type=int)
  args = parser.parse_args()
  event_path = os.environ.get("GITHUB_EVENT_PATH")
  event = json.loads(Path(event_path).read_text()) if event_path else {}
  checker = Checker(
    GitHub(os.environ["GH_TOKEN"]), int(os.environ["OWNERSHIP_APP_ID"]), args.dry_run
  )
  checker.run(os.environ.get("GITHUB_EVENT_NAME", ""), event, args.pr)


if __name__ == "__main__":
  main()
