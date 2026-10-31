# A Candidate XML from europass.europa.eu, inside a PDF that Google Docs wrote

`test_with_attachment.pdf` is copied unchanged from
<https://github.com/vatanasov/cv/blob/main/internal/testdata/test_with_attachment.pdf>
(commit a6f1b52a40adaff63ae41652f32048a0f3e919d0, "Add pdf/xml tests", written 2025-03-24 and
committed 2025-03-25), under the MIT licence below. SHA-256 `b0fd3a638a4de0acb8085d1eed729cf5a9c9f360c660d6b60b9d29b9a0928ee2`.

**What it is.** The attached `attachment.xml` looks like a genuine export from the current
Europass editor: a `{http://www.europass.eu/1.0}Candidate` with the `europass_eures.eu/1.0`,
`hr-xml.org/3` and `oagis/9` namespaces, `schemeID="Test-0001"`, Quill HTML in the
descriptions and a `RenderingInformation` block naming Template1 and a SectionsOrder. Its
details are invented ("Test Testing").

**What it is not.** The PDF around it is not the editor's: it was written by Google Docs, and
the XML was attached afterwards as an incremental update. The attachment is a bare stream,
reached from the `/EmbeddedFiles` name tree through a file specification that has only
`/UF`: no `/F`, no `/Type`, no `/Subtype`, no `/Filter` and no `/AF`. So it shows that the
reader reads a real Candidate and finds an attachment without `/Type /EmbeddedFile`, not what
the editor's own PDF looks like (#318).

Read by `test_a_real_download_reads` in `tests/test_europass_candidate.py`.

## Licence

    MIT License

    Copyright (c) 2025 Valentin Atanasov

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.
