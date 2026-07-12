#!/usr/bin/env bash
# Publish minimum runtime plugin files from main HEAD to the publish branch.
# The plugin tree (src/memo/*) is lifted to the branch root so a clone of
# the publish branch IS a loadable hermes plugin directory.
# Requires Git >= 2.15 (git worktree add --orphan).
#
# Usage:
#   ./scripts/publish-release.sh [--tag <version-tag>] [--message <commit-message>]
set -euo pipefail

# Runtime only — no docs/, tests/, scripts/, deploy/, or local config.
# src/memo/* lands at the branch root; the root extras ride alongside.
PLUGIN_TREE="src/memo"
ROOT_PATHS=(
    pyproject.toml
    README.md
)

RELEASE_BRANCH="publish"
SOURCE_REF="main"
VERSION=""
COMMIT_MESSAGE=""

usage() {
    sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
}

require_value() {
    local option="$1"; local value="${2:-}"
    [[ -n "$value" ]] || { echo "Error: $option requires a value." >&2; exit 1; }
}

while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --tag) require_value "$1" "${2:-}"; VERSION="$2"; shift 2 ;;
        --tag=*) VERSION="${1#--tag=}"; require_value "--tag" "$VERSION"; shift ;;
        --message|-m) require_value "$1" "${2:-}"; COMMIT_MESSAGE="$2"; shift 2 ;;
        --message=*|-m=*) COMMIT_MESSAGE="${1#*=}"; shift ;;
        --help|-h) usage; exit 0 ;;
        *) echo "Error: unknown argument: $1" >&2; usage >&2; exit 1 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
TMP_WORKTREE="$(mktemp -d)"

cleanup() {
    git -C "$REPO_ROOT" worktree remove --force "$TMP_WORKTREE" 2>/dev/null || true
    rm -rf "$TMP_WORKTREE"
}
trap cleanup EXIT

SOURCE_SHORT="$(git -C "$REPO_ROOT" rev-parse --short "$SOURCE_REF" 2>/dev/null)" \
    || { echo "Error: branch '$SOURCE_REF' not found." >&2; exit 1; }

echo "Source : $SOURCE_REF ($SOURCE_SHORT)"
echo "Target : $RELEASE_BRANCH"
[[ -n "$VERSION" ]] && echo "Tag    : $VERSION"
echo ""

if git -C "$REPO_ROOT" show-ref --quiet "refs/heads/$RELEASE_BRANCH"; then
    git -C "$REPO_ROOT" worktree add -q "$TMP_WORKTREE" "$RELEASE_BRANCH"
    git -C "$TMP_WORKTREE" rm -rf --quiet . 2>/dev/null || true
else
    git -C "$REPO_ROOT" worktree add -q --orphan -b "$RELEASE_BRANCH" "$TMP_WORKTREE"
fi

# Plugin tree lifted to the root (strip src/memo/).
git -C "$REPO_ROOT" cat-file -e "${SOURCE_REF}:${PLUGIN_TREE}" 2>/dev/null \
    || { echo "Error: ${PLUGIN_TREE} not found in ${SOURCE_REF}." >&2; exit 1; }
git -C "$REPO_ROOT" archive "$SOURCE_REF" "$PLUGIN_TREE" | tar -x -C "$TMP_WORKTREE" --strip-components=2
echo "  + ${PLUGIN_TREE}/* -> /"

for path in "${ROOT_PATHS[@]}"; do
    if git -C "$REPO_ROOT" cat-file -e "${SOURCE_REF}:${path}" 2>/dev/null; then
        git -C "$REPO_ROOT" archive "$SOURCE_REF" "$path" | tar -x -C "$TMP_WORKTREE"
        echo "  + $path"
    else
        echo "  - $path  (not in $SOURCE_REF, skipped)"
    fi
done

echo ""
git -C "$TMP_WORKTREE" add -A

if git -C "$TMP_WORKTREE" diff --cached --quiet 2>/dev/null; then
    RELEASE_COMMIT="$(git -C "$TMP_WORKTREE" rev-parse HEAD)"
    echo "No changes — publish branch is already up to date."
    echo "HEAD : $RELEASE_COMMIT"
else
    COMMIT_MSG="${COMMIT_MESSAGE:-}"
    if [[ -z "$COMMIT_MSG" ]]; then
        if [[ -n "$VERSION" ]]; then
            COMMIT_MSG="publish: release $VERSION runtime plugin"
        else
            COMMIT_MSG="publish: release runtime plugin"
        fi
    fi
    git -C "$TMP_WORKTREE" commit -q -m "$COMMIT_MSG"
    RELEASE_COMMIT="$(git -C "$TMP_WORKTREE" rev-parse HEAD)"
    echo "Committed  : $RELEASE_COMMIT"
fi

if [[ -n "$VERSION" ]]; then
    git -C "$REPO_ROOT" tag -d "$VERSION" 2>/dev/null && echo "Removed existing tag '$VERSION'." || true
    git -C "$REPO_ROOT" tag -a "$VERSION" "$RELEASE_COMMIT" -m "Release $VERSION"
    echo "Tagged     : $VERSION → $RELEASE_COMMIT"
fi

echo ""
echo "Push with:"
echo "  git push origin $RELEASE_BRANCH"
[[ -n "$VERSION" ]] && echo "  git push origin $VERSION"
