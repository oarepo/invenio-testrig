# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Git reference parsing utilities.

Provides parsers for converting string representations of git references
into GitReference structures, supporting multiple formats including GitHub
URLs, custom DSL syntax with org/repo@branch notation, pull request references,
and version constraints.
"""

from typing import Any, cast
from urllib.parse import parse_qs, urlparse

from lark import Lark, Token, Transformer
from lark import exceptions as lark_exceptions

from invenio_testrig.types import GitReference, VersionConstraint


def parse_string_reference(reference: str) -> GitReference:
    """Parse a string reference into a GitReference.

    Tries GitHub URL parsing first, then falls back to custom grammar parsing.
    Note: This does NOT parse version constraints. Use parse_version_constraints separately.

    :param reference: String representation of a git reference (without version constraints)

    :return: Parsed GitReference structure

    :raises ValidationError: If the reference format is invalid
    """
    # Try to parse as GitHub URL first
    github_ref = _parse_github_url(reference)
    if github_ref is not None:
        return github_ref

    # Fall back to Lark parser for custom format
    try:
        tree = _parser.parse(reference)
        result = cast(GitReference, _transformer.transform(tree))
        return result
    except lark_exceptions.LarkError as e:
        from marshmallow import ValidationError

        raise ValidationError(
            f"Invalid git reference format: '{reference}'. Expected formats: "
            "org/package@branch, org/package#pr_number, "
            "org/package@branch[base], package_name: org/package..., or a GitHub URL "
            "(https://github.com/org/repo, https://github.com/org/repo/tree/branch, "
            "https://github.com/org/repo/pull/123)"
        ) from e


def parse_version_constraints(constraints_str: str) -> list[VersionConstraint]:
    """Parse a version constraints string into a list of VersionConstraint objects.

    :param constraints_str: String representation of version constraints (e.g., '<123,>=234')

    :return: List of VersionConstraint objects

    :raises ValidationError: If the constraints format is invalid

    Example::

        >>> parse_version_constraints('<1.0.0')
        [VersionConstraint(operator='<', version='1.0.0')]
        >>> parse_version_constraints('>=1.0.0,<2.0.0')
        [VersionConstraint(operator='>=', version='1.0.0'), VersionConstraint(operator='<', version='2.0.0')]
    """
    try:
        tree = _version_parser.parse(constraints_str)
        result = cast(list[VersionConstraint], _version_transformer.transform(tree))
        return result
    except lark_exceptions.LarkError as e:
        from marshmallow import ValidationError

        raise ValidationError(
            f"Invalid version constraints format: '{constraints_str}'. "
            "Expected format: OPERATOR VERSION (e.g., '>=1.0.0' or '>=1.0.0,<2.0.0')"
        ) from e


GIT_REFERENCE_GRAMMAR = r"""
    git_reference: [package_prefix] repo_ref [base_bracket]

    package_prefix: NAME ":" WS*

    repo_ref: NAME "/" NAME (branch_ref | pr_ref)?

    branch_ref: "@" BRANCH_NAME
    pr_ref: "#" NUMBER

    base_bracket: "[" NAME "]"

    NAME: /[\w\-\.]+/
    BRANCH_NAME: /[\w\-\.\/]+/
    NUMBER: /\d+/

    WS: /\s+/

    %ignore WS
"""

VERSION_CONSTRAINTS_GRAMMAR = r"""
    version_constraints: version_constraint (WS* "," WS* version_constraint)*

    version_constraint: OPERATOR VERSION

    OPERATOR: ">=" | "<=" | ">" | "<" | "==" | "!="
    VERSION: /\d+(\.\d+)*((a|alpha|b|beta|rc|c)(\.?\d+))?(\.post\d+)?(\.dev\d+)?(\+[\w\.]+)?/

    WS: /\s+/

    %ignore WS
