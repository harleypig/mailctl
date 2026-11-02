# Known limits

* `is` and `matches` cannot be expressed in IMAP SEARCH, which only does
  case-insensitive substring matching. So the existing-mail pass searches
  deliberately too broadly and then re-checks every candidate's real headers
  in Python against the Sieve semantics in [*`--compare` tests the whole
  header value*][compare]. Correct, but it fetches the header block of
  every candidate the broad search returned, not just the ones that end up
  matching.
* Merging round-trips the filter set through a parser. A filter with neither a
  `# rule:[NAME]` nor a `# Filter: NAME` comment above it is renamed
  `Unnamed rule N`, and formatting is normalized. Your own `#` comments are
  kept, each above the filter it preceded; `/* ... */` comments are dropped.
  The diff shows all of this before anything is uploaded.
* `filter list` and `filter optimize` judge the filter set from the filters' own
  conditions and their order. Nothing runs a message through your existing
  filters: mailctl can apply criteria you give it to old mail, but it cannot
  tell you which of your filters would have caught a given message.

[compare]: USAGE.md#--compare-tests-the-whole-header-value
