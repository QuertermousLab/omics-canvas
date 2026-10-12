#import <AppKit/AppKit.h>
#import <CoreText/CoreText.h>
#import <WebKit/WebKit.h>
#include <math.h>
#include <stdio.h>

static NSString * const GCDefaultServerAddress = @"http://171.65.68.140:8892/genome-canvas/";
static NSString * const GCServerDefaultsKey = @"GenomeCanvasServerAddress";
static NSString * const GCAppearanceDefaultsKey = @"GenomeCanvasAppearance";
static NSPasteboardType const GCTrackRowPasteboardType = @"org.genomecanvas.desktop.track-row";

#pragma mark - Nord palette

// These mirror the CSS custom properties in styles.css: :root is Nord
// "Snow Storm" (light) and html[data-theme="dark"] is Nord "Polar Night".
static NSColor *GCHex(uint32_t rgb, CGFloat alpha) {
    return [NSColor colorWithSRGBRed:((rgb >> 16) & 0xff) / 255.0 green:((rgb >> 8) & 0xff) / 255.0 blue:(rgb & 0xff) / 255.0 alpha:alpha];
}

static BOOL GCAppearanceIsDark(NSAppearance *appearance) {
    NSAppearanceName match = [appearance bestMatchFromAppearancesWithNames:@[NSAppearanceNameAqua, NSAppearanceNameDarkAqua]];
    return [match isEqualToString:NSAppearanceNameDarkAqua];
}

static NSColor *GCDynamic(uint32_t light, CGFloat lightAlpha, uint32_t dark, CGFloat darkAlpha) {
    NSColor *lightColor = GCHex(light, lightAlpha);
    NSColor *darkColor = GCHex(dark, darkAlpha);
    return [NSColor colorWithName:nil dynamicProvider:^NSColor *(NSAppearance *appearance) {
        return GCAppearanceIsDark(appearance) ? darkColor : lightColor;
    }];
}

#define GC_COLOR(name, light, lightAlpha, dark, darkAlpha) \
    static NSColor *name(void) { \
        static NSColor *color; static dispatch_once_t once; \
        dispatch_once(&once, ^{ color = GCDynamic(light, lightAlpha, dark, darkAlpha); }); \
        return color; \
    }

GC_COLOR(GCBackgroundColor, 0xe5e9f0, 1, 0x242933, 1)
GC_COLOR(GCRailColor, 0xe5e9f0, 1, 0x2a303b, 1)
GC_COLOR(GCSurfaceColor, 0xf8f9fb, 1, 0x2e3440, 1)
GC_COLOR(GCSurface3Color, 0xe5e9f0, 1, 0x3b4252, 1)
GC_COLOR(GCHoverColor, 0xe9edf2, 1, 0x394050, 1)
GC_COLOR(GCBorderColor, 0xd8dee9, 1, 0x3b4252, 1)
GC_COLOR(GCBorderStrongColor, 0xc0c8d4, 1, 0x4c566a, 1)
GC_COLOR(GCDividerColor, 0xe5e9f0, 1, 0x3b4252, 1)
GC_COLOR(GCTextColor, 0x2e3440, 1, 0xeceff4, 1)
GC_COLOR(GCText2Color, 0x434c5e, 1, 0xd8dee9, 1)
GC_COLOR(GCText3Color, 0x69728a, 1, 0x9aa5b8, 1)
GC_COLOR(GCText4Color, 0x9aa2b1, 1, 0x687286, 1)
GC_COLOR(GCAccentColor, 0x4c7a86, 1, 0x88c0d0, 1)
GC_COLOR(GCAccentHoverColor, 0x3f6873, 1, 0x9dcfdc, 1)
GC_COLOR(GCAccentSoftColor, 0xe0ebee, 1, 0x88c0d0, 0.14)
GC_COLOR(GCAccentRingColor, 0x4c7a86, 0.28, 0x88c0d0, 0.40)
GC_COLOR(GCAccentTextColor, 0x3a6570, 1, 0x8fd3e3, 1)
GC_COLOR(GCOnAccentColor, 0xffffff, 1, 0x242933, 1)
GC_COLOR(GCBrandGlyphColor, 0x5e7f8c, 1, 0x5e81ac, 1)
GC_COLOR(GCCanvasColor, 0xffffff, 1, 0x2e3440, 1)
GC_COLOR(GCSuccessColor, 0x7a9a5e, 1, 0xa3be8c, 1)
GC_COLOR(GCWarningColor, 0xd4a94f, 1, 0xebcb8b, 1)
GC_COLOR(GCDangerColor, 0xbf616a, 1, 0xbf616a, 1)
GC_COLOR(GCDangerSoftColor, 0xf6e4e6, 1, 0xbf616a, 0.18)
GC_COLOR(GCGreenSoftColor, 0xe6eedd, 1, 0xa3be8c, 0.16)
GC_COLOR(GCGreenTextColor, 0x5a7642, 1, 0xb9d4a2, 1)

#pragma mark - Bundled fonts

// Manrope, Fraunces and JetBrains Mono are the web interface's fonts (SIL OFL),
// bundled as variable TrueType files in Resources/Fonts.
static void GCRegisterBundledFonts(void) {
    for (NSURL *url in [[NSBundle mainBundle] URLsForResourcesWithExtension:@"ttf" subdirectory:@"Fonts"] ?: @[]) {
        CTFontManagerRegisterFontsForURL((__bridge CFURLRef)url, kCTFontManagerScopeProcess, NULL);
    }
}

static NSFont *GCVariableFont(NSString *family, CGFloat size, CGFloat weight, BOOL monospaced) {
    static NSMutableDictionary<NSString *, NSFont *> *cache;
    static dispatch_once_t once;
    dispatch_once(&once, ^{ cache = [NSMutableDictionary dictionary]; });
    NSString *key = [NSString stringWithFormat:@"%@-%.1f-%.0f", family, size, weight];
    NSFont *cached = cache[key];
    if (cached) return cached;
    NSFontDescriptor *descriptor = [NSFontDescriptor fontDescriptorWithFontAttributes:@{
        NSFontFamilyAttribute: family,
        NSFontVariationAttribute: @{@(0x77676874): @(weight)},  // 'wght'
    }];
    NSFont *font = [NSFont fontWithDescriptor:descriptor size:size];
    if (!font || ![font.familyName isEqualToString:family]) {
        NSFontWeight fallback = weight >= 600 ? NSFontWeightSemibold : weight >= 500 ? NSFontWeightMedium : NSFontWeightRegular;
        font = monospaced ? [NSFont monospacedSystemFontOfSize:size weight:fallback] : [NSFont systemFontOfSize:size weight:fallback];
    }
    cache[key] = font;
    return font;
}

static NSFont *GCUIFont(CGFloat size, CGFloat weight) { return GCVariableFont(@"Manrope", size, weight, NO); }
static NSFont *GCHeadingFont(CGFloat size) { return GCVariableFont(@"Fraunces", size, 600, NO); }
static NSFont *GCMonoFont(CGFloat size, CGFloat weight) { return GCVariableFont(@"JetBrains Mono", size, weight, YES); }

static NSView *GCFindView(NSView *root, NSString *identifier) {
    if ([root.identifier isEqualToString:identifier]) return root;
    for (NSView *child in root.subviews) {
        NSView *found = GCFindView(child, identifier);
        if (found) return found;
    }
    return nil;
}

static NSImage *GCSymbol(NSString *name, CGFloat pointSize, NSString *description) {
    NSImage *image = [NSImage imageWithSystemSymbolName:name accessibilityDescription:description];
    NSImageSymbolConfiguration *configuration = [NSImageSymbolConfiguration configurationWithPointSize:pointSize weight:NSFontWeightMedium];
    return [image imageWithSymbolConfiguration:configuration] ?: image;
}

#pragma mark - Themed views

// A rounded, filled and optionally stroked panel. It draws with dynamic colors
// in drawRect:, so it follows light/dark changes and renders in snapshots.
@interface GCFillView : NSView
@property(nonatomic, strong) NSColor *fillColor;
@property(nonatomic, strong) NSColor *strokeColor;
@property(nonatomic) CGFloat cornerRadius;
@property(nonatomic) BOOL clipsContent;
@end

@implementation GCFillView
- (instancetype)initWithFrame:(NSRect)frame {
    if ((self = [super initWithFrame:frame])) self.translatesAutoresizingMaskIntoConstraints = NO;
    return self;
}
- (void)setFillColor:(NSColor *)fillColor { _fillColor = fillColor; self.needsDisplay = YES; }
- (void)setStrokeColor:(NSColor *)strokeColor { _strokeColor = strokeColor; self.needsDisplay = YES; }
- (void)setCornerRadius:(CGFloat)cornerRadius {
    _cornerRadius = cornerRadius;
    if (self.layer) self.layer.cornerRadius = cornerRadius;
    self.needsDisplay = YES;
}
- (void)setClipsContent:(BOOL)clipsContent {
    _clipsContent = clipsContent;
    if (clipsContent) self.wantsLayer = YES;
    self.layer.cornerRadius = self.cornerRadius;
    self.layer.masksToBounds = clipsContent;
}
- (void)drawRect:(NSRect)dirtyRect {
    NSBezierPath *path = [NSBezierPath bezierPathWithRoundedRect:NSInsetRect(self.bounds, 0.5, 0.5) xRadius:self.cornerRadius yRadius:self.cornerRadius];
    if (self.fillColor) { [self.fillColor setFill]; [path fill]; }
    if (self.strokeColor) { [self.strokeColor setStroke]; path.lineWidth = 1; [path stroke]; }
}
@end

typedef NS_ENUM(NSInteger, GCButtonStyle) {
    GCButtonStylePrimary,    // .primary-button
    GCButtonStyleSecondary,  // .secondary-button / .source-actions button
    GCButtonStyleGhost,      // .quiet-button / .icon-button.ghost
    GCButtonStyleTool,       // .tool-button (toggles to the accent state)
    GCButtonStyleSegment,    // data-source segmented control
};

// Borderless NSButton with the web interface's button styles drawn behind
// AppKit's own image and title rendering.
@interface GCButton : NSButton
@property(nonatomic) GCButtonStyle style;
// AppKit flips `state` on every click, even for momentary push buttons, so the
// "on" appearance is drawn only for buttons that are meant to toggle.
@property(nonatomic) BOOL showsState;
@property(nonatomic, copy) NSString *label;
@property(nonatomic) BOOL hovered;
@property(nonatomic) CGFloat fixedHeight;
@property(nonatomic, strong) NSTrackingArea *hoverArea;
+ (instancetype)buttonWithLabel:(NSString *)label symbol:(NSString *)symbol style:(GCButtonStyle)style target:(id)target action:(SEL)action;
- (void)refreshColors;
@end

@implementation GCButton
+ (instancetype)buttonWithLabel:(NSString *)label symbol:(NSString *)symbol style:(GCButtonStyle)style target:(id)target action:(SEL)action {
    GCButton *button = [[self alloc] initWithFrame:NSZeroRect];
    button.translatesAutoresizingMaskIntoConstraints = NO;
    button.bordered = NO;
    button.style = style;
    button.target = target;
    button.action = action;
    button.fixedHeight = 32;
    button.font = GCUIFont(13, style == GCButtonStylePrimary ? 650 : 550);
    if (symbol.length) {
        button.image = GCSymbol(symbol, 13, label ?: symbol);
        button.imagePosition = label.length ? NSImageLeading : NSImageOnly;
        button.imageHugsTitle = YES;
    }
    button.label = label ?: @"";
    if (!label.length) button.toolTip = symbol;
    return button;
}
- (void)setLabel:(NSString *)label { _label = [label copy]; [self refreshColors]; [self invalidateIntrinsicContentSize]; }
- (void)setHovered:(BOOL)hovered { if (_hovered == hovered) return; _hovered = hovered; [self refreshColors]; }
- (void)setState:(NSControlStateValue)state { [super setState:state]; [self refreshColors]; }
- (void)setEnabled:(BOOL)enabled { [super setEnabled:enabled]; [self refreshColors]; }
- (void)setStyle:(GCButtonStyle)style { _style = style; [self refreshColors]; }
- (BOOL)isOn { return self.showsState && self.state == NSControlStateValueOn; }
- (NSColor *)foregroundColor {
    if (!self.enabled) return GCText4Color();
    BOOL on = [self isOn];
    switch (self.style) {
        case GCButtonStylePrimary: return GCOnAccentColor();
        case GCButtonStyleTool: return on ? GCAccentTextColor() : (self.hovered ? GCTextColor() : GCText2Color());
        case GCButtonStyleSegment: return on ? GCTextColor() : GCText3Color();
        default: return self.hovered ? GCTextColor() : GCText2Color();
    }
}
- (void)refreshColors {
    NSColor *foreground = [self foregroundColor];
    self.contentTintColor = foreground;
    NSMutableParagraphStyle *paragraph = [[NSMutableParagraphStyle alloc] init];
    paragraph.alignment = NSTextAlignmentCenter;
    paragraph.lineBreakMode = NSLineBreakByTruncatingTail;
    self.attributedTitle = [[NSAttributedString alloc] initWithString:self.label ?: @"" attributes:@{
        NSForegroundColorAttributeName: foreground,
        NSFontAttributeName: self.font ?: GCUIFont(13, 550),
        NSParagraphStyleAttributeName: paragraph,
    }];
    self.needsDisplay = YES;
}
- (NSSize)intrinsicContentSize {
    if (!self.label.length) return NSMakeSize(self.fixedHeight, self.fixedHeight);
    NSSize size = [super intrinsicContentSize];
    return NSMakeSize(ceil(size.width + 24), self.fixedHeight);
}
- (void)updateTrackingAreas {
    [super updateTrackingAreas];
    if (self.hoverArea) [self removeTrackingArea:self.hoverArea];
    self.hoverArea = [[NSTrackingArea alloc] initWithRect:NSZeroRect
        options:NSTrackingMouseEnteredAndExited | NSTrackingActiveInActiveApp | NSTrackingInVisibleRect owner:self userInfo:nil];
    [self addTrackingArea:self.hoverArea];
}
- (void)mouseEntered:(NSEvent *)event { self.hovered = YES; }
- (void)mouseExited:(NSEvent *)event { self.hovered = NO; }
- (void)viewDidChangeEffectiveAppearance { [super viewDidChangeEffectiveAppearance]; [self refreshColors]; }
- (void)drawRect:(NSRect)dirtyRect {
    BOOL active = self.enabled && (self.hovered || self.cell.isHighlighted);
    BOOL on = [self isOn];
    NSColor *fill = nil;
    NSColor *stroke = nil;
    CGFloat radius = self.style == GCButtonStyleSegment ? 7 : 9;
    switch (self.style) {
        case GCButtonStylePrimary:
            fill = !self.enabled ? GCSurface3Color() : (active ? GCAccentHoverColor() : GCAccentColor());
            break;
        case GCButtonStyleSecondary:
            fill = active ? GCHoverColor() : GCSurfaceColor();
            stroke = GCBorderColor();
            break;
        case GCButtonStyleTool:
            fill = on ? GCAccentSoftColor() : (active ? GCHoverColor() : GCSurfaceColor());
            stroke = on ? GCAccentColor() : GCBorderColor();
            break;
        case GCButtonStyleGhost:
            fill = active ? GCHoverColor() : nil;
            break;
        case GCButtonStyleSegment:
            fill = on ? GCSurfaceColor() : (active ? GCHoverColor() : nil);
            stroke = on ? GCBorderColor() : nil;
            break;
    }
    NSBezierPath *path = [NSBezierPath bezierPathWithRoundedRect:NSInsetRect(self.bounds, 0.5, 0.5) xRadius:radius yRadius:radius];
    if (fill) { [fill setFill]; [path fill]; }
    if (stroke) { [stroke setStroke]; path.lineWidth = 1; [path stroke]; }
    [super drawRect:dirtyRect];
}
@end

