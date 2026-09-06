# Letters

Four kinds, told apart by their shape rather than their name.

| Kind | What it is |
| --- | --- |
| **Cover letter** | One page, addressed, about one posting. |
| **Motivation letter** | Longer and sectioned: your story and your reasons, usually with no addressee block. The norm for academic posts, EU institutions, NGOs and apprenticeships, and across much of Europe. |
| **Speculative letter** | Unsolicited, with no posting behind it. Pairs with a thin listing. |
| **Follow-up note** | Short, after an interview, and worth keeping because you will want the wording again. |

Choose the kind when you make the letter and it starts from that kind's shape — a
motivation letter opens with its sections in place, a follow-up note with two lines and a
sign-off — and from the theme that suits it. Change either afterwards; nothing is locked.
The letters page filters by kind, and a rendered motivation letter is filed as a
motivation letter rather than a cover letter, so *Sent documents* says what it was.

**Documents → Letters**

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

## Language

A letter has a **language**, and the PDF says so. It matters more than it sounds: the file
you send is one a recruiter may open with a screen reader, and a Portuguese letter declaring
itself English is read aloud with English letter-to-sound rules. The renderer hyphenates and
justifies by the same declaration.

Leave it blank and the letter follows the language you read Postulo in, which is the right
guess far more often than English is. Set it when you write to an employer in a language
that is not your own. CVs have the same field, and behave the same way.

## Themes

The same two as CVs — Plain and Classic — laid out as a letter: your contact block, the
recipient, the date, a subject line, then the body.
