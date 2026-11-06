import re
import unittest
from pathlib import Path

import server

APP = Path(server.APP_DIR)
REPOSITORY = APP.parent


class WorkspaceChooserTests(unittest.TestCase):
    """workspace-chooser.mjs: Omics Canvas's workspace chooser and manager, served by Genome Canvas to every module."""

    def test_served_and_bundled(self):
        self.assertEqual(server.STATIC_FILES["/workspace-chooser.mjs"][0], "workspace-chooser.mjs")
        self.assertIn("workspace-chooser.mjs", (APP / "macos" / "GenomeCanvasDesktop" / "build.sh").read_text())
        module = (APP / "workspace-chooser.mjs").read_text()
        for name in ("chooseWorkspace", "manageWorkspaces", "selectWorkspace", "watchWorkspace", "announceWorkspace"):
            self.assertRegex(module, r"export (async )?function {}\(".format(name))

    def test_one_version_everywhere(self):
        # versioned assets are cached for a day: every page that imports the module names the same ?v=
        files = [APP / "index.html", APP / "app.js"]
        for other in ("hub/web/src/common.ts", "great/web/great.mjs"):
            if (REPOSITORY / other).is_file():
                files.append(REPOSITORY / other)
        versions = {}
        for path in files:
            for version in re.findall(r"workspace-chooser\.mjs\?v=([0-9.]+)", path.read_text()):
                versions.setdefault(version, []).append(path.name)
        self.assertEqual(len(versions), 1, versions)
        self.assertGreaterEqual(len(next(iter(versions.values()))), 2)  # the preload and app.js's import at least


if __name__ == "__main__":
    unittest.main()
