"""Every service Postulo ships, and what an address on each one looks like.

Kept short and conventional on purpose. A service earns a line by being somewhere a
recruiter is sent, not by existing; everything else is *Other*, which refuses nothing.

**Each pattern reads the path only**, percent-decoded, and is matched whole. The host is
checked apart from it -- a subdomain of a listed host counts, so ``www.``, ``pt.`` and
``m.`` need nothing said -- and the query string and the fragment are never read. So every
service here accepts a trailing slash, a tracking parameter and an accented name however
the browser copied it.

**Permissive at the end, strict at the start.** What is checked is that the address begins
the way a profile, a page or a project does there. What follows -- a tab, a branch, a
language suffix -- is let through, because the cost of refusing a real address is somebody
filing it under *Other* for no reason they can see, and the cost of accepting an odd one
is nothing at all.

**Websites have no services.** A website is its own thing; its block offers *Other* alone.

**Nothing here is looked up anywhere.** The icon is a generic one out of the Lucide set, and
is what a select draws. A service may also name a ``brand`` mark (#654), drawn where a link
is shown, and only where the owner's published colour clears 3:1 on both pages: Mastodon,
Bluesky, Facebook, Instagram, YouTube and Codeberg. The others keep the icon alone, and
LinkedIn has no mark at all (``assets/brands.txt``, ``TRADEMARKS.md``).
"""

from __future__ import annotations

import re

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import LinkKind, LinkService

# ------------------------------------------------------------------------- the keys
#
# What a row stores. A key that moved would be a data migration, so these do not move.

LINKEDIN = "linkedin"
MASTODON = "mastodon"
BLUESKY = "bluesky"
X = "x"
XING = "xing"
FACEBOOK = "facebook"
INSTAGRAM = "instagram"
THREADS = "threads"
YOUTUBE = "youtube"
GITHUB = "github"
GITLAB = "gitlab"
CODEBERG = "codeberg"
FORGEJO = "forgejo"
BITBUCKET = "bitbucket"
SOURCEHUT = "sourcehut"

# ----------------------------------------------------------------- how a path is read

#: One part of a path: anything but a slash or a space, in any script.
PART = r"[^/\s]+"

#: Whatever follows the part that says who or what: a tab, a branch, a language.
REST = r"(?:/.*)?"


# ----------------------------------------------------------------- the handle rules
#
# Where a service publishes what a name there may be, the pattern says it, and a name that
# breaks it is refused with the sentence that says what an address there looks like; the
# way out is *Other*. Where none is published, as for LinkedIn and Xing, there is no rule
# here, because a guessed one refuses somebody's real name (#638). Nothing is looked up.

#: Bluesky: a handle is a domain name -- dot-separated labels of letters, digits and
#: hyphens, at least two -- or a DID. Written in the AT Protocol's handle specification,
#: https://atproto.com/specs/handle, and in the DID's, https://atproto.com/specs/did.
BLUESKY_HANDLE = r"(?:did:[a-z]+:[A-Za-z0-9._:%-]+|[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)"

#: Instagram, and Threads which takes its names from it: one to thirty letters, digits,
#: full stops and underscores. Written in Instagram's help, "Changing your username".
INSTAGRAM_NAME = r"[A-Za-z0-9._]{1,30}"

#: YouTube: a handle is three to thirty letters, digits, underscores, hyphens and full
#: stops. Written in YouTube's help, "Handles on YouTube". X's own rule, one to fifteen
#: letters, digits and underscores, is in its pattern below, from X's help, "Change your
#: username".
YOUTUBE_HANDLE = r"[\w.-]{3,30}"


def _not(*words: str) -> str:
    """Refuse a first part that is one of the service's own pages rather than a name."""
    return r"(?!(?:" + "|".join(re.escape(word) for word in words) + r")(?:/|$))"


