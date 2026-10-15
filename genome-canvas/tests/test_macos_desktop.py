import plistlib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAC_APP = ROOT / "macos" / "GenomeCanvasDesktop"


class MacOSDesktopClientTests(unittest.TestCase):
    def test_info_plist_allows_http_only_for_web_content(self):
        with (MAC_APP / "Resources" / "Info.plist").open("rb") as stream:
            info = plistlib.load(stream)
        self.assertEqual(info["CFBundleIdentifier"], "org.genomecanvas.desktop")
        self.assertEqual(info["CFBundleShortVersionString"], "1.8.0")
        self.assertEqual(info["LSMinimumSystemVersion"], "13.0")
        self.assertTrue(info["NSAppTransportSecurity"]["NSAllowsArbitraryLoadsInWebContent"])
        self.assertNotIn("NSAllowsArbitraryLoads", info["NSAppTransportSecurity"])

    def test_client_opens_genomecanvas_links_in_place(self):
        with (MAC_APP / "Resources" / "Info.plist").open("rb") as stream:
            info = plistlib.load(stream)
        schemes = [scheme for entry in info["CFBundleURLTypes"] for scheme in entry["CFBundleURLSchemes"]]
        self.assertEqual(schemes, ["genomecanvas"])
        source = (MAC_APP / "Sources" / "main.m").read_text(encoding="utf-8")
        web_app = (ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn("application:(NSApplication *)application openURLs:", source)
        self.assertIn('@[@"locus", @"genome", @"highlight", @"color", @"track"]', source)
        self.assertIn("b.openLink(%@)", source)
        self.assertIn("self.pendingLinkQuery", source)
        # a link that launches the app comes before the web view exists
        self.assertIn("if (!self.webView) {\n        self.pendingLinkQuery = linkQuery;", source)
        self.assertIn("openLink(query) { if (!state.browser) return false;", web_app)

    def test_client_uses_persistent_webkit_and_native_file_panels(self):
        source = (MAC_APP / "Sources" / "main.m").read_text(encoding="utf-8")
        self.assertIn("http://171.65.68.140:8892/genome-canvas/", source)
        self.assertIn("[WKWebsiteDataStore defaultDataStore]", source)
        self.assertIn("WKDownloadDelegate", source)
        self.assertIn("[NSSavePanel savePanel]", source)
        self.assertIn("[NSOpenPanel openPanel]", source)

    def test_client_uses_native_workbench_controls_and_desktop_bridge(self):
        source = (MAC_APP / "Sources" / "main.m").read_text(encoding="utf-8")
        web_app = (ROOT / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "styles.css").read_text(encoding="utf-8")
        self.assertIn("WKScriptMessageHandler", source)
        self.assertIn("NSTableViewDataSource", source)
        self.assertIn('name:@"genomeCanvas"', source)
        self.assertIn('callDesktopMethod:@"moveTrack"', source)
        self.assertIn("window.GenomeCanvasDesktop", web_app)
        self.assertIn("desktopStateSnapshot", web_app)
        self.assertIn("desktop-embedded .track-rail", styles)
        self.assertIn("desktop-embedded .browser-toolbar", styles)
        self.assertNotIn("swatch.tag =", source)
        self.assertIn('swatch.identifier = @"TrackSwatch"', source)
        self.assertNotIn("NSWindowStyleMaskFullSizeContentView", source)
        self.assertIn('action:@selector(createWorkspace:)', source)
        self.assertIn('action:@selector(deleteWorkspace:)', source)

    def test_client_can_launch_a_single_workspace_local_backend(self):
        source = (MAC_APP / "Sources" / "main.m").read_text(encoding="utf-8")
        web_app = (ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn("NSTask *localServerTask", source)
        self.assertIn('environment[@"GENOME_CANVAS_LOCAL_MODE"] = @"1"', source)
        self.assertIn('environment[@"GENOME_DATA_ROOTS"] = @"/"', source)
        self.assertIn('@"--ready-file"', source)
        self.assertIn('@"Use Local Backend"', source)
        self.assertIn('@"Open local files"', source)
        self.assertIn("self.workspaceControls.hidden = self.localMode", source)
        self.assertIn("self.localMode = NO", source)
        self.assertIn("[self stopLocalBackend];\n    self.localMode = NO", source)
        self.assertIn('setObject:self.serverAddress forKey:GCServerDefaultsKey', source)
        self.assertIn("Boolean(state.config?.localMode)", web_app)

    def test_local_backend_url_is_not_rewritten_to_the_gateway_path(self):
        source = (MAC_APP / "Sources" / "main.m").read_text(encoding="utf-8")
        self.assertIn('@"http://127.0.0.1:%ld/?local=1"', source)
        self.assertIn('components.path = loopback ? @"/" : @"/genome-canvas/"', source)

    def test_client_follows_nord_themes_with_manual_override(self):
        source = (MAC_APP / "Sources" / "main.m").read_text(encoding="utf-8")
        web_app = (ROOT / "app.js").read_text(encoding="utf-8")
        script = (MAC_APP / "build.sh").read_text(encoding="utf-8")
        # Nord Snow / Nord tokens shared with styles.css.
        self.assertIn("GC_COLOR(GCBackgroundColor, 0xe5e9f0, 1, 0x242933, 1)", source)
        self.assertIn("GC_COLOR(GCAccentColor, 0x4c7a86, 1, 0x88c0d0, 1)", source)
        self.assertIn('@[@"Use System Setting", @"system"]', source)
        self.assertIn("NSApp.appearance = nil", source)
        self.assertIn('callDesktopMethod:@"setTheme"', source)
        self.assertIn("setTheme(theme)", web_app)
        self.assertIn("Resources/Fonts/*.ttf", script)
        for font in ("Manrope-Variable.ttf", "Fraunces-Variable.ttf", "JetBrainsMono-Variable.ttf"):
            self.assertTrue((MAC_APP / "Resources" / "Fonts" / font).is_file(), font)

    def test_only_toggle_buttons_draw_the_selected_state(self):
        source = (MAC_APP / "Sources" / "main.m").read_text(encoding="utf-8")
        self.assertIn("return self.showsState && self.state == NSControlStateValueOn;", source)
        self.assertEqual(source.count(".showsState = YES;"), 3)
        self.assertNotIn("NSTrackingActiveInKeyWindow", source)

    def test_release_notes_explain_gatekeeper_quarantine(self):
        notes = (MAC_APP / "RELEASE_NOTES.md").read_text(encoding="utf-8")
        # the workflows and the combined release notes live at the root of the Omics Canvas repository
        repository = ROOT.parent
        workflow = (repository / ".github" / "workflows" / "release-macos.yml").read_text(encoding="utf-8")
        release_notes = (repository / "RELEASE_NOTES.md").read_text(encoding="utf-8")
        self.assertIn('xattr -dr com.apple.quarantine "/Applications/Genome Canvas.app"', notes)
        self.assertIn('xattr -dr com.apple.quarantine "/Applications/Genome Canvas.app"', release_notes)
        self.assertIn("--notes-file RELEASE_NOTES.md", workflow)

    def test_build_script_targets_supported_mac_architectures(self):
        script = (MAC_APP / "build.sh").read_text(encoding="utf-8")
        self.assertIn("arm64|x86_64", script)
        self.assertIn("-mmacosx-version-min=13.0", script)
        self.assertIn("xcrun --sdk macosx clang", script)
        self.assertIn("codesign --force --deep --sign -", script)
        self.assertIn("Resources/GenomeCanvas-1024.png", script)
        self.assertIn('Contents/Resources/LocalBackend', script)
        self.assertIn("server.py workspace_store.py resource_cache.py index.html styles.css app.js", script)
        self.assertIn("reference-resources.mjs", script)
        self.assertIn("canvas-theme.mjs", script)
        self.assertIn("vendor/fonts/*.woff2", script)
        self.assertIn('public/favicon.svg', script)
        self.assertTrue((MAC_APP / "Resources" / "GenomeCanvas-1024.png").is_file())


if __name__ == "__main__":
    unittest.main()
