# Writing a plugin

This guide has moved to the wiki: **[Writing a plugin](https://source.tiagoagueda.com/postulo/postulo/wiki/Writing-a-plugin)**.

The wiki is the one place Postulo's documentation lives (#169), and the plugin guide sits
there now, beside a reference of every name `postulo.plugins.api` offers and every kind of
plugin with the entry-point group it registers in (#170).

**Since #896 a company, a contact or a listing may be a group's, or shared.** A plugin reads
them with `visible_to(user)` (what the person may see), changes them only within
`visible_to(user, write=True)`, deletes or shares them only within `manageable_by(user)`, and
matches by name within `owned_by(principal)`. `for_user` is kept, as `visible_to`, for one
release. A company's and a contact's `notes`, and a listing's `state`, `discard_reason`,
`noted_at` and `decided_at`, are each person's: read them with `with_notes(user)` /
`with_status(user)` or `note_for(user)` / `status_for(user)`, and write them with
`write_note(user, text)` or `shortlist(user)` / `discard(user, reason)` / `restore(user)`.
Reading one for nobody raises `NotReadFor`. The wiki's guide says more.

**A calendar provider is a `sync` plugin, and needs no new kind (#1254).** CalDAV with an app
password is the provider that needs least, and
[postulo-dav](https://source.tiagoagueda.com/postulo/postulo-dav) is its reference plugin. A
provider that signs in with OAuth (Google, Microsoft) declares `needs_consent`, and Postulo
conducts the round trip with a client the person or the operator registered with that provider,
read from the connection's `client_id` setting and `client_secret` secret: Postulo ships no
client, so the project needs no provider's review. The wiki compares the providers, what works
today, what each needs and what it risks, on
[Calendars and other services](https://source.tiagoagueda.com/postulo/postulo/wiki/Calendars-and-other-services),
and the guide's *Syncs* section says what a calendar sync keeps to.

This file stays so that the links to it still lead somewhere: every plugin's
`pyproject.toml` points here, and so does the metadata of every plugin already published.