// The web rail's "Open server files" card: icon tile, title, detail, chevron.
@interface GCCardButton : NSButton
@property(nonatomic, strong) NSTextField *titleLabel;
@property(nonatomic, strong) NSTextField *detailLabel;
@property(nonatomic, strong) NSImageView *chevron;
@property(nonatomic) BOOL hovered;
@property(nonatomic, strong) NSTrackingArea *hoverArea;
@end

@implementation GCCardButton
- (instancetype)initWithFrame:(NSRect)frame {
    if (!(self = [super initWithFrame:frame])) return nil;
    self.translatesAutoresizingMaskIntoConstraints = NO;
    self.bordered = NO;
    self.title = @"";
    GCFillView *tile = [[GCFillView alloc] initWithFrame:NSZeroRect];
    tile.fillColor = GCBrandGlyphColor();
    tile.cornerRadius = 9;
    NSImageView *glyph = [NSImageView imageViewWithImage:GCSymbol(@"server.rack", 16, @"Server files")];
    glyph.translatesAutoresizingMaskIntoConstraints = NO;
    glyph.contentTintColor = GCHex(0xeceff4, 1);
    [tile addSubview:glyph];
    self.titleLabel = [NSTextField labelWithString:@""];
    self.titleLabel.font = GCUIFont(13.5, 650);
    self.titleLabel.textColor = GCTextColor();
    self.detailLabel = [NSTextField wrappingLabelWithString:@""];
    self.detailLabel.font = GCUIFont(12, 450);
    self.detailLabel.textColor = GCText3Color();
    self.detailLabel.maximumNumberOfLines = 2;
    NSStackView *copy = [NSStackView stackViewWithViews:@[self.titleLabel, self.detailLabel]];
    copy.translatesAutoresizingMaskIntoConstraints = NO;
    copy.orientation = NSUserInterfaceLayoutOrientationVertical;
    copy.alignment = NSLayoutAttributeLeading;
    copy.spacing = 2;
    self.chevron = [NSImageView imageViewWithImage:GCSymbol(@"chevron.right", 11, @"Open")];
    self.chevron.translatesAutoresizingMaskIntoConstraints = NO;
    self.chevron.contentTintColor = GCText4Color();
    [self addSubview:tile];
    [self addSubview:copy];
    [self addSubview:self.chevron];
    [NSLayoutConstraint activateConstraints:@[
        [tile.leadingAnchor constraintEqualToAnchor:self.leadingAnchor constant:12],
        [tile.centerYAnchor constraintEqualToAnchor:self.centerYAnchor],
        [tile.widthAnchor constraintEqualToConstant:36], [tile.heightAnchor constraintEqualToConstant:36],
        [glyph.centerXAnchor constraintEqualToAnchor:tile.centerXAnchor],
        [glyph.centerYAnchor constraintEqualToAnchor:tile.centerYAnchor],
        [copy.leadingAnchor constraintEqualToAnchor:tile.trailingAnchor constant:11],
        [copy.trailingAnchor constraintLessThanOrEqualToAnchor:self.chevron.leadingAnchor constant:-8],
        [copy.centerYAnchor constraintEqualToAnchor:self.centerYAnchor],
        [self.detailLabel.widthAnchor constraintLessThanOrEqualToConstant:170],
        [self.chevron.trailingAnchor constraintEqualToAnchor:self.trailingAnchor constant:-12],
        [self.chevron.centerYAnchor constraintEqualToAnchor:self.centerYAnchor],
        [self.heightAnchor constraintEqualToConstant:64],
    ]];
    return self;
}
- (NSView *)hitTest:(NSPoint)point { return [super hitTest:point] ? self : nil; }
- (void)setHovered:(BOOL)hovered {
    _hovered = hovered;
    self.chevron.contentTintColor = hovered ? GCAccentColor() : GCText4Color();
    self.needsDisplay = YES;
}
- (void)updateTrackingAreas {
    [super updateTrackingAreas];
    if (self.hoverArea) [self removeTrackingArea:self.hoverArea];
    self.hoverArea = [[NSTrackingArea alloc] initWithRect:NSZeroRect
        options:NSTrackingMouseEnteredAndExited | NSTrackingActiveInActiveApp | NSTrackingInVisibleRect owner:self userInfo:nil];
    [self addTrackingArea:self.hoverArea];
}
- (void)mouseEntered:(NSEvent *)event { self.hovered = YES; }
- (void)mouseExited:(NSEvent *)event { self.hovered = NO; }
- (void)drawRect:(NSRect)dirtyRect {
    NSRect frame = NSInsetRect(self.bounds, 2, 2);
    if (self.hovered) {
        NSBezierPath *ring = [NSBezierPath bezierPathWithRoundedRect:NSInsetRect(frame, -1.5, -1.5) xRadius:14.5 yRadius:14.5];
        ring.lineWidth = 3;
        [GCAccentRingColor() setStroke];
        [ring stroke];
    }
    NSBezierPath *path = [NSBezierPath bezierPathWithRoundedRect:NSInsetRect(frame, 0.5, 0.5) xRadius:13 yRadius:13];
    [GCSurfaceColor() setFill];
    [path fill];
    [(self.hovered ? GCAccentColor() : GCBorderColor()) setStroke];
    path.lineWidth = 1;
    [path stroke];
}
@end

// Track rows: rounded hover and selection fills like .track-item, and the
// per-row remove button only while hovered or selected.
@interface GCTrackRowView : NSTableRowView
@property(nonatomic) BOOL hovered;
@property(nonatomic, strong) NSTrackingArea *hoverArea;
@end

@implementation GCTrackRowView
- (void)updateTrackingAreas {
    [super updateTrackingAreas];
    if (self.hoverArea) [self removeTrackingArea:self.hoverArea];
    self.hoverArea = [[NSTrackingArea alloc] initWithRect:NSZeroRect
        options:NSTrackingMouseEnteredAndExited | NSTrackingActiveInActiveApp | NSTrackingInVisibleRect owner:self userInfo:nil];
    [self addTrackingArea:self.hoverArea];
}
- (void)mouseEntered:(NSEvent *)event { self.hovered = YES; }
- (void)mouseExited:(NSEvent *)event { self.hovered = NO; }
- (void)setHovered:(BOOL)hovered { _hovered = hovered; [self updateRemoveButton]; self.needsDisplay = YES; }
- (void)setSelected:(BOOL)selected { [super setSelected:selected]; [self updateRemoveButton]; }
- (void)didAddSubview:(NSView *)subview { [super didAddSubview:subview]; [self updateRemoveButton]; }
- (void)updateRemoveButton { GCFindView(self, @"TrackRemove").hidden = !(self.hovered || self.selected); }
- (NSBackgroundStyle)interiorBackgroundStyle { return NSBackgroundStyleNormal; }
- (NSBezierPath *)rowPath { return [NSBezierPath bezierPathWithRoundedRect:NSInsetRect(self.bounds, 2, 1) xRadius:9 yRadius:9]; }
- (void)drawBackgroundInRect:(NSRect)dirtyRect {
    if (self.hovered && !self.selected) { [GCHoverColor() setFill]; [[self rowPath] fill]; }
}
- (void)drawSelectionInRect:(NSRect)dirtyRect { [GCAccentSoftColor() setFill]; [[self rowPath] fill]; }
@end

// Thin divider that disappears into the light background and shows as a
// hairline in dark mode, matching the web rail.
@interface GCSplitView : NSSplitView
@end

@implementation GCSplitView
- (NSColor *)dividerColor { return GCDividerColor(); }
@end

@interface GCAppDelegate : NSObject <NSApplicationDelegate, NSMenuItemValidation, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate, WKScriptMessageHandler, NSTableViewDataSource, NSTableViewDelegate>
@property(nonatomic, strong) NSWindow *window;
@property(nonatomic, strong) WKWebView *webView;
@property(nonatomic, strong) GCButton *localModeButton;
@property(nonatomic, strong) GCButton *serverModeButton;
@property(nonatomic, strong) NSTextField *workspaceLabel;
@property(nonatomic, strong) NSStackView *workspaceControls;
@property(nonatomic, strong) NSPopUpButton *workspacePopup;
@property(nonatomic, strong) GCButton *deleteWorkspaceButton;
@property(nonatomic, strong) GCCardButton *filesButton;
@property(nonatomic, strong) NSMenuItem *openFilesMenuItem;
@property(nonatomic, strong) NSMenuItem *localBackendMenuItem;
@property(nonatomic, strong) NSMenuItem *remoteServerMenuItem;
@property(nonatomic, strong) NSArray<NSMenuItem *> *appearanceMenuItems;
@property(nonatomic, strong) NSPopUpButton *genomePopup;
@property(nonatomic, strong) NSTextField *locusField;
@property(nonatomic, strong) GCFillView *statusDot;
@property(nonatomic, strong) NSTextField *statusLabel;
@property(nonatomic, strong) NSTextField *trackCountLabel;
@property(nonatomic, strong) NSTableView *trackTable;
@property(nonatomic, strong) GCButton *highlightButton;
@property(nonatomic, strong) GCButton *clearHighlightsButton;
@property(nonatomic, strong) GCButton *themeButton;
@property(nonatomic, strong) NSColorWell *highlightColorWell;
@property(nonatomic, strong) GCButton *reloadButton;
@property(nonatomic, strong) GCFillView *progressBar;
@property(nonatomic, strong) NSLayoutConstraint *progressWidthConstraint;
@property(nonatomic, strong) NSMapTable<WKDownload *, NSURL *> *downloadDestinations;
@property(nonatomic, copy) NSArray<NSDictionary *> *tracks;
@property(nonatomic, copy) NSArray<NSDictionary *> *workspaces;
@property(nonatomic, copy) NSString *serverAddress;
@property(nonatomic) BOOL localMode;
@property(nonatomic) BOOL snapshotScheduled;
@property(nonatomic, strong) NSTask *localServerTask;
@property(nonatomic, strong) NSFileHandle *localServerLogHandle;
@property(nonatomic, copy) NSString *localServerAddress;
@end