def _path(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


# What a forge keeps for itself at the top of its paths. Each list is the service's own
# pages a person is most likely to have open when they copy an address by mistake, and no
# attempt at every reserved word: a miss here files a settings page as a repository, which
# its owner sees at once.
_GITHUB_PAGES = (
    "about",
    "codespaces",
    "collections",
    "contact",
    "enterprise",
    "explore",
    "features",
    "issues",
    "join",
    "login",
    "marketplace",
    "new",
    "notifications",
    "pricing",
    "pulls",
    "search",
    "security",
    "settings",
    "site",
    "topics",
    "trending",
)
_FORGEJO_PAGES = (
    "admin",
    "api",
    "assets",
    "avatars",
    "explore",
    "issues",
    "milestones",
    "notifications",
    "pulls",
    "repo",
    "user",
)

# --------------------------------------------------------------------------- the table

SERVICES: dict[str, LinkService] = {
    service.key: service
    for service in (
        # ------------------------------------------------------------ social profiles
        LinkService(
            LINKEDIN,
            "LinkedIn",
            LinkKind.SOCIAL,
            # A profile, an older public profile, a company page, a school, a showcase.
            _path(rf"/(?:in|pub|company|school|showcase)/(?P<handle>{PART}){REST}"),
            hosts=("linkedin.com",),
            icon="user",
            example="https://www.linkedin.com/in/name",
            # A person's own profile; a company, a school and a showcase page stay a
            # stored address, or *Other*. No handle rule: LinkedIn's custom address takes
            # accented letters (#638) and a rule here would refuse a real one.
            prefix="https://www.linkedin.com/in/",
        ),
        LinkService(
            MASTODON,
            "Mastodon",
            LinkKind.SOCIAL,
            # @name, @name@another.server as one server shows somebody on another, the
            # older /web/ prefix, and the /users/name the protocol itself uses.
            _path(rf"/(?:web/)?(?P<handle>@[^/\s@]+(?:@[^/\s@]+)?|users/{PART}){REST}"),
            # Recognised without being asked on the two servers its makers run; any other
            # server is chosen, because a shape alone says too little to guess from.
            hosts=("mastodon.social", "mastodon.online"),
            any_host=True,
            icon="at-sign",
            brand="mastodon",
            example="https://mastodon.social/@name",
        ),
        LinkService(
            BLUESKY,
            "Bluesky",
            LinkKind.SOCIAL,
            # The handle is a domain name, or a DID with colons in it.
            _path(rf"/profile/(?P<handle>{BLUESKY_HANDLE}){REST}"),
            hosts=("bsky.app",),
            icon="at-sign",
            brand="bluesky",
            example="https://bsky.app/profile/name.bsky.social",
            prefix="https://bsky.app/profile/",
        ),
        LinkService(
            X,
            "X",
            LinkKind.SOCIAL,
            _path(
                r"/"
                + _not(
                    "compose",
                    "explore",
                    "hashtag",
                    "home",
                    "i",
                    "intent",
                    "login",
                    "messages",
                    "notifications",
                    "privacy",
                    "search",
                    "settings",
                    "share",
                    "signup",
                    "tos",
                )
                + rf"@?(?P<handle>[A-Za-z0-9_]{{1,15}}){REST}"
            ),
            hosts=("x.com", "twitter.com"),
            icon="at-sign",
            example="https://x.com/name",
            prefix="https://x.com/",
        ),
        LinkService(
            XING,
            "Xing",
            LinkKind.SOCIAL,
            # A person, and a company under each of the three names its pages have had.
            _path(rf"/(?:profile|pages|companies|company)/(?P<handle>{PART}){REST}"),
            hosts=("xing.com",),
            icon="user",
            example="https://www.xing.com/profile/Name_Surname",
            prefix="https://www.xing.com/profile/",
        ),
        LinkService(
            FACEBOOK,
            "Facebook",
            LinkKind.SOCIAL,
            # A name, /profile.php with its id in the query, /people/Name/id, a page, a
            # group: nearly anything, so what is refused is the site's own pages.
            _path(
                r"/(?P<handle>"
                + _not(
                    "dialog",
                    "events",
                    "gaming",
                    "hashtag",
                    "help",
                    "login",
                    "marketplace",
                    "photo",
                    "plugins",
                    "policies",
                    "privacy",
                    "reel",
                    "search",
                    "settings",
                    "share",
                    "sharer",
                    "stories",
                    "watch",
                )
                + rf"{PART}(?:/{PART})*)/?"
            ),
            hosts=("facebook.com", "fb.com"),
            icon="user",
            brand="facebook",
            example="https://www.facebook.com/name",
            prefix="https://www.facebook.com/",
        ),
        LinkService(
            INSTAGRAM,
            "Instagram",
            LinkKind.SOCIAL,
            _path(
                r"/"
                + _not(
                    "about",
                    "accounts",
                    "challenge",
                    "developer",
                    "direct",
                    "explore",
                    "legal",
                    "p",
                    "reel",
                    "reels",
                    "stories",
                    "tv",
                )
                + rf"(?P<handle>{INSTAGRAM_NAME}){REST}"
            ),
            hosts=("instagram.com",),
            icon="user",
            brand="instagram",
            example="https://www.instagram.com/name",
            prefix="https://www.instagram.com/",
        ),
        LinkService(
            THREADS,
            "Threads",
            LinkKind.SOCIAL,
            _path(rf"/(?P<handle>@{INSTAGRAM_NAME}){REST}"),
            hosts=("threads.com", "threads.net"),
            icon="at-sign",
            example="https://www.threads.com/@name",
            prefix="https://www.threads.com/@",
        ),
        LinkService(
            YOUTUBE,
            "YouTube",
            LinkKind.SOCIAL,
            # A channel under each of its four addresses; a single video is not a profile.
            _path(rf"/(?P<handle>@{YOUTUBE_HANDLE}|(?:channel|c|user)/{PART}){REST}"),
            hosts=("youtube.com",),
            icon="video",
            brand="youtube",
            example="https://www.youtube.com/@name",
            prefix="https://www.youtube.com/@",
        ),
        # ---------------------------------------------------------- code repositories
        LinkService(
            GITHUB,
            "GitHub",
            LinkKind.REPOSITORY,
            # A person or an organisation, a project of theirs, /orgs/name; and a gist,
            # whose host is a subdomain.
            _path(rf"/{_not(*_GITHUB_PAGES)}(?P<handle>{PART}(?:/{PART})?){REST}"),
            hosts=("github.com",),
            icon="git-branch",
            brand="github",
            example="https://github.com/name/project",
        ),
        LinkService(
            GITLAB,
            "GitLab",
            LinkKind.REPOSITORY,
            # Groups nest to any depth, and the project's own pages sit behind "/-/".
            _path(
                r"/"
                + _not("-", "admin", "dashboard", "explore", "help", "search", "users")
                + rf"(?P<handle>{PART}(?:/(?!-(?:/|$)){PART})*)(?:/-{REST})?/?"
            ),
            hosts=("gitlab.com",),
            # Also one somebody runs themselves: chosen, and accepted on its shape.
            any_host=True,
            icon="git-branch",
            example="https://gitlab.com/name/project",
        ),
        LinkService(
            CODEBERG,
            "Codeberg",
            LinkKind.REPOSITORY,
            _path(rf"/{_not(*_FORGEJO_PAGES)}(?P<handle>{PART}(?:/{PART})?){REST}"),
            hosts=("codeberg.org",),
            icon="git-branch",
            brand="codeberg",
            example="https://codeberg.org/name/project",
        ),
        LinkService(
            FORGEJO,
            # One entry for both: Forgejo began as Gitea, and their addresses are the same.
            _("Forgejo or Gitea"),
            LinkKind.REPOSITORY,
            # An owner, or an owner and a project, on whatever host somebody runs it.
            _path(rf"/{_not(*_FORGEJO_PAGES)}(?P<handle>{PART}(?:/{PART})?){REST}"),
            hosts=("gitea.com",),
            any_host=True,
            icon="git-branch",
            brand="forgejo",
            example="https://git.example.org/name/project",
        ),
        LinkService(
            BITBUCKET,
            "Bitbucket",
            LinkKind.REPOSITORY,
            _path(
                r"/"
                + _not("account", "blog", "dashboard", "product", "site", "support")
                + rf"(?P<handle>{PART}(?:/{PART})?){REST}"
            ),
            hosts=("bitbucket.org",),
            icon="git-branch",
            example="https://bitbucket.org/name/project",
        ),
        LinkService(
            SOURCEHUT,
            "SourceHut",
            LinkKind.REPOSITORY,
            # ~name on sr.ht itself, ~name/project on git.sr.ht and hg.sr.ht.
            _path(rf"/(?P<handle>~{PART}(?:/{PART})?){REST}"),
            hosts=("sr.ht",),
            icon="git-branch",
            brand="sourcehut",
            example="https://git.sr.ht/~name/project",
        ),
    )
}
