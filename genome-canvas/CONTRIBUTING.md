# Contributing

1. Create a focused branch from the default branch.
2. Keep real genomic data, local paths, credentials and runtime state out of
   commits.
3. Run `npm test` before submitting a change.
4. Describe user-visible behavior changes and include screenshots for UI work.
   For the native macOS client, push a `mac-ui-*` branch to get light and dark
   screenshots from the **macOS UI preview** workflow.

The backend targets Python 3.8+ and intentionally uses the standard library.
Frontend unit tests use Node.js's built-in test runner.