@implementation GCAppDelegate

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    GCRegisterBundledFonts();
    self.downloadDestinations = [NSMapTable weakToStrongObjectsMapTable];
    self.tracks = @[];
    self.workspaces = @[];
    NSUserDefaults *defaults = [NSUserDefaults standardUserDefaults];
    NSString *savedAddress = [defaults stringForKey:GCServerDefaultsKey];
    self.serverAddress = savedAddress ?: GCDefaultServerAddress;
    self.localMode = NO;
    // Preview builds capture the window in both themes (see macos-preview.yml);
    // they use the bundled backend so no server is needed.
    if ([self snapshotDirectory].length) self.localMode = YES;
    [self buildApplicationMenu];

    NSRect frame = NSMakeRect(0, 0, 1480, 900);
    NSWindowStyleMask style = NSWindowStyleMaskTitled
        | NSWindowStyleMaskClosable
        | NSWindowStyleMaskMiniaturizable
        | NSWindowStyleMaskResizable;
    self.window = [[NSWindow alloc] initWithContentRect:frame styleMask:style backing:NSBackingStoreBuffered defer:NO];
    self.window.title = @"Genome Canvas";
    self.window.subtitle = @"Native genomics workbench";
    self.window.minSize = NSMakeSize(1140, 680);
    self.window.titlebarAppearsTransparent = YES;
    self.window.backgroundColor = GCBackgroundColor();
    [self.window center];

    GCSplitView *splitView = [[GCSplitView alloc] initWithFrame:frame];
    splitView.translatesAutoresizingMaskIntoConstraints = NO;
    splitView.vertical = YES;
    splitView.dividerStyle = NSSplitViewDividerStyleThin;
    self.window.contentView = splitView;

    NSView *sidebar = [self buildSidebar];
    NSView *content = [self buildGenomeContent];
    [splitView addArrangedSubview:sidebar];
    [splitView addArrangedSubview:content];
    [sidebar.widthAnchor constraintEqualToConstant:280].active = YES;

    [self.webView addObserver:self forKeyPath:@"estimatedProgress"
                      options:NSKeyValueObservingOptionInitial | NSKeyValueObservingOptionNew context:NULL];
    [self.webView addObserver:self forKeyPath:@"loading" options:NSKeyValueObservingOptionNew context:NULL];
    [NSApp addObserver:self forKeyPath:@"effectiveAppearance" options:NSKeyValueObservingOptionNew context:NULL];
    [self applyAppearancePreference];

    [self.window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
    [self updateConnectionUI];
    if (self.localMode) [self startLocalBackend];
    else [self loadServerAddress:self.serverAddress remember:NO];
}

#pragma mark - Sidebar (mirrors the web track rail)

- (NSView *)buildSidebar {
    GCFillView *sidebar = [[GCFillView alloc] initWithFrame:NSZeroRect];
    sidebar.fillColor = GCRailColor();

    NSImageView *mark = [NSImageView imageViewWithImage:NSApp.applicationIconImage ?: [NSImage imageNamed:NSImageNameApplicationIcon]];
    mark.translatesAutoresizingMaskIntoConstraints = NO;
    mark.imageScaling = NSImageScaleProportionallyUpOrDown;
    NSTextField *title = [NSTextField labelWithString:@"Genome Canvas"];
    title.font = GCHeadingFont(16);
    title.textColor = GCTextColor();
    NSStackView *brand = [NSStackView stackViewWithViews:@[mark, title]];
    brand.orientation = NSUserInterfaceLayoutOrientationHorizontal;
    brand.alignment = NSLayoutAttributeCenterY;
    brand.spacing = 9;

    NSTextField *sourceLabel = [self sectionLabel:@"Data source"];
    self.localModeButton = [GCButton buttonWithLabel:@"This Mac" symbol:@"laptopcomputer" style:GCButtonStyleSegment target:self action:@selector(selectLocalMode:)];
    self.serverModeButton = [GCButton buttonWithLabel:@"Server" symbol:@"server.rack" style:GCButtonStyleSegment target:self action:@selector(selectServerMode:)];
    self.localModeButton.fixedHeight = 28;
    self.serverModeButton.fixedHeight = 28;
    self.localModeButton.showsState = YES;
    self.serverModeButton.showsState = YES;
    GCFillView *segments = [[GCFillView alloc] initWithFrame:NSZeroRect];
    segments.fillColor = GCSurface3Color();
    segments.cornerRadius = 10;
    [segments addSubview:self.localModeButton];
    [segments addSubview:self.serverModeButton];
    [NSLayoutConstraint activateConstraints:@[
        [segments.heightAnchor constraintEqualToConstant:34],
        [self.localModeButton.leadingAnchor constraintEqualToAnchor:segments.leadingAnchor constant:3],
        [self.localModeButton.centerYAnchor constraintEqualToAnchor:segments.centerYAnchor],
        [self.serverModeButton.leadingAnchor constraintEqualToAnchor:self.localModeButton.trailingAnchor constant:2],
        [self.serverModeButton.trailingAnchor constraintEqualToAnchor:segments.trailingAnchor constant:-3],
        [self.serverModeButton.centerYAnchor constraintEqualToAnchor:segments.centerYAnchor],
        [self.serverModeButton.widthAnchor constraintEqualToAnchor:self.localModeButton.widthAnchor],
    ]];

    self.workspaceLabel = [self sectionLabel:@"Workspace"];
    self.workspacePopup = [[NSPopUpButton alloc] initWithFrame:NSZeroRect pullsDown:NO];
    self.workspacePopup.translatesAutoresizingMaskIntoConstraints = NO;
    self.workspacePopup.bordered = NO;
    self.workspacePopup.font = GCUIFont(13, 550);
    self.workspacePopup.target = self;
    self.workspacePopup.action = @selector(changeWorkspace:);
    [self.workspacePopup addItemWithTitle:@"Choose Workspace…"];
    GCFillView *workspaceField = [self fieldContainerWithContent:self.workspacePopup inset:6];
    [workspaceField setContentHuggingPriority:NSLayoutPriorityDefaultLow forOrientation:NSLayoutConstraintOrientationHorizontal];
    [workspaceField setContentCompressionResistancePriority:NSLayoutPriorityDefaultLow forOrientation:NSLayoutConstraintOrientationHorizontal];
    GCButton *createWorkspaceButton = [self iconButton:@"plus" tip:@"Create temporary project" action:@selector(createWorkspace:)];
    self.deleteWorkspaceButton = [self iconButton:@"trash" tip:@"Delete selected temporary project" action:@selector(deleteWorkspace:)];
    self.deleteWorkspaceButton.enabled = NO;
    GCButton *manageWorkspaceButton = [self iconButton:@"gearshape" tip:@"Manage all workspaces" action:@selector(manageWorkspaces:)];
    self.workspaceControls = [NSStackView stackViewWithViews:@[
        workspaceField, createWorkspaceButton, self.deleteWorkspaceButton, manageWorkspaceButton
    ]];
    self.workspaceControls.orientation = NSUserInterfaceLayoutOrientationHorizontal;
    self.workspaceControls.alignment = NSLayoutAttributeCenterY;
    self.workspaceControls.spacing = 2;

    NSView *firstDivider = [self dividerLine];

    NSTextField *libraryLabel = [self sectionLabel:@"Track library"];
    self.trackCountLabel = [NSTextField labelWithString:@""];
    [self updateTrackCount];
    NSStackView *libraryHeading = [NSStackView stackViewWithViews:@[libraryLabel, self.trackCountLabel]];
    libraryHeading.orientation = NSUserInterfaceLayoutOrientationVertical;
    libraryHeading.alignment = NSLayoutAttributeLeading;
    libraryHeading.spacing = 1;

    self.filesButton = [[GCCardButton alloc] initWithFrame:NSZeroRect];
    self.filesButton.target = self;
    self.filesButton.action = @selector(openServerFiles:);
    GCButton *publicButton = [GCButton buttonWithLabel:@"Public data" symbol:@"globe" style:GCButtonStyleSecondary target:self action:@selector(openPublicData:)];
    GCButton *referenceButton = [GCButton buttonWithLabel:@"Reference" symbol:@"scope" style:GCButtonStyleSecondary target:self action:@selector(openCustomReference:)];
    publicButton.font = GCUIFont(12.5, 550);
    referenceButton.font = GCUIFont(12.5, 550);
    referenceButton.toolTip = @"Load a custom reference genome";
    publicButton.label = publicButton.label;
    referenceButton.label = referenceButton.label;
    NSStackView *sourceActions = [NSStackView stackViewWithViews:@[publicButton, referenceButton]];
    sourceActions.orientation = NSUserInterfaceLayoutOrientationHorizontal;
    sourceActions.distribution = NSStackViewDistributionFillEqually;
    sourceActions.spacing = 8;

    NSView *secondDivider = [self dividerLine];

    NSTextField *currentLabel = [self sectionLabel:@"Current view"];
    GCButton *refreshButton = [self iconButton:@"arrow.clockwise" tip:@"Refresh track list" action:@selector(refreshTracks:)];
    refreshButton.fixedHeight = 26;
    NSView *headingSpacer = [[NSView alloc] initWithFrame:NSZeroRect];
    [headingSpacer setContentHuggingPriority:1 forOrientation:NSLayoutConstraintOrientationHorizontal];
    NSStackView *currentHeading = [NSStackView stackViewWithViews:@[currentLabel, headingSpacer, refreshButton]];
    currentHeading.orientation = NSUserInterfaceLayoutOrientationHorizontal;
    currentHeading.alignment = NSLayoutAttributeCenterY;

    self.trackTable = [[NSTableView alloc] initWithFrame:NSZeroRect];
    NSTableColumn *trackColumn = [[NSTableColumn alloc] initWithIdentifier:@"track"];
    trackColumn.resizingMask = NSTableColumnAutoresizingMask;
    [self.trackTable addTableColumn:trackColumn];
    self.trackTable.headerView = nil;
    self.trackTable.style = NSTableViewStylePlain;
    self.trackTable.rowHeight = 44;
    self.trackTable.intercellSpacing = NSMakeSize(0, 2);
    self.trackTable.backgroundColor = [NSColor clearColor];
    self.trackTable.selectionHighlightStyle = NSTableViewSelectionHighlightStyleRegular;
    self.trackTable.delegate = self;
    self.trackTable.dataSource = self;
    [self.trackTable registerForDraggedTypes:@[GCTrackRowPasteboardType]];
    self.trackTable.draggingDestinationFeedbackStyle = NSTableViewDraggingDestinationFeedbackStyleGap;
    self.trackTable.verticalMotionCanBeginDrag = YES;

    NSScrollView *trackScroll = [[NSScrollView alloc] initWithFrame:NSZeroRect];
    trackScroll.translatesAutoresizingMaskIntoConstraints = NO;
    trackScroll.documentView = self.trackTable;
    trackScroll.hasVerticalScroller = YES;
    trackScroll.autohidesScrollers = YES;
    trackScroll.drawsBackground = NO;
    trackScroll.borderType = NSNoBorder;
    [trackScroll setContentHuggingPriority:1 forOrientation:NSLayoutConstraintOrientationVertical];
    [trackScroll setContentCompressionResistancePriority:NSLayoutPriorityDefaultLow forOrientation:NSLayoutConstraintOrientationVertical];

    NSView *tips = [self tipsView];

    self.statusDot = [[GCFillView alloc] initWithFrame:NSZeroRect];
    self.statusDot.cornerRadius = 3.5;
    self.statusDot.fillColor = GCWarningColor();
    self.statusLabel = [NSTextField labelWithString:@"Connecting"];
    self.statusLabel.font = GCUIFont(12, 550);
    self.statusLabel.textColor = GCText2Color();
    self.statusLabel.lineBreakMode = NSLineBreakByTruncatingTail;
    NSStackView *status = [NSStackView stackViewWithViews:@[self.statusDot, self.statusLabel]];
    status.orientation = NSUserInterfaceLayoutOrientationHorizontal;
    status.alignment = NSLayoutAttributeCenterY;
    status.spacing = 7;

    NSArray<NSView *> *rows = @[
        brand, sourceLabel, segments, self.workspaceLabel, self.workspaceControls, firstDivider,
        libraryHeading, self.filesButton, sourceActions, secondDivider, currentHeading, trackScroll, tips, status
    ];
    NSStackView *column = [NSStackView stackViewWithViews:rows];
    column.translatesAutoresizingMaskIntoConstraints = NO;
    column.orientation = NSUserInterfaceLayoutOrientationVertical;
    column.alignment = NSLayoutAttributeLeading;
    column.distribution = NSStackViewDistributionFill;
    column.spacing = 6;
    [column setCustomSpacing:18 afterView:brand];
    [column setCustomSpacing:14 afterView:segments];
    [column setCustomSpacing:16 afterView:self.workspaceControls];
    [column setCustomSpacing:14 afterView:firstDivider];
    [column setCustomSpacing:12 afterView:libraryHeading];
    [column setCustomSpacing:8 afterView:self.filesButton];
    [column setCustomSpacing:16 afterView:sourceActions];
    [column setCustomSpacing:12 afterView:secondDivider];
    [column setCustomSpacing:12 afterView:trackScroll];
    [column setCustomSpacing:12 afterView:tips];
    [sidebar addSubview:column];

    NSMutableArray<NSLayoutConstraint *> *constraints = [NSMutableArray arrayWithArray:@[
        [mark.widthAnchor constraintEqualToConstant:28], [mark.heightAnchor constraintEqualToConstant:28],
        [column.topAnchor constraintEqualToAnchor:sidebar.safeAreaLayoutGuide.topAnchor constant:12],
        [column.leadingAnchor constraintEqualToAnchor:sidebar.leadingAnchor constant:12],
        [column.trailingAnchor constraintEqualToAnchor:sidebar.trailingAnchor constant:-12],
        [column.bottomAnchor constraintEqualToAnchor:sidebar.bottomAnchor constant:-12],
        [trackScroll.heightAnchor constraintGreaterThanOrEqualToConstant:96],
        [self.statusDot.widthAnchor constraintEqualToConstant:7], [self.statusDot.heightAnchor constraintEqualToConstant:7],
    ]];
    for (NSView *row in @[segments, self.workspaceControls, firstDivider, self.filesButton, sourceActions, secondDivider, currentHeading, trackScroll, tips]) {
        [constraints addObject:[row.widthAnchor constraintEqualToAnchor:column.widthAnchor]];
    }
    [NSLayoutConstraint activateConstraints:constraints];
    return sidebar;
}

