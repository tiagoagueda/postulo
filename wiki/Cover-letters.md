# Cover letters

**Documents → Cover letters**

Write one good letter and let placeholders do the repetitive part.

## Placeholders

Five, and only five:

| Placeholder | Filled with |
| --- | --- |
| `{{ company }}` | The company you are applying to |
| `{{ role }}` | The job title |
| `{{ location }}` | Where the role is based |
| `{{ name }}` | Your own name |
| `{{ date }}` | Today's date |

Spacing inside the braces does not matter: `{{company}}` and `{{ company }}` are the
same.

The first three come from the application the letter is being sent with. Preview a letter
on its own and they will be empty; preview it against an application and you will see the
finished text.

**A misspelt placeholder is left visible** rather than silently blanked, so `{{ compnay }}`
appears as-is in the draft. That is deliberate: quietly deleting a word would be worse
than showing you the typo.

## Why only five

A cover letter is text you wrote, and it usually contains fragments pasted from a job
advert. Postulo substitutes placeholders with a simple pattern match over that fixed list
of names — it does not run your letter through a template engine. A general-purpose
expression language inside a document that carries employer-supplied text would be a
liability rather than a feature.

If you paste something containing template syntax, it comes out as exactly the text you
pasted.

## Templates

Letters marked **reusable template** are offered when you record what you sent with an
application. Untick it for a one-off letter written for a single employer.

## Themes

The same two as CVs — Plain and Classic — laid out as a letter: your contact block, the
recipient, the date, a subject line, then the body.
