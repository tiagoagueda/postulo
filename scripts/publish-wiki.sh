#!/usr/bin/env bash
# Publish wiki/ to the Forgejo wiki repository.
#
# The pages are authored in this repository so that they are reviewed and versioned
# alongside the code they describe. Forgejo keeps its wiki in a separate git repository,
# so publishing is a copy and a push.
#
# The wiki repository only exists once the wiki has been enabled for the project:
# Settings -> Repository -> Wiki, then create any page in the web interface once.
set -euo pipefail

REMOTE="${POSTULO_WIKI_REMOTE:-https://source.tiagoagueda.com/tiagoagueda/postulo.wiki.git}"
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/wiki"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

echo "Cloning $REMOTE"
if ! git clone --quiet "$REMOTE" "$WORKDIR/wiki" 2>/dev/null; then
    echo "error: could not clone the wiki repository." >&2
    echo "Enable the wiki for the project and create one page, then try again." >&2
    exit 1
fi

# Remove pages that no longer exist here, then copy the current set over.
# README.md explains this directory to contributors and is not a wiki page.
find "$WORKDIR/wiki" -maxdepth 1 -name '*.md' -delete
for page in "$SOURCE"/*.md; do
    [ "$(basename "$page")" = "README.md" ] && continue
    cp "$page" "$WORKDIR/wiki/"
done

cd "$WORKDIR/wiki"
if git diff --quiet && git diff --cached --quiet && [ -z "$(git status --porcelain)" ]; then
    echo "The wiki is already up to date."
    exit 0
fi

git add -A
git commit --quiet -m "Update wiki from postulo@$(git -C "$SOURCE/.." rev-parse --short HEAD)"
git push --quiet
echo "Published $(ls -1 "$WORKDIR/wiki"/*.md | wc -l) pages."