- (NSView *)tipsView {
    NSArray<NSArray<NSString *> *> *tips = @[
        @[@"info.circle", @"Indexed formats are fastest.", @" BAM, CRAM, VCF and their index files are paired automatically."],
        @[@"cursorarrow.click", @"Right-click a track", @" for settings; drag rows here or the six-dot grip to reorder."],
    ];
    NSMutableArray<NSView *> *rows = [NSMutableArray array];
    for (NSArray<NSString *> *tip in tips) {
        NSImageView *icon = [NSImageView imageViewWithImage:GCSymbol(tip[0], 11, tip[1])];
        icon.translatesAutoresizingMaskIntoConstraints = NO;
        icon.contentTintColor = GCText4Color();
        NSMutableAttributedString *text = [[NSMutableAttributedString alloc] initWithString:tip[1] attributes:@{
            NSFontAttributeName: GCUIFont(12, 600), NSForegroundColorAttributeName: GCText2Color()
        }];
        [text appendAttributedString:[[NSAttributedString alloc] initWithString:tip[2] attributes:@{
            NSFontAttributeName: GCUIFont(12, 450), NSForegroundColorAttributeName: GCText3Color()
        }]];
        NSTextField *label = [NSTextField wrappingLabelWithString:@""];
        label.attributedStringValue = text;
        label.translatesAutoresizingMaskIntoConstraints = NO;
        [label setContentCompressionResistancePriority:NSLayoutPriorityDefaultLow forOrientation:NSLayoutConstraintOrientationHorizontal];
        NSStackView *row = [NSStackView stackViewWithViews:@[icon, label]];
        row.orientation = NSUserInterfaceLayoutOrientationHorizontal;
        row.alignment = NSLayoutAttributeFirstBaseline;
        row.spacing = 8;
        [icon.widthAnchor constraintEqualToConstant:14].active = YES;
        [label.widthAnchor constraintEqualToConstant:228].active = YES;
        [rows addObject:row];
    }
    NSStackView *stack = [NSStackView stackViewWithViews:rows];
    stack.orientation = NSUserInterfaceLayoutOrientationVertical;
    stack.alignment = NSLayoutAttributeLeading;
    stack.spacing = 8;
    return stack;
}

#pragma mark - Toolbar and canvas

- (NSView *)buildGenomeContent {
    NSView *content = [[NSView alloc] initWithFrame:NSZeroRect];
    content.translatesAutoresizingMaskIntoConstraints = NO;
    GCFillView *toolbar = [[GCFillView alloc] initWithFrame:NSZeroRect];
    toolbar.fillColor = GCSurfaceColor();
    [content addSubview:toolbar];
    GCFillView *toolbarRule = [[GCFillView alloc] initWithFrame:NSZeroRect];
    toolbarRule.fillColor = GCBorderColor();
    [content addSubview:toolbarRule];

    self.genomePopup = [[NSPopUpButton alloc] initWithFrame:NSZeroRect pullsDown:NO];
    self.genomePopup.translatesAutoresizingMaskIntoConstraints = NO;
    self.genomePopup.bordered = NO;
    self.genomePopup.font = GCUIFont(13, 550);
    NSArray<NSArray<NSString *> *> *genomes = @[
        @[@"Human · GRCh38 / hg38", @"hg38"], @[@"Human · GRCh37 / hg19", @"hg19"],
        @[@"Mouse · GRCm39 / mm39", @"mm39"], @[@"Mouse · GRCm38 / mm10", @"mm10"],
        @[@"Rat · mRatBN7.2 / rn7", @"rn7"], @[@"Zebrafish · GRCz11", @"danRer11"],
        @[@"Drosophila · dm6", @"dm6"], @[@"Yeast · sacCer3", @"sacCer3"]
    ];
    for (NSArray<NSString *> *entry in genomes) {
        [self.genomePopup addItemWithTitle:entry[0]];
        self.genomePopup.lastItem.representedObject = entry[1];
    }
    self.genomePopup.target = self;
    self.genomePopup.action = @selector(changeGenome:);
    self.genomePopup.toolTip = @"Reference assembly";
    GCFillView *genomeField = [self fieldContainerWithContent:self.genomePopup inset:6];

    NSImageView *searchIcon = [NSImageView imageViewWithImage:GCSymbol(@"magnifyingglass", 12, @"Search")];
    searchIcon.translatesAutoresizingMaskIntoConstraints = NO;
    searchIcon.contentTintColor = GCText3Color();
    self.locusField = [[NSTextField alloc] initWithFrame:NSZeroRect];
    self.locusField.translatesAutoresizingMaskIntoConstraints = NO;
    self.locusField.bordered = NO;
    self.locusField.drawsBackground = NO;
    self.locusField.focusRingType = NSFocusRingTypeNone;
    self.locusField.placeholderAttributedString = [[NSAttributedString alloc] initWithString:@"Gene, rsID, or chr17:7,660,000-7,690,000" attributes:@{
        NSFontAttributeName: GCMonoFont(12.5, 500), NSForegroundColorAttributeName: GCText4Color()
    }];
    self.locusField.font = GCMonoFont(12.5, 500);
    self.locusField.textColor = GCTextColor();
    self.locusField.target = self;
    self.locusField.action = @selector(searchLocus:);
    self.locusField.cell.scrollable = YES;
    self.locusField.cell.wraps = NO;
    NSStackView *locusContent = [NSStackView stackViewWithViews:@[searchIcon, self.locusField]];
    locusContent.translatesAutoresizingMaskIntoConstraints = NO;
    locusContent.orientation = NSUserInterfaceLayoutOrientationHorizontal;
    locusContent.alignment = NSLayoutAttributeCenterY;
    locusContent.spacing = 8;
    GCFillView *locusField = [self fieldContainerWithContent:locusContent inset:10];
    [locusField setContentHuggingPriority:NSLayoutPriorityDefaultLow forOrientation:NSLayoutConstraintOrientationHorizontal];
    [locusField setContentCompressionResistancePriority:NSLayoutPriorityDefaultLow forOrientation:NSLayoutConstraintOrientationHorizontal];

    GCButton *goButton = [GCButton buttonWithLabel:@"Go" symbol:nil style:GCButtonStylePrimary target:self action:@selector(searchLocus:)];
    goButton.fixedHeight = 34;
    GCButton *zoomOut = [self toolButton:@"minus" tip:@"Zoom out" action:@selector(zoomOut:)];
    GCButton *zoomIn = [self toolButton:@"plus" tip:@"Zoom in" action:@selector(zoomIn:)];
    zoomIn.identifier = @"ZoomInButton";
    self.highlightButton = [self toolButton:@"highlighter" tip:@"Drag across a track to highlight an interval" action:@selector(toggleHighlight:)];
    [self.highlightButton setButtonType:NSButtonTypePushOnPushOff];
    self.highlightButton.showsState = YES;
    self.highlightColorWell = [[NSColorWell alloc] initWithFrame:NSZeroRect];
    self.highlightColorWell.translatesAutoresizingMaskIntoConstraints = NO;
    self.highlightColorWell.colorWellStyle = NSColorWellStyleMinimal;
    self.highlightColorWell.color = GCHex(0xf2c94c, 1);
    self.highlightColorWell.target = self;
    self.highlightColorWell.action = @selector(changeHighlightColor:);
    self.highlightColorWell.toolTip = @"Highlight color";
    self.clearHighlightsButton = [self toolButton:@"eraser" tip:@"Clear highlights" action:@selector(clearHighlights:)];
    self.clearHighlightsButton.enabled = NO;
    GCButton *exportButton = [self toolButton:@"photo" tip:@"Export PNG" action:@selector(exportPNG:)];
    NSView *spacer = [[NSView alloc] initWithFrame:NSZeroRect];
    [spacer setContentHuggingPriority:1 forOrientation:NSLayoutConstraintOrientationHorizontal];
    GCButton *favorites = [self iconButton:@"star" tip:@"Favorite profiles" action:@selector(openFavorites:)];
    self.themeButton = [self iconButton:@"moon" tip:@"Switch to dark theme" action:@selector(toggleAppearance:)];
    self.reloadButton = [self iconButton:@"arrow.clockwise" tip:@"Reload from server" action:@selector(reloadOrStop:)];
    GCButton *share = [GCButton buttonWithLabel:@"Share" symbol:@"square.and.arrow.up" style:GCButtonStylePrimary target:self action:@selector(shareView:)];
    share.toolTip = @"Create and copy share link";
    share.fixedHeight = 34;
    for (GCButton *button in @[favorites, self.themeButton, self.reloadButton]) button.fixedHeight = 34;

    NSStackView *controls = [NSStackView stackViewWithViews:@[
        genomeField, locusField, goButton, zoomOut, zoomIn, [self verticalRule],
        self.highlightButton, self.highlightColorWell, self.clearHighlightsButton, exportButton,
        spacer, favorites, self.themeButton, self.reloadButton, share
    ]];
    controls.translatesAutoresizingMaskIntoConstraints = NO;
    controls.orientation = NSUserInterfaceLayoutOrientationHorizontal;
    controls.alignment = NSLayoutAttributeCenterY;
    controls.spacing = 6;
    [controls setCustomSpacing:2 afterView:zoomOut];
    [controls setCustomSpacing:10 afterView:goButton];
    [controls setCustomSpacing:2 afterView:self.reloadButton];
    [controls setCustomSpacing:8 afterView:self.themeButton];
    [toolbar addSubview:controls];

    GCFillView *progressTrack = [[GCFillView alloc] initWithFrame:NSZeroRect];
    [content addSubview:progressTrack];
    self.progressBar = [[GCFillView alloc] initWithFrame:NSZeroRect];
    self.progressBar.fillColor = GCAccentColor();
    self.progressBar.hidden = YES;
    [progressTrack addSubview:self.progressBar];
    self.progressWidthConstraint = [self.progressBar.widthAnchor constraintEqualToConstant:0];

    WKWebViewConfiguration *configuration = [[WKWebViewConfiguration alloc] init];
    configuration.websiteDataStore = [WKWebsiteDataStore defaultDataStore];
    configuration.preferences.javaScriptCanOpenWindowsAutomatically = YES;
    [configuration.userContentController addScriptMessageHandler:self name:@"genomeCanvas"];
    self.webView = [[WKWebView alloc] initWithFrame:NSZeroRect configuration:configuration];
    self.webView.translatesAutoresizingMaskIntoConstraints = NO;
    self.webView.navigationDelegate = self;
    self.webView.UIDelegate = self;
    self.webView.allowsMagnification = YES;
    self.webView.underPageBackgroundColor = GCCanvasColor();

    // The canvas sits on the page as an inset sheet, like the web .genome-stage.
    GCFillView *canvasCard = [[GCFillView alloc] initWithFrame:NSZeroRect];
    canvasCard.fillColor = GCCanvasColor();
    canvasCard.strokeColor = GCBorderColor();
    canvasCard.cornerRadius = 13;
    canvasCard.clipsContent = YES;
    [canvasCard addSubview:self.webView];
    [content addSubview:canvasCard];

    [NSLayoutConstraint activateConstraints:@[
        [toolbar.topAnchor constraintEqualToAnchor:content.topAnchor],
        [toolbar.leadingAnchor constraintEqualToAnchor:content.leadingAnchor],
        [toolbar.trailingAnchor constraintEqualToAnchor:content.trailingAnchor],
        [toolbar.heightAnchor constraintEqualToConstant:56],
        [toolbarRule.topAnchor constraintEqualToAnchor:toolbar.bottomAnchor],
        [toolbarRule.leadingAnchor constraintEqualToAnchor:content.leadingAnchor],
        [toolbarRule.trailingAnchor constraintEqualToAnchor:content.trailingAnchor],
        [toolbarRule.heightAnchor constraintEqualToConstant:1],
        [controls.leadingAnchor constraintEqualToAnchor:toolbar.leadingAnchor constant:12],
        [controls.trailingAnchor constraintEqualToAnchor:toolbar.trailingAnchor constant:-12],
        [controls.centerYAnchor constraintEqualToAnchor:toolbar.centerYAnchor],
        [genomeField.widthAnchor constraintEqualToConstant:196],
        [locusField.widthAnchor constraintGreaterThanOrEqualToConstant:190],
        [locusField.widthAnchor constraintLessThanOrEqualToConstant:520],
        [self.highlightColorWell.widthAnchor constraintEqualToConstant:38],
        [self.highlightColorWell.heightAnchor constraintEqualToConstant:30],
        [progressTrack.topAnchor constraintEqualToAnchor:toolbarRule.bottomAnchor],
        [progressTrack.leadingAnchor constraintEqualToAnchor:content.leadingAnchor],
        [progressTrack.trailingAnchor constraintEqualToAnchor:content.trailingAnchor],
        [progressTrack.heightAnchor constraintEqualToConstant:2],
        [self.progressBar.leadingAnchor constraintEqualToAnchor:progressTrack.leadingAnchor],
        [self.progressBar.topAnchor constraintEqualToAnchor:progressTrack.topAnchor],
        [self.progressBar.bottomAnchor constraintEqualToAnchor:progressTrack.bottomAnchor],
        self.progressWidthConstraint,
        [canvasCard.topAnchor constraintEqualToAnchor:progressTrack.bottomAnchor constant:8],
        [canvasCard.leadingAnchor constraintEqualToAnchor:content.leadingAnchor constant:10],
        [canvasCard.trailingAnchor constraintEqualToAnchor:content.trailingAnchor constant:-10],
        [canvasCard.bottomAnchor constraintEqualToAnchor:content.bottomAnchor constant:-10],
        [self.webView.topAnchor constraintEqualToAnchor:canvasCard.topAnchor constant:1],
        [self.webView.leadingAnchor constraintEqualToAnchor:canvasCard.leadingAnchor constant:1],
        [self.webView.trailingAnchor constraintEqualToAnchor:canvasCard.trailingAnchor constant:-1],
        [self.webView.bottomAnchor constraintEqualToAnchor:canvasCard.bottomAnchor constant:-1],
    ]];
    return content;
}

