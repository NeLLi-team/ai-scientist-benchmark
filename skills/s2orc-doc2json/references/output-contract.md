# Output Contract

The normal output set for one PDF is:

- `<stem>.s2orc.json`
- `<stem>.tei.xml`
- `<stem>.md`

`<stem>.s2orc.json` is authoritative.
`<stem>.md` is a readable projection.

The Markdown should usually have this shape:

1. `# <title>`
2. optional author line
3. optional year line
4. `## Abstract`
5. body sections in reading order
6. optional `## Figures And Tables`
7. optional `## References`

Keep these rules:

- do not invent content that is missing from the parse
- do not summarize the paper instead of converting it
- do not hide obvious parser damage; report it
- preserve section order unless the parse is clearly broken
- keep bibliography text when available, even if imperfect

For benchmark-case work in this repo, the usual cleaned Markdown destination is:

- `ground_truth/<case>/data/<case>.md`

but the raw conversion can be staged anywhere first.
