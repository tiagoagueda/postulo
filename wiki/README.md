# Wiki sources

These are the pages of the [Postulo wiki](https://source.tiagoagueda.com/postulo/postulo/wiki).

They are kept here, rather than edited in the wiki interface, so that documentation
changes are reviewed alongside the code that made them necessary.

Publish with:

```sh
./scripts/publish-wiki.sh
```

The wiki repository exists only after the wiki has been enabled for the project
(Settings → Repository → Wiki) and one page created through the web interface.

File names become page names: `Installing-Postulo.md` is the page *Installing Postulo*.
`_Sidebar.md` is the navigation shown beside every page. Links between pages use the page
name without the extension, as in `[Configuration](Configuration)`.

**Keep it honest.** The wiki states plainly what is not built yet. A page describing a
feature that does not exist is a bug.