#pragma mark - Control factories

- (NSTextField *)sectionLabel:(NSString *)text {
    NSTextField *label = [NSTextField labelWithString:@""];
    label.translatesAutoresizingMaskIntoConstraints = NO;
    label.attributedStringValue = [[NSAttributedString alloc] initWithString:text.uppercaseString attributes:@{
        NSFontAttributeName: GCUIFont(11, 650),
        NSForegroundColorAttributeName: GCText3Color(),
        NSKernAttributeName: @0.7,
    }];
    return label;
}

- (GCFillView *)fieldContainerWithContent:(NSView *)contentView inset:(CGFloat)inset {
    GCFillView *field = [[GCFillView alloc] initWithFrame:NSZeroRect];
    field.fillColor = GCSurfaceColor();
    field.strokeColor = GCBorderStrongColor();
    field.cornerRadius = 9;
    contentView.translatesAutoresizingMaskIntoConstraints = NO;
    [field addSubview:contentView];
    [NSLayoutConstraint activateConstraints:@[
        [field.heightAnchor constraintEqualToConstant:34],
        [contentView.leadingAnchor constraintEqualToAnchor:field.leadingAnchor constant:inset],
        [contentView.trailingAnchor constraintEqualToAnchor:field.trailingAnchor constant:-inset],
        [contentView.centerYAnchor constraintEqualToAnchor:field.centerYAnchor],
    ]];
    return field;
}

- (NSView *)dividerLine {
    GCFillView *line = [[GCFillView alloc] initWithFrame:NSZeroRect];
    line.fillColor = GCBorderColor();
    [line.heightAnchor constraintEqualToConstant:1].active = YES;
    return line;
}

- (NSView *)verticalRule {
    GCFillView *line = [[GCFillView alloc] initWithFrame:NSZeroRect];
    line.fillColor = GCBorderColor();
    [line.widthAnchor constraintEqualToConstant:1].active = YES;
    [line.heightAnchor constraintEqualToConstant:22].active = YES;
    return line;
}

- (GCButton *)iconButton:(NSString *)symbol tip:(NSString *)tip action:(SEL)action {
    GCButton *button = [GCButton buttonWithLabel:nil symbol:symbol style:GCButtonStyleGhost target:self action:action];
    button.toolTip = tip;
    button.fixedHeight = 30;
    return button;
}

- (GCButton *)toolButton:(NSString *)symbol tip:(NSString *)tip action:(SEL)action {
    GCButton *button = [GCButton buttonWithLabel:nil symbol:symbol style:GCButtonStyleTool target:self action:action];
    button.toolTip = tip;
    button.fixedHeight = 32;
    return button;
}

- (void)updateTrackCount {
    NSMutableAttributedString *text = [[NSMutableAttributedString alloc] initWithString:[NSString stringWithFormat:@"%lu", (unsigned long)self.tracks.count] attributes:@{
        NSFontAttributeName: GCHeadingFont(15), NSForegroundColorAttributeName: GCAccentTextColor()
    }];
    [text appendAttributedString:[[NSAttributedString alloc] initWithString:@" visible" attributes:@{
        NSFontAttributeName: GCHeadingFont(15), NSForegroundColorAttributeName: GCTextColor()
    }]];
    self.trackCountLabel.attributedStringValue = text;
}

#pragma mark - Appearance (System / Light / Dark)

- (NSString *)appearancePreference {
    NSString *value = [[NSUserDefaults standardUserDefaults] stringForKey:GCAppearanceDefaultsKey];
    return [@[@"light", @"dark"] containsObject:value] ? value : @"system";
}

- (void)setAppearancePreference:(NSString *)preference {
    NSUserDefaults *defaults = [NSUserDefaults standardUserDefaults];
    if ([preference isEqualToString:@"light"] || [preference isEqualToString:@"dark"]) [defaults setObject:preference forKey:GCAppearanceDefaultsKey];
    else [defaults removeObjectForKey:GCAppearanceDefaultsKey];
    [self applyAppearancePreference];
}

- (void)applyAppearancePreference {
    NSString *preference = [self appearancePreference];
    if ([preference isEqualToString:@"light"]) NSApp.appearance = [NSAppearance appearanceNamed:NSAppearanceNameAqua];
    else if ([preference isEqualToString:@"dark"]) NSApp.appearance = [NSAppearance appearanceNamed:NSAppearanceNameDarkAqua];
    else NSApp.appearance = nil;
    [self appearanceDidChange];
}

- (BOOL)systemPrefersDark {
    return [[[NSUserDefaults standardUserDefaults] stringForKey:@"AppleInterfaceStyle"] isEqualToString:@"Dark"];
}

- (void)appearanceDidChange {
    BOOL dark = GCAppearanceIsDark(NSApp.effectiveAppearance);
    self.themeButton.image = GCSymbol(dark ? @"sun.max" : @"moon", 13, @"Switch theme");
    self.themeButton.toolTip = dark ? @"Switch to light theme" : @"Switch to dark theme";
    [self.themeButton refreshColors];
    NSString *preference = [self appearancePreference];
    for (NSMenuItem *item in self.appearanceMenuItems) {
        item.state = [item.representedObject isEqualToString:preference] ? NSControlStateValueOn : NSControlStateValueOff;
    }
    // The web canvas mirrors the native appearance (its own toggle is hidden in desktop mode).
    [self callDesktopMethod:@"setTheme" arguments:@[dark ? @"dark" : @"light"]];
}

- (void)toggleAppearance:(id)sender {
    NSString *next = GCAppearanceIsDark(NSApp.effectiveAppearance) ? @"light" : @"dark";
    // Choosing the theme the system already uses returns to following the system.
    BOOL matchesSystem = [next isEqualToString:@"dark"] == [self systemPrefersDark];
    [self setAppearancePreference:matchesSystem ? @"system" : next];
}

- (void)chooseAppearance:(NSMenuItem *)sender { [self setAppearancePreference:sender.representedObject]; }

#pragma mark - Preview snapshots (CI only)

- (NSString *)snapshotDirectory { return [NSProcessInfo processInfo].environment[@"GENOME_CANVAS_SNAPSHOT_DIR"]; }

- (void)scheduleSnapshotCaptureIfRequested {
    NSString *directory = [self snapshotDirectory];
    if (!directory.length || self.snapshotScheduled) return;
    self.snapshotScheduled = YES;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(12 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
        // Click a momentary tool button first: it must not look selected afterwards.
        [(NSButton *)GCFindView(self.window.contentView, @"ZoomInButton") performClick:nil];
        [self captureThemes:@[@"light", @"dark"] directory:directory];
    });
}

- (void)captureThemes:(NSArray<NSString *> *)themes directory:(NSString *)directory {
    if (!themes.count) { [NSApp terminate:nil]; return; }
    [self setAppearancePreference:themes.firstObject];
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(3 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
        NSString *path = [directory stringByAppendingPathComponent:[NSString stringWithFormat:@"window-%@.png", themes.firstObject]];
        [self captureWindowToPath:path completion:^{
            [self captureThemes:[themes subarrayWithRange:NSMakeRange(1, themes.count - 1)] directory:directory];
        }];
    });
}

- (void)captureWindowToPath:(NSString *)path completion:(void (^)(void))completion {
    NSView *root = self.window.contentView;
    NSRect bounds = root.bounds;
    NSBitmapImageRep *chrome = [root bitmapImageRepForCachingDisplayInRect:bounds];
    [root cacheDisplayInRect:bounds toBitmapImageRep:chrome];
    NSRect webRect = [self.webView convertRect:self.webView.bounds toView:nil];
    [self.webView takeSnapshotWithConfiguration:nil completionHandler:^(NSImage *webImage, NSError *error) {
        NSImage *image = [NSImage imageWithSize:bounds.size flipped:NO drawingHandler:^BOOL(NSRect rect) {
            [chrome drawInRect:rect];
            if (webImage) [webImage drawInRect:webRect];
            return YES;
        }];
        NSRect proposed = NSMakeRect(0, 0, bounds.size.width, bounds.size.height);
        CGImageRef cgImage = [image CGImageForProposedRect:&proposed context:nil hints:nil];
        if (cgImage) {
            NSBitmapImageRep *output = [[NSBitmapImageRep alloc] initWithCGImage:cgImage];
            [[output representationUsingType:NSBitmapImageFileTypePNG properties:@{}] writeToFile:path atomically:YES];
        }
        completion();
    }];
}

- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender { return YES; }

- (void)applicationWillTerminate:(NSNotification *)notification {
    [self stopLocalBackend];
    [self.webView.configuration.userContentController removeScriptMessageHandlerForName:@"genomeCanvas"];
}

- (void)dealloc {
    @try { [self.webView removeObserver:self forKeyPath:@"estimatedProgress"]; }
    @catch (__unused NSException *exception) {}
    @try { [self.webView removeObserver:self forKeyPath:@"loading"]; }
    @catch (__unused NSException *exception) {}
    @try { [NSApp removeObserver:self forKeyPath:@"effectiveAppearance"]; }
    @catch (__unused NSException *exception) {}
}

- (void)buildApplicationMenu {
    NSMenu *mainMenu = [[NSMenu alloc] initWithTitle:@""];
    NSMenuItem *applicationItem = [[NSMenuItem alloc] initWithTitle:@"" action:nil keyEquivalent:@""];
    NSMenu *applicationMenu = [[NSMenu alloc] initWithTitle:@"Genome Canvas"];
    [applicationMenu addItemWithTitle:@"About Genome Canvas" action:@selector(orderFrontStandardAboutPanel:) keyEquivalent:@""];
    [applicationMenu addItem:[NSMenuItem separatorItem]];
    self.localBackendMenuItem = [[NSMenuItem alloc] initWithTitle:@"Use Local Backend" action:@selector(useLocalBackend:) keyEquivalent:@""];
    self.localBackendMenuItem.target = self;
    [applicationMenu addItem:self.localBackendMenuItem];
    self.remoteServerMenuItem = [[NSMenuItem alloc] initWithTitle:@"Connect to Server…" action:@selector(editServerAddress:) keyEquivalent:@","];
    self.remoteServerMenuItem.target = self;
    [applicationMenu addItem:self.remoteServerMenuItem];
    [applicationMenu addItem:[NSMenuItem separatorItem]];
    [applicationMenu addItemWithTitle:@"Hide Genome Canvas" action:@selector(hide:) keyEquivalent:@"h"];
    [applicationMenu addItemWithTitle:@"Quit Genome Canvas" action:@selector(terminate:) keyEquivalent:@"q"];
    applicationItem.submenu = applicationMenu;
    [mainMenu addItem:applicationItem];

    NSMenuItem *fileItem = [[NSMenuItem alloc] initWithTitle:@"" action:nil keyEquivalent:@""];
    NSMenu *fileMenu = [[NSMenu alloc] initWithTitle:@"File"];
    self.openFilesMenuItem = [[NSMenuItem alloc] initWithTitle:@"Open Server Files…" action:@selector(openServerFiles:) keyEquivalent:@"o"];
    self.openFilesMenuItem.target = self;
    [fileMenu addItem:self.openFilesMenuItem];
    NSMenuItem *publicData = [[NSMenuItem alloc] initWithTitle:@"Connect Public Data…" action:@selector(openPublicData:) keyEquivalent:@"d"];
    publicData.target = self;
    [fileMenu addItem:publicData];
    [fileMenu addItem:[NSMenuItem separatorItem]];
    NSMenuItem *export = [[NSMenuItem alloc] initWithTitle:@"Export PNG…" action:@selector(exportPNG:) keyEquivalent:@"e"];
    export.target = self;
    export.keyEquivalentModifierMask = NSEventModifierFlagCommand | NSEventModifierFlagShift;
    [fileMenu addItem:export];
    fileItem.submenu = fileMenu;
    [mainMenu addItem:fileItem];

    NSMenuItem *editItem = [[NSMenuItem alloc] initWithTitle:@"" action:nil keyEquivalent:@""];
    NSMenu *editMenu = [[NSMenu alloc] initWithTitle:@"Edit"];
    [editMenu addItemWithTitle:@"Cut" action:@selector(cut:) keyEquivalent:@"x"];
    [editMenu addItemWithTitle:@"Copy" action:@selector(copy:) keyEquivalent:@"c"];
    [editMenu addItemWithTitle:@"Paste" action:@selector(paste:) keyEquivalent:@"v"];
    [editMenu addItemWithTitle:@"Select All" action:@selector(selectAll:) keyEquivalent:@"a"];
    [editMenu addItem:[NSMenuItem separatorItem]];
    NSMenuItem *removeTrack = [[NSMenuItem alloc] initWithTitle:@"Remove Selected Track" action:@selector(removeSelectedTrack:)
        keyEquivalent:[NSString stringWithFormat:@"%C", (unichar)NSBackspaceCharacter]];
    removeTrack.target = self;
    [editMenu addItem:removeTrack];
    editItem.submenu = editMenu;
    [mainMenu addItem:editItem];

    NSMenuItem *viewItem = [[NSMenuItem alloc] initWithTitle:@"" action:nil keyEquivalent:@""];
    NSMenu *viewMenu = [[NSMenu alloc] initWithTitle:@"View"];
    NSMenuItem *favorites = [[NSMenuItem alloc] initWithTitle:@"Favorites…" action:@selector(openFavorites:) keyEquivalent:@"s"];
    favorites.target = self;
    favorites.keyEquivalentModifierMask = NSEventModifierFlagCommand | NSEventModifierFlagShift;
    [viewMenu addItem:favorites];
    NSMenuItem *reload = [[NSMenuItem alloc] initWithTitle:@"Reload Genome Canvas" action:@selector(reloadOrStop:) keyEquivalent:@"r"];
    reload.target = self;
    [viewMenu addItem:reload];
    [viewMenu addItem:[NSMenuItem separatorItem]];
    NSMenuItem *appearanceItem = [[NSMenuItem alloc] initWithTitle:@"Appearance" action:nil keyEquivalent:@""];
    NSMenu *appearanceMenu = [[NSMenu alloc] initWithTitle:@"Appearance"];
    NSMutableArray<NSMenuItem *> *appearanceItems = [NSMutableArray array];
    for (NSArray<NSString *> *entry in @[@[@"Use System Setting", @"system"], @[@"Light", @"light"], @[@"Dark", @"dark"]]) {
        NSMenuItem *item = [[NSMenuItem alloc] initWithTitle:entry[0] action:@selector(chooseAppearance:) keyEquivalent:@""];
        item.target = self;
        item.representedObject = entry[1];
        [appearanceMenu addItem:item];
        [appearanceItems addObject:item];
    }
    NSMenuItem *toggleTheme = [[NSMenuItem alloc] initWithTitle:@"Toggle Light/Dark" action:@selector(toggleAppearance:) keyEquivalent:@"t"];
    toggleTheme.target = self;
    toggleTheme.keyEquivalentModifierMask = NSEventModifierFlagCommand | NSEventModifierFlagShift;
    [appearanceMenu addItem:[NSMenuItem separatorItem]];
    [appearanceMenu addItem:toggleTheme];
    self.appearanceMenuItems = appearanceItems;
    appearanceItem.submenu = appearanceMenu;
    [viewMenu addItem:appearanceItem];
    viewItem.submenu = viewMenu;
    [mainMenu addItem:viewItem];
    [NSApp setMainMenu:mainMenu];
}

