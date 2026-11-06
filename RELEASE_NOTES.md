The apps are ad-hoc signed, not notarized. After unzipping and moving an app to Applications, run once in Terminal:

```bash
xattr -dr com.apple.quarantine "/Applications/Genome Canvas.app"
xattr -dr com.apple.quarantine "/Applications/Omics Canvas.app"
```