"""


class GitReferenceTransformer(Transformer):
    """Transform parsed git reference tree into a GitReference object.

    Takes the parse tree from Lark parser and constructs a GitReference
    dataclass with all the parsed components.
    """

    def git_reference(self, items: list[Any]) -> GitReference:
        """Transform git_reference rule into GitReference object.

        :param items: List of parsed items from the git_reference rule

        :return: GitReference object with all parsed components
        """
        result = GitReference(
            org="",
            repo="",
            branch=None,
            pr=None,
            package="",
            base=None,
            pr_info=None,
            commit=None,
        )

        for item in items:
            if isinstance(item, dict):
                for k, v in item.items():
                    setattr(result, k, v)

        if not result.package:
            result.package = result.repo.lower()

        return result

    def package_prefix(self, items: list[str]) -> dict[str, str]:
        """Transform package_prefix rule into package name.

        :param items: List containing the package name

        :return: Dictionary with 'package' key and lowercased package name
        """
        return {"package": str(items[0]).lower()}

    def repo_ref(self, items: list[Any]) -> dict[str, str | None]:
        """Transform repo_ref rule into org/repo dict.

        :param items: List containing org, repo, and optionally branch/PR info

        :return: Dictionary with org, repo, and optionally branch or pr keys
        """
        result: dict[str, str | None] = {"org": str(items[0]), "repo": str(items[1])}
        if len(items) > 2 and isinstance(items[2], dict):
            result.update(items[2])
        return result

    def branch_ref(self, items: list[str]) -> dict[str, str]:
        """Transform branch_ref rule into branch name.

        :param items: List containing the branch name

        :return: Dictionary with 'branch' key and branch name
        """
        return {"branch": str(items[0])}

    def pr_ref(self, items: list[str]) -> dict[str, int]:
        """Transform pr_ref rule into PR number.

        :param items: List containing the PR number as a string

        :return: Dictionary with 'pr' key and PR number as integer
        """
        return {"pr": int(items[0])}

    def base_bracket(self, items: list[str]) -> dict[str, str]:
        """Transform base_bracket rule into base branch name.

        :param items: List containing the base branch name

        :return: Dictionary with 'base' key and base branch name
        """
        return {"base": str(items[0])}

    def NAME(self, token: Token) -> str:
        """Extract NAME token value.

        :param token: Lark Token containing the NAME value

        :return: The token value as a string
        """
        return token.value

    def BRANCH_NAME(self, token: Token) -> str:
        """Extract BRANCH_NAME token value.

        :param token: Lark Token containing the BRANCH_NAME value

        :return: The token value as a string
        """
        return token.value

    def NUMBER(self, token: Token) -> str:
        """Extract NUMBER token value.

        :param token: Lark Token containing the NUMBER value

        :return: The token value as a string
        """
        return token.value


class VersionConstraintsTransformer(Transformer):
    """Transform parsed version constraints tree into a list of VersionConstraint objects.

    Processes the parse tree from Lark parser and constructs VersionConstraint
    dataclasses for each operator-version pair found in the input.
    """

    def version_constraints(
        self, items: list[VersionConstraint]
    ) -> list[VersionConstraint]:
        """Transform version_constraints rule into list of version constraints.

        :param items: List of VersionConstraint objects from parsed constraints

        :return: List of VersionConstraint objects
        """
        return items

    def version_constraint(self, items: list[str]) -> VersionConstraint:
        """Transform version_constraint rule into VersionConstraint object.

        :param items: List containing operator and version strings

        :return: VersionConstraint object with the parsed operator and version
        """
        return VersionConstraint(operator=str(items[0]), version=str(items[1]))

    def OPERATOR(self, token: Token) -> str:
        """Extract OPERATOR token value.

        :param token: Lark Token containing the OPERATOR value

        :return: The operator string (e.g., '>=', '<=', '==', '!=', '>', '<')
        """
        return token.value

    def VERSION(self, token: Token) -> str:
        """Extract VERSION token value.

        :param token: Lark Token containing the VERSION value

        :return: The version string (e.g., '1.2.3', '2.0.0a1')
        """
        return token.value


def _extract_branch_from_query(query: str) -> str | None:
    """Return branch/rev from URL query parameters, or None if absent."""
    if not query:
        return None
    query_dict = parse_qs(query)
    if "branch" in query_dict:
        return query_dict["branch"][0]
    if "rev" in query_dict:
        return query_dict["rev"][0]
    return None


def _parse_github_url(url: str) -> GitReference | None:
    """Parse a GitHub URL into a GitReference structure.

    Supports:
    - https://github.com/org/repo
    - https://github.com/org/repo/tree/branch-name
    - https://github.com/org/repo/pull/123

    Also pip-installed github references:
    - https://github.com/inveniosoftware/invenio-records-resources?branch=fix-read-many#c6b973a14802e2a7f73100ab4e32cb0c36bd4672
    - https://github.com/inveniosoftware/invenio-swh?rev=v0.13.4#828a3a415cf8e725c369939832b61281c44aec40
    For these two cases, fragments (sha commit) are not parsed because pip can use their obsolete version.

    :param url: String that may be a GitHub URL

    :return: GitReference object if the URL is a valid GitHub URL, None otherwise
    """
    if not url.startswith("https://"):
        return None

    parsed = urlparse(url)

    # Check if it's a GitHub URL
    if parsed.netloc != "github.com":
        return None

    # Parse the path: /org/repo[/tree/branch | /pull/number]
    path_segments = parsed.path.strip("/").split("/")
    if len(path_segments) < 2:
        return None
    org, repo, *parts = path_segments
    if repo.endswith(".git"):
        repo = repo[:-4]

    branch: str | None = None
    pr: int | None = None

    if parts:
        if parts[0] in ("tree", "heads"):
            branch = parts[1]
        elif parts[0] == "pull":
            try:
                pr = int(parts[1])
            except ValueError:
                return None

    # Handle pip-installed github references with query parameters
    if branch is None:
        branch = _extract_branch_from_query(parsed.query)

    # Create GitReference structure
    result = GitReference(
        org=org,
        repo=repo,
        package=repo.lower(),
        branch=branch,
        pr=pr,
        base=None,
        pr_info=None,
        commit=None,
    )

    return result


# Initialize parsers and transformers once at module level for efficiency
_parser: Lark = Lark(GIT_REFERENCE_GRAMMAR, start="git_reference", parser="lalr")
_transformer: GitReferenceTransformer = GitReferenceTransformer()

_version_parser: Lark = Lark(
    VERSION_CONSTRAINTS_GRAMMAR, start="version_constraints", parser="lalr"
)
_version_transformer: VersionConstraintsTransformer = VersionConstraintsTransformer()