- (void)updateConnectionUI {
    self.localModeButton.state = self.localMode ? NSControlStateValueOn : NSControlStateValueOff;
    self.serverModeButton.state = self.localMode ? NSControlStateValueOff : NSControlStateValueOn;
    self.workspaceLabel.hidden = self.localMode;
    self.workspaceControls.hidden = self.localMode;
    self.filesButton.titleLabel.stringValue = self.localMode ? @"Open local files" : @"Open server files";
    self.filesButton.detailLabel.stringValue = self.localMode ? @"Browse files on this Mac without uploading" : @"Browse data directories without uploading";
    self.filesButton.accessibilityLabel = self.filesButton.titleLabel.stringValue;
    self.openFilesMenuItem.title = self.localMode ? @"Open Local Files…" : @"Open Server Files…";
    self.localBackendMenuItem.state = self.localMode ? NSControlStateValueOn : NSControlStateValueOff;
    self.remoteServerMenuItem.state = self.localMode ? NSControlStateValueOff : NSControlStateValueOn;
    self.window.subtitle = self.localMode ? @"Local genomics workbench" : @"Server-connected genomics workbench";
}

- (NSString *)localPythonPath {
    NSFileManager *manager = [NSFileManager defaultManager];
    for (NSString *candidate in @[@"/usr/bin/python3", @"/opt/homebrew/bin/python3", @"/usr/local/bin/python3"]) {
        if ([manager isExecutableFileAtPath:candidate]) return candidate;
    }
    return nil;
}

- (NSURL *)localApplicationSupportURL:(NSError **)error {
    NSURL *base = [[[NSFileManager defaultManager] URLsForDirectory:NSApplicationSupportDirectory inDomains:NSUserDomainMask] firstObject];
    NSURL *directory = [base URLByAppendingPathComponent:@"Genome Canvas" isDirectory:YES];
    if (![[NSFileManager defaultManager] createDirectoryAtURL:directory withIntermediateDirectories:YES attributes:nil error:error]) return nil;
    return directory;
}

- (void)showLocalBackendError:(NSString *)message {
    [self setStatus:@"Local backend unavailable" state:@"error"];
    NSAlert *alert = [[NSAlert alloc] init];
    alert.alertStyle = NSAlertStyleCritical;
    alert.messageText = @"Local Backend Could Not Start";
    alert.informativeText = message ?: @"The bundled local backend could not be started.";
    [alert addButtonWithTitle:@"OK"];
    [alert beginSheetModalForWindow:self.window completionHandler:nil];
}

- (void)startLocalBackend {
    if (self.localServerTask.running) {
        if (self.localServerAddress.length) [self loadServerAddress:self.localServerAddress remember:NO];
        return;
    }

    NSURL *scriptURL = [[NSBundle mainBundle] URLForResource:@"server" withExtension:@"py" subdirectory:@"LocalBackend"];
    NSString *pythonPath = [self localPythonPath];
    if (!scriptURL || !pythonPath) {
        [self showLocalBackendError:@"The local backend resources or Python 3 runtime were not found. Install Xcode Command Line Tools, or connect to a Genome Canvas server instead."];
        return;
    }

    NSError *directoryError = nil;
    NSURL *supportURL = [self localApplicationSupportURL:&directoryError];
    if (!supportURL) {
        [self showLocalBackendError:directoryError.localizedDescription];
        return;
    }
    NSURL *readyURL = [supportURL URLByAppendingPathComponent:@"local-backend-ready.json"];
    NSURL *logURL = [supportURL URLByAppendingPathComponent:@"local-backend.log"];
    NSFileManager *manager = [NSFileManager defaultManager];
    [manager removeItemAtURL:readyURL error:nil];
    [manager createFileAtPath:logURL.path contents:[NSData data] attributes:nil];
    self.localServerLogHandle = [NSFileHandle fileHandleForWritingAtPath:logURL.path];

    NSMutableDictionary<NSString *, NSString *> *environment = [[[NSProcessInfo processInfo] environment] mutableCopy];
    environment[@"GENOME_CANVAS_LOCAL_MODE"] = @"1";
    environment[@"GENOME_CANVAS_STATE_DIR"] = supportURL.path;
    environment[@"GENOME_DATA_ROOTS"] = @"/";
    environment[@"PYTHONUNBUFFERED"] = @"1";

    NSTask *task = [[NSTask alloc] init];
    task.executableURL = [NSURL fileURLWithPath:pythonPath];
    task.currentDirectoryURL = [scriptURL URLByDeletingLastPathComponent];
    task.arguments = @[scriptURL.path, @"--host", @"127.0.0.1", @"--port", @"0", @"--ready-file", readyURL.path];
    task.environment = environment;
    task.standardOutput = self.localServerLogHandle;
    task.standardError = self.localServerLogHandle;
    __weak typeof(self) weakSelf = self;
    task.terminationHandler = ^(NSTask *finishedTask) {
        dispatch_async(dispatch_get_main_queue(), ^{
            typeof(weakSelf) strongSelf = weakSelf;
            if (!strongSelf || strongSelf.localServerTask != finishedTask) return;
            strongSelf.localServerTask = nil;
            if (strongSelf.localMode) [strongSelf showLocalBackendError:[NSString stringWithFormat:@"The local backend stopped unexpectedly. See %@", logURL.path]];
        });
    };
    self.localServerTask = task;
    [self setStatus:@"Starting local backend" state:@"busy"];
    NSError *launchError = nil;
    if (![task launchAndReturnError:&launchError]) {
        self.localServerTask = nil;
        [self showLocalBackendError:launchError.localizedDescription];
        return;
    }
    [self waitForLocalBackendReadyAtURL:readyURL attempt:0];
}

- (void)waitForLocalBackendReadyAtURL:(NSURL *)readyURL attempt:(NSInteger)attempt {
    if (!self.localMode || !self.localServerTask.running) return;
    NSData *data = [NSData dataWithContentsOfURL:readyURL];
    NSDictionary *payload = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
    NSNumber *port = [payload isKindOfClass:[NSDictionary class]] ? payload[@"port"] : nil;
    if (port.integerValue > 0) {
        self.localServerAddress = [NSString stringWithFormat:@"http://127.0.0.1:%ld/?local=1", (long)port.integerValue];
        [self loadServerAddress:self.localServerAddress remember:NO];
        return;
    }
    if (attempt >= 100) {
        [self stopLocalBackend];
        [self showLocalBackendError:@"The local backend did not become ready within 10 seconds. See ~/Library/Application Support/Genome Canvas/local-backend.log."];
        return;
    }
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.1 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
        [self waitForLocalBackendReadyAtURL:readyURL attempt:attempt + 1];
    });
}

- (void)stopLocalBackend {
    NSTask *task = self.localServerTask;
    self.localServerTask = nil;
    self.localServerAddress = nil;
    if (task.running) [task terminate];
    [self.localServerLogHandle closeFile];
    self.localServerLogHandle = nil;
}

- (void)activateLocalBackend {
    self.localMode = YES;
    [self updateConnectionUI];
    [self startLocalBackend];
}

- (void)connectToRemoteServer:(NSString *)address {
    if (![self normalizedURL:address]) {
        [self setStatus:@"Invalid server address" state:@"error"];
        [self updateConnectionUI];
        NSBeep();
        return;
    }
    [self stopLocalBackend];
    self.localMode = NO;
    [self updateConnectionUI];
    [self loadServerAddress:address remember:YES];
}

- (void)selectLocalMode:(id)sender { [self updateConnectionUI]; [self activateLocalBackend]; }

- (void)selectServerMode:(id)sender {
    [self updateConnectionUI];
    [self editServerAddress:sender];
}

- (void)useLocalBackend:(id)sender { [self activateLocalBackend]; }

