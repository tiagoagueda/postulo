Closes #

## What this changes and why

## A library, a standard, a format

- **What library was looked at?** Before writing code that implements a standard, a format,
  a data set or a well-known algorithm, name the open-source library you found, or say none
  exists. See [Look for a library before you write one](../CONTRIBUTING.md#look-for-a-library-before-you-write-one).
- **Does it need a licence line?** Files copied into the tree go in `THIRD-PARTY.md`; a
  package from the index gets a comment in `pyproject.toml` saying why it is there.
- **Did the advisory check come back clean?** `pip-audit` or `npm audit` over the lock, or
  the advisory by name if the library is not taken because of it.

A new export or import follows [Formats](../CONTRIBUTING.md#formats).