- (NSURL *)normalizedURL:(NSString *)value {
    NSString *candidate = [value stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if (!candidate.length) return nil;
    if ([candidate rangeOfString:@"://"].location == NSNotFound) candidate = [@"http://" stringByAppendingString:candidate];
    NSURLComponents *components = [NSURLComponents componentsWithString:candidate];
    NSString *scheme = components.scheme.lowercaseString;
    if (!components.host.length || !([scheme isEqualToString:@"http"] || [scheme isEqualToString:@"https"])) return nil;
    // A bare LAN server address defaults to the gateway path; the bundled
    // loopback backend serves the app at its root.
    BOOL loopback = [@[@"127.0.0.1", @"localhost", @"::1"] containsObject:components.host.lowercaseString];
    if (!components.path.length || [components.path isEqualToString:@"/"]) components.path = loopback ? @"/" : @"/genome-canvas/";
    else if (![components.path hasSuffix:@"/"]) components.path = [components.path stringByAppendingString:@"/"];
    NSMutableArray<NSURLQueryItem *> *items = [NSMutableArray array];
    for (NSURLQueryItem *item in components.queryItems ?: @[]) {
        if (![item.name isEqualToString:@"desktop"]) [items addObject:item];
    }
    [items addObject:[NSURLQueryItem queryItemWithName:@"desktop" value:@"1"]];
    components.queryItems = items;
    return components.URL;
}

- (NSString *)storableAddressFromURL:(NSURL *)url {
    NSURLComponents *components = [NSURLComponents componentsWithURL:url resolvingAgainstBaseURL:NO];
    NSMutableArray<NSURLQueryItem *> *items = [NSMutableArray array];
    for (NSURLQueryItem *item in components.queryItems ?: @[]) if (![item.name isEqualToString:@"desktop"]) [items addObject:item];
    components.queryItems = items.count ? items : nil;
    return components.URL.absoluteString;
}

- (void)loadServerAddress:(NSString *)value remember:(BOOL)remember {
    NSURL *url = [self normalizedURL:value];
    if (!url) { [self setStatus:@"Invalid server address" state:@"error"]; NSBeep(); return; }
    if (remember) {
        self.serverAddress = [self storableAddressFromURL:url];
        [[NSUserDefaults standardUserDefaults] setObject:self.serverAddress forKey:GCServerDefaultsKey];
    }
    [self setStatus:@"Connecting" state:@"busy"];
    NSURLRequest *request = [NSURLRequest requestWithURL:url cachePolicy:NSURLRequestReloadRevalidatingCacheData timeoutInterval:30];
    [self.webView loadRequest:request];
}

- (void)setStatus:(NSString *)text state:(NSString *)state {
    self.statusLabel.stringValue = text.length ? text : @"Ready";
    if ([state isEqualToString:@"error"]) self.statusDot.fillColor = GCDangerColor();
    else if ([state isEqualToString:@"busy"]) self.statusDot.fillColor = GCWarningColor();
    else self.statusDot.fillColor = GCSuccessColor();
}

- (NSString *)JSONFragment:(id)value {
    NSData *data = [NSJSONSerialization dataWithJSONObject:value ?: [NSNull null] options:NSJSONWritingFragmentsAllowed error:nil];
    return data ? [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding] : @"null";
}

- (void)callDesktopMethod:(NSString *)method arguments:(NSArray *)arguments {
    NSMutableArray<NSString *> *encoded = [NSMutableArray arrayWithCapacity:arguments.count];
    for (id argument in arguments) [encoded addObject:[self JSONFragment:argument]];
    NSString *script = [NSString stringWithFormat:@"(function(){const b=window.GenomeCanvasDesktop;return b&&b[%@](%@);})()", [self JSONFragment:method], [encoded componentsJoinedByString:@","]];
    [self.webView evaluateJavaScript:script completionHandler:^(id result, NSError *error) {
        if (error && error.code != WKErrorJavaScriptResultTypeIsUnsupported) [self setStatus:@"Desktop command failed" state:@"error"];
    }];
}

- (void)requestSnapshot {
    [self.webView evaluateJavaScript:@"window.GenomeCanvasDesktop && window.GenomeCanvasDesktop.snapshot()" completionHandler:^(id result, NSError *error) {
        if ([result isKindOfClass:[NSDictionary class]]) [self applySnapshot:(NSDictionary *)result];
    }];
}

- (void)userContentController:(WKUserContentController *)userContentController didReceiveScriptMessage:(WKScriptMessage *)message {
    if (![message.name isEqualToString:@"genomeCanvas"] || ![message.body isKindOfClass:[NSDictionary class]]) return;
    NSDictionary *payload = (NSDictionary *)message.body;
    if ([payload[@"type"] isEqualToString:@"share"]) {
        NSString *url = payload[@"url"];
        if (url.length) {
            NSPasteboard *pasteboard = [NSPasteboard generalPasteboard];
            [pasteboard clearContents];
            [pasteboard setString:url forType:NSPasteboardTypeString];
            [self setStatus:@"Share link copied" state:@"ready"];
            NSAlert *alert = [[NSAlert alloc] init];
            alert.messageText = @"Share Link Copied";
            alert.informativeText = @"The LAN share link is ready on the clipboard.";
            [alert addButtonWithTitle:@"OK"];
            [alert beginSheetModalForWindow:self.window completionHandler:nil];
        }
        return;
    }
    [self applySnapshot:payload];
}

- (void)applySnapshot:(NSDictionary *)snapshot {
    NSArray *workspaceRecords = [snapshot[@"workspaces"] isKindOfClass:[NSArray class]] ? snapshot[@"workspaces"] : @[];
    if (![workspaceRecords isEqualToArray:self.workspaces]) {
        self.workspaces = workspaceRecords;
        [self.workspacePopup removeAllItems];
        if (!workspaceRecords.count) [self.workspacePopup addItemWithTitle:@"Choose Workspace…"];
        for (NSDictionary *workspace in workspaceRecords) {
            [self.workspacePopup addItemWithTitle:workspace[@"name"] ?: @"Workspace"];
            self.workspacePopup.lastItem.representedObject = workspace[@"id"];
        }
    }
    NSString *workspaceId = [snapshot[@"workspace"] isKindOfClass:[NSDictionary class]] ? snapshot[@"workspace"][@"id"] : nil;
    NSString *workspaceKind = [snapshot[@"workspace"] isKindOfClass:[NSDictionary class]] ? snapshot[@"workspace"][@"kind"] : nil;
    for (NSMenuItem *item in self.workspacePopup.itemArray) {
        if (workspaceId.length && [item.representedObject isEqual:workspaceId]) { [self.workspacePopup selectItem:item]; break; }
    }
    self.deleteWorkspaceButton.enabled = [workspaceKind isEqualToString:@"manual"];

    NSString *genome = [snapshot[@"genome"] isKindOfClass:[NSString class]] ? snapshot[@"genome"] : nil;
    if (genome.length) {
        NSMenuItem *found = nil;
        for (NSMenuItem *item in self.genomePopup.itemArray) if ([item.representedObject isEqual:genome]) { found = item; break; }
        if (!found) {
            [self.genomePopup addItemWithTitle:[NSString stringWithFormat:@"Custom · %@", genome]];
            self.genomePopup.lastItem.representedObject = genome;
            found = self.genomePopup.lastItem;
        }
        [self.genomePopup selectItem:found];
    }

    NSText *fieldEditor = [self.window fieldEditor:NO forObject:self.locusField];
    NSString *locus = [snapshot[@"locus"] isKindOfClass:[NSString class]] ? snapshot[@"locus"] : @"";
    if (locus.length && self.window.firstResponder != fieldEditor) self.locusField.stringValue = locus;

    NSArray *trackRecords = [snapshot[@"tracks"] isKindOfClass:[NSArray class]] ? snapshot[@"tracks"] : @[];
    if (![trackRecords isEqualToArray:self.tracks]) {
        self.tracks = trackRecords;
        [self.trackTable reloadData];
    }
    [self updateTrackCount];
    self.highlightButton.state = [snapshot[@"highlightMode"] boolValue] ? NSControlStateValueOn : NSControlStateValueOff;
    self.clearHighlightsButton.enabled = [snapshot[@"highlightCount"] integerValue] > 0;
    NSString *color = snapshot[@"highlightColor"];
    if ([color isKindOfClass:[NSString class]]) self.highlightColorWell.color = [self colorFromHex:color];
    NSString *status = [snapshot[@"status"] isKindOfClass:[NSString class]] ? snapshot[@"status"] : @"Ready";
    NSString *mode = [snapshot[@"statusMode"] isKindOfClass:[NSString class]] ? snapshot[@"statusMode"] : @"ready";
    [self setStatus:status state:mode];
}

- (NSColor *)colorFromHex:(NSString *)hex {
    double red = 0, green = 0, blue = 0;
    if ([hex hasPrefix:@"rgb"] && sscanf(hex.UTF8String, "rgb(%lf, %lf, %lf", &red, &green, &blue) == 3) {
        return [NSColor colorWithSRGBRed:red / 255.0 green:green / 255.0 blue:blue / 255.0 alpha:1.0];
    }
    NSString *value = [hex stringByTrimmingCharactersInSet:[NSCharacterSet characterSetWithCharactersInString:@"#"]];
    unsigned int rgb = 0;
    if (value.length != 6 || ![[NSScanner scannerWithString:value] scanHexInt:&rgb]) return [NSColor systemBlueColor];
    return [NSColor colorWithSRGBRed:((rgb >> 16) & 0xff) / 255.0 green:((rgb >> 8) & 0xff) / 255.0 blue:(rgb & 0xff) / 255.0 alpha:1.0];
}

- (NSString *)hexFromColor:(NSColor *)color {
    NSColor *rgb = [color colorUsingColorSpace:[NSColorSpace sRGBColorSpace]] ?: color;
    return [NSString stringWithFormat:@"#%02X%02X%02X", (int)round(rgb.redComponent * 255), (int)round(rgb.greenComponent * 255), (int)round(rgb.blueComponent * 255)];
}

- (NSInteger)numberOfRowsInTableView:(NSTableView *)tableView { return self.tracks.count; }

- (NSTableRowView *)tableView:(NSTableView *)tableView rowViewForRow:(NSInteger)row {
    GCTrackRowView *rowView = [tableView makeViewWithIdentifier:@"TrackRow" owner:self];
    if (!rowView) {
        rowView = [[GCTrackRowView alloc] initWithFrame:NSZeroRect];
        rowView.identifier = @"TrackRow";
    }
    return rowView;
}

- (NSView *)tableView:(NSTableView *)tableView viewForTableColumn:(NSTableColumn *)tableColumn row:(NSInteger)row {
    NSTableCellView *cell = [tableView makeViewWithIdentifier:@"TrackCell" owner:self];
    if (!cell) {
        cell = [[NSTableCellView alloc] initWithFrame:NSZeroRect];
        cell.identifier = @"TrackCell";
        GCFillView *swatch = [[GCFillView alloc] initWithFrame:NSZeroRect];
        swatch.cornerRadius = 2;
        swatch.identifier = @"TrackSwatch";
        NSTextField *name = [NSTextField labelWithString:@""];
        name.translatesAutoresizingMaskIntoConstraints = NO;
        name.font = GCUIFont(13, 550);
        name.textColor = GCTextColor();
        name.lineBreakMode = NSLineBreakByTruncatingTail;
        name.identifier = @"TrackName";
        [name setContentCompressionResistancePriority:NSLayoutPriorityDefaultLow forOrientation:NSLayoutConstraintOrientationHorizontal];
        NSTextField *meta = [NSTextField labelWithString:@""];
        meta.translatesAutoresizingMaskIntoConstraints = NO;
        meta.font = GCMonoFont(10.5, 550);
        meta.textColor = GCText3Color();
        meta.identifier = @"TrackMeta";
        GCButton *remove = [GCButton buttonWithLabel:nil symbol:@"xmark" style:GCButtonStyleGhost target:self action:@selector(removeTrackFromRow:)];
        remove.fixedHeight = 24;
        remove.identifier = @"TrackRemove";
        remove.toolTip = @"Remove track";
        remove.hidden = YES;
        [cell addSubview:swatch]; [cell addSubview:name]; [cell addSubview:meta]; [cell addSubview:remove];
        [NSLayoutConstraint activateConstraints:@[
            [swatch.leadingAnchor constraintEqualToAnchor:cell.leadingAnchor constant:10],
            [swatch.centerYAnchor constraintEqualToAnchor:cell.centerYAnchor],
            [swatch.widthAnchor constraintEqualToConstant:4], [swatch.heightAnchor constraintEqualToConstant:28],
            [name.leadingAnchor constraintEqualToAnchor:swatch.trailingAnchor constant:10],
            [name.trailingAnchor constraintLessThanOrEqualToAnchor:remove.leadingAnchor constant:-4],
            [name.topAnchor constraintEqualToAnchor:cell.topAnchor constant:6],
            [meta.leadingAnchor constraintEqualToAnchor:name.leadingAnchor],
            [meta.trailingAnchor constraintLessThanOrEqualToAnchor:remove.leadingAnchor constant:-4],
            [meta.topAnchor constraintEqualToAnchor:name.bottomAnchor constant:1],
            [remove.trailingAnchor constraintEqualToAnchor:cell.trailingAnchor constant:-6],
            [remove.centerYAnchor constraintEqualToAnchor:cell.centerYAnchor],
        ]];
    }
    NSDictionary *track = self.tracks[row];
    GCFillView *swatch = (GCFillView *)GCFindView(cell, @"TrackSwatch");
    NSTextField *nameField = (NSTextField *)GCFindView(cell, @"TrackName");
    NSTextField *metaField = (NSTextField *)GCFindView(cell, @"TrackMeta");
    swatch.fillColor = [self colorFromHex:track[@"color"] ?: @"#71808A"];
    nameField.stringValue = track[@"name"] ?: @"Untitled track";
    nameField.toolTip = nameField.stringValue;
    metaField.stringValue = [track[@"format"] ?: @"track" uppercaseString];
    return cell;
}

- (id<NSPasteboardWriting>)tableView:(NSTableView *)tableView pasteboardWriterForRow:(NSInteger)row {
    NSPasteboardItem *item = [[NSPasteboardItem alloc] init];
    [item setString:[NSString stringWithFormat:@"%ld", (long)row] forType:GCTrackRowPasteboardType];
    return item;
}

- (NSDragOperation)tableView:(NSTableView *)tableView validateDrop:(id<NSDraggingInfo>)info proposedRow:(NSInteger)row proposedDropOperation:(NSTableViewDropOperation)dropOperation {
    [tableView setDropRow:row dropOperation:NSTableViewDropAbove];
    return NSDragOperationMove;
}

- (BOOL)tableView:(NSTableView *)tableView acceptDrop:(id<NSDraggingInfo>)info row:(NSInteger)row dropOperation:(NSTableViewDropOperation)dropOperation {
    NSString *value = [info.draggingPasteboard stringForType:GCTrackRowPasteboardType];
    NSInteger source = value.integerValue;
    NSInteger target = row;
    if (source < target) target -= 1;
    target = MAX(0, MIN((NSInteger)self.tracks.count - 1, target));
    if (source < 0 || source >= (NSInteger)self.tracks.count || source == target) return NO;
    NSMutableArray *updated = [self.tracks mutableCopy];
    NSDictionary *moved = updated[source];
    [updated removeObjectAtIndex:source];
    [updated insertObject:moved atIndex:target];
    self.tracks = updated;
    [tableView reloadData];
    [tableView selectRowIndexes:[NSIndexSet indexSetWithIndex:target] byExtendingSelection:NO];
    [self callDesktopMethod:@"moveTrack" arguments:@[@(source), @(target)]];
    return YES;
}

- (BOOL)validateMenuItem:(NSMenuItem *)menuItem {
    if (menuItem.action == @selector(removeSelectedTrack:)) return self.trackTable.selectedRow >= 0;
    return YES;
}

- (void)removeTrackFromRow:(id)sender {
    NSInteger row = [self.trackTable rowForView:sender];
    if (row >= 0) [self callDesktopMethod:@"removeTrack" arguments:@[@(row)]];
}

- (void)refreshTracks:(id)sender { [self requestSnapshot]; }

- (NSDictionary *)selectedWorkspaceRecord {
    NSString *workspaceId = self.workspacePopup.selectedItem.representedObject;
    for (NSDictionary *workspace in self.workspaces) if ([workspace[@"id"] isEqual:workspaceId]) return workspace;
    return nil;
}

- (void)changeWorkspace:(id)sender {
    NSDictionary *workspace = [self selectedWorkspaceRecord];
    self.deleteWorkspaceButton.enabled = [workspace[@"kind"] isEqualToString:@"manual"];
    NSString *workspaceId = workspace[@"id"];
    if (workspaceId) [self callDesktopMethod:@"selectWorkspace" arguments:@[workspaceId]];
}

- (void)createWorkspace:(id)sender {
    NSAlert *alert = [[NSAlert alloc] init];
    alert.messageText = @"Create Temporary Project";
    alert.informativeText = @"This creates an independent workspace with its own Favorites and a default /nfs file location.";
    [alert addButtonWithTitle:@"Create and Enter"];
    [alert addButtonWithTitle:@"Cancel"];
    NSTextField *input = [NSTextField textFieldWithString:@""];
    input.placeholderString = @"Project name";
    input.frame = NSMakeRect(0, 0, 340, 26);
    alert.accessoryView = input;
    [alert beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse result) {
        NSString *name = [input.stringValue stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
        if (result == NSAlertFirstButtonReturn && name.length) [self callDesktopMethod:@"createWorkspace" arguments:@[name]];
    }];
}

- (void)deleteWorkspace:(id)sender {
    NSDictionary *workspace = [self selectedWorkspaceRecord];
    if (![workspace[@"kind"] isEqualToString:@"manual"]) { NSBeep(); return; }
    NSAlert *alert = [[NSAlert alloc] init];
    alert.alertStyle = NSAlertStyleWarning;
    alert.messageText = [NSString stringWithFormat:@"Delete project “%@”?", workspace[@"name"] ?: @"Project"];
    alert.informativeText = @"The project entry will be removed. Server data under /nfs and saved Favorite files are not deleted.";
    [alert addButtonWithTitle:@"Delete"];
    [alert addButtonWithTitle:@"Cancel"];
    [alert beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse result) {
        if (result == NSAlertFirstButtonReturn && workspace[@"id"]) [self callDesktopMethod:@"deleteWorkspace" arguments:@[workspace[@"id"]]];
    }];
}

- (void)manageWorkspaces:(id)sender { [self callDesktopMethod:@"openWorkspace" arguments:@[]]; }
- (void)changeGenome:(id)sender { id genome = self.genomePopup.selectedItem.representedObject; if (genome) [self callDesktopMethod:@"selectGenome" arguments:@[genome]]; }
- (void)searchLocus:(id)sender { if (self.locusField.stringValue.length) [self callDesktopMethod:@"search" arguments:@[self.locusField.stringValue]]; }
- (void)zoomIn:(id)sender { [self callDesktopMethod:@"zoomIn" arguments:@[]]; }
- (void)zoomOut:(id)sender { [self callDesktopMethod:@"zoomOut" arguments:@[]]; }
- (void)toggleHighlight:(id)sender {
    [self.highlightButton refreshColors];
    [self callDesktopMethod:@"setHighlight" arguments:@[@(self.highlightButton.state == NSControlStateValueOn)]];
}
- (void)changeHighlightColor:(id)sender { [self callDesktopMethod:@"setHighlightColor" arguments:@[[self hexFromColor:self.highlightColorWell.color]]]; }
- (void)clearHighlights:(id)sender { [self callDesktopMethod:@"clearHighlights" arguments:@[]]; }
- (void)openServerFiles:(id)sender { [self callDesktopMethod:@"openServerFiles" arguments:@[]]; }
- (void)openPublicData:(id)sender { [self callDesktopMethod:@"openPublicData" arguments:@[]]; }
- (void)openCustomReference:(id)sender { [self callDesktopMethod:@"openCustomReference" arguments:@[]]; }
- (void)openFavorites:(id)sender { [self callDesktopMethod:@"openFavorites" arguments:@[]]; }
- (void)shareView:(id)sender { [self callDesktopMethod:@"share" arguments:@[]]; }
- (void)exportPNG:(id)sender { [self callDesktopMethod:@"exportPNG" arguments:@[]]; }
- (void)removeSelectedTrack:(id)sender { NSInteger row = self.trackTable.selectedRow; if (row >= 0) [self callDesktopMethod:@"removeTrack" arguments:@[@(row)]]; }

- (void)editServerAddress:(id)sender {
    NSAlert *alert = [[NSAlert alloc] init];
    alert.messageText = @"Connect to Genome Canvas Server";
    alert.informativeText = @"Enter a LAN server address. The local backend will stop after the connection is accepted.";
    [alert addButtonWithTitle:@"Connect"];
    [alert addButtonWithTitle:@"Cancel"];
    NSTextField *input = [NSTextField textFieldWithString:self.serverAddress ?: GCDefaultServerAddress];
    input.frame = NSMakeRect(0, 0, 390, 26);
    alert.accessoryView = input;
    [alert beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse result) {
        if (result == NSAlertFirstButtonReturn) [self connectToRemoteServer:input.stringValue];
        else [self updateConnectionUI];
    }];
}

- (void)reloadOrStop:(id)sender {
    if (self.webView.loading) { [self.webView stopLoading]; [self setStatus:@"Stopped" state:@"ready"]; }
    else { [self setStatus:@"Loading" state:@"busy"]; [self.webView reloadFromOrigin]; }
}

- (void)observeValueForKeyPath:(NSString *)keyPath ofObject:(id)object change:(NSDictionary<NSKeyValueChangeKey,id> *)change context:(void *)context {
    if ([keyPath isEqualToString:@"estimatedProgress"] || [keyPath isEqualToString:@"loading"]) {
        CGFloat width = self.progressBar.superview.bounds.size.width;
        self.progressWidthConstraint.constant = width * self.webView.estimatedProgress;
        self.progressBar.hidden = !self.webView.loading;
        // "loading" changes after the final progress event, so follow it directly.
        NSString *symbol = self.webView.loading ? @"xmark" : @"arrow.clockwise";
        self.reloadButton.image = GCSymbol(symbol, 13, @"Reload or stop");
        self.reloadButton.toolTip = self.webView.loading ? @"Stop loading" : @"Reload from server";
        [self.reloadButton refreshColors];
        return;
    }
    if ([keyPath isEqualToString:@"effectiveAppearance"]) {
        dispatch_async(dispatch_get_main_queue(), ^{ [self appearanceDidChange]; });
        return;
    }
    [super observeValueForKeyPath:keyPath ofObject:object change:change context:context];
}

- (void)webView:(WKWebView *)webView didStartProvisionalNavigation:(WKNavigation *)navigation { [self setStatus:@"Loading" state:@"busy"]; self.progressBar.hidden = NO; }
- (void)webView:(WKWebView *)webView didFinishNavigation:(WKNavigation *)navigation {
    self.progressBar.hidden = YES;
    [self appearanceDidChange];
    [self scheduleSnapshotCaptureIfRequested];
    self.window.title = @"Genome Canvas";
    [self setStatus:@"Preparing canvas" state:@"busy"];
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.8 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{ [self requestSnapshot]; });
}
- (void)webView:(WKWebView *)webView didFailNavigation:(WKNavigation *)navigation withError:(NSError *)error { [self showNavigationError:error]; }
- (void)webView:(WKWebView *)webView didFailProvisionalNavigation:(WKNavigation *)navigation withError:(NSError *)error { [self showNavigationError:error]; }
- (void)showNavigationError:(NSError *)error { if (error.code != NSURLErrorCancelled) { [self setStatus:@"Server unavailable" state:@"error"]; self.statusLabel.toolTip = error.localizedDescription; self.progressBar.hidden = YES; } }
- (void)webViewWebContentProcessDidTerminate:(WKWebView *)webView { [self setStatus:@"Canvas process stopped" state:@"error"]; }

- (void)webView:(WKWebView *)webView decidePolicyForNavigationAction:(WKNavigationAction *)navigationAction decisionHandler:(void (^)(WKNavigationActionPolicy))decisionHandler {
    if (navigationAction.shouldPerformDownload) { decisionHandler(WKNavigationActionPolicyDownload); return; }
    NSURL *url = navigationAction.request.URL;
    NSString *scheme = url.scheme.lowercaseString;
    NSSet *internalSchemes = [NSSet setWithArray:@[@"http", @"https", @"blob", @"about"]];
    if (scheme.length && ![internalSchemes containsObject:scheme]) { [[NSWorkspace sharedWorkspace] openURL:url]; decisionHandler(WKNavigationActionPolicyCancel); return; }
    decisionHandler(WKNavigationActionPolicyAllow);
}

- (void)webView:(WKWebView *)webView decidePolicyForNavigationResponse:(WKNavigationResponse *)navigationResponse decisionHandler:(void (^)(WKNavigationResponsePolicy))decisionHandler {
    NSString *disposition = @"";
    if ([navigationResponse.response isKindOfClass:[NSHTTPURLResponse class]]) disposition = [[(NSHTTPURLResponse *)navigationResponse.response valueForHTTPHeaderField:@"Content-Disposition"] lowercaseString] ?: @"";
    decisionHandler(!navigationResponse.canShowMIMEType || [disposition containsString:@"attachment"] ? WKNavigationResponsePolicyDownload : WKNavigationResponsePolicyAllow);
}
- (void)webView:(WKWebView *)webView navigationAction:(WKNavigationAction *)navigationAction didBecomeDownload:(WKDownload *)download { download.delegate = self; }
- (void)webView:(WKWebView *)webView navigationResponse:(WKNavigationResponse *)navigationResponse didBecomeDownload:(WKDownload *)download { download.delegate = self; }

- (void)download:(WKDownload *)download decideDestinationUsingResponse:(NSURLResponse *)response suggestedFilename:(NSString *)suggestedFilename completionHandler:(void (^)(NSURL * _Nullable destination))completionHandler {
    NSSavePanel *panel = [NSSavePanel savePanel];
    panel.nameFieldStringValue = suggestedFilename;
    panel.canCreateDirectories = YES;
    panel.title = @"Save Genome Canvas File";
    [panel beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse result) {
        if (result != NSModalResponseOK || !panel.URL) { completionHandler(nil); return; }
        NSURL *destination = panel.URL;
        if ([[NSFileManager defaultManager] fileExistsAtPath:destination.path]) [[NSFileManager defaultManager] removeItemAtURL:destination error:nil];
        [self.downloadDestinations setObject:destination forKey:download];
        [self setStatus:@"Downloading" state:@"busy"];
        completionHandler(destination);
    }];
}
- (void)downloadDidFinish:(WKDownload *)download { NSURL *destination = [self.downloadDestinations objectForKey:download]; [self.downloadDestinations removeObjectForKey:download]; [self setStatus:(destination.lastPathComponent.length ? [@"Saved " stringByAppendingString:destination.lastPathComponent] : @"Download complete") state:@"ready"]; }
- (void)download:(WKDownload *)download didFailWithError:(NSError *)error resumeData:(NSData *)resumeData { [self.downloadDestinations removeObjectForKey:download]; [self setStatus:@"Download failed" state:@"error"]; self.statusLabel.toolTip = error.localizedDescription; }

- (void)webView:(WKWebView *)webView runOpenPanelWithParameters:(WKOpenPanelParameters *)parameters initiatedByFrame:(WKFrameInfo *)frame completionHandler:(void (^)(NSArray<NSURL *> * _Nullable URLs))completionHandler {
    NSOpenPanel *panel = [NSOpenPanel openPanel];
    panel.allowsMultipleSelection = parameters.allowsMultipleSelection;
    panel.canChooseDirectories = parameters.allowsDirectories;
    panel.canChooseFiles = YES;
    [panel beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse result) { completionHandler(result == NSModalResponseOK ? panel.URLs : nil); }];
}
- (WKWebView *)webView:(WKWebView *)webView createWebViewWithConfiguration:(WKWebViewConfiguration *)configuration forNavigationAction:(WKNavigationAction *)navigationAction windowFeatures:(WKWindowFeatures *)windowFeatures { if (!navigationAction.targetFrame) [webView loadRequest:navigationAction.request]; return nil; }
- (void)webView:(WKWebView *)webView runJavaScriptAlertPanelWithMessage:(NSString *)message initiatedByFrame:(WKFrameInfo *)frame completionHandler:(void (^)(void))completionHandler { NSAlert *alert = [[NSAlert alloc] init]; alert.messageText = @"Genome Canvas"; alert.informativeText = message; [alert addButtonWithTitle:@"OK"]; [alert beginSheetModalForWindow:self.window completionHandler:^(__unused NSModalResponse result) { completionHandler(); }]; }
- (void)webView:(WKWebView *)webView runJavaScriptConfirmPanelWithMessage:(NSString *)message initiatedByFrame:(WKFrameInfo *)frame completionHandler:(void (^)(BOOL result))completionHandler { NSAlert *alert = [[NSAlert alloc] init]; alert.messageText = @"Genome Canvas"; alert.informativeText = message; [alert addButtonWithTitle:@"Continue"]; [alert addButtonWithTitle:@"Cancel"]; [alert beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse result) { completionHandler(result == NSAlertFirstButtonReturn); }]; }
- (void)webView:(WKWebView *)webView runJavaScriptTextInputPanelWithPrompt:(NSString *)prompt defaultText:(NSString *)defaultText initiatedByFrame:(WKFrameInfo *)frame completionHandler:(void (^)(NSString * _Nullable result))completionHandler { NSAlert *alert = [[NSAlert alloc] init]; alert.messageText = @"Genome Canvas"; alert.informativeText = prompt; [alert addButtonWithTitle:@"OK"]; [alert addButtonWithTitle:@"Cancel"]; NSTextField *input = [NSTextField textFieldWithString:defaultText ?: @""]; input.frame = NSMakeRect(0, 0, 320, 24); alert.accessoryView = input; [alert beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse result) { completionHandler(result == NSAlertFirstButtonReturn ? input.stringValue : nil); }]; }

@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSApplication *application = [NSApplication sharedApplication];
        application.activationPolicy = NSApplicationActivationPolicyRegular;
        GCAppDelegate *delegate = [[GCAppDelegate alloc] init];
        application.delegate = delegate;
        [application run];
    }
    return 0;
}
