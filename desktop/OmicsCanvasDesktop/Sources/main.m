// Omics Canvas: a native macOS window around Omics Canvas's spatial viewer, single-cell viewer and
// CRISPR screen explorer (the web pages of http://171.65.68.140:8892, opened with ?desktop=1 so the page
// leaves the window chrome to this app). Modeled on Genome Canvas Desktop (genome-canvas/macos), whose
// palette, fonts, GCFillView and GCButton are copied unchanged below; Genome Canvas stays its own app.
#import <AppKit/AppKit.h>
#import <CoreText/CoreText.h>
#import <WebKit/WebKit.h>
#include <math.h>

static NSString * const TVDefaultServerAddress = @"http://171.65.68.140:8892/";
static NSString * const TVServerDefaultsKey = @"OmicsCanvasServerAddress";
static NSString * const TVAppearanceDefaultsKey = @"OmicsCanvasAppearance";
static NSString * const TVViewerDefaultsKey = @"OmicsCanvasViewer";

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

#pragma mark - Viewers

// The three web tools this app shows. "path" is the URL path on the THOR gateway.
static NSArray<NSDictionary<NSString *, NSString *> *> *TVViewers(void) {
    return @[
        @{@"kind": @"spatial", @"path": @"spatial", @"title": @"Spatial viewer", @"detail": @"Xenium, Visium (HD), Slide-seq", @"symbol": @"square.grid.3x3.square"},
        @{@"kind": @"singlecell", @"path": @"single-cell", @"title": @"Single-cell viewer", @"detail": @"scRNA, multiome, scATAC", @"symbol": @"circle.hexagongrid"},
        @{@"kind": @"crispr", @"path": @"crispr", @"title": @"CRISPR explorer", @"detail": @"Enhancer screens, Perturb-seq", @"symbol": @"chart.dots.scatter"},
    ];
}

static NSDictionary<NSString *, NSString *> *TVViewerForKind(NSString *kind) {
    for (NSDictionary<NSString *, NSString *> *viewer in TVViewers()) if ([viewer[@"kind"] isEqualToString:kind]) return viewer;
    return TVViewers().firstObject;
}

static NSString *const TVGenomeCanvasBundleID = @"org.genomecanvas.desktop";

static NSDictionary<NSString *, NSString *> *TVViewerForPath(NSString *path) {
    for (NSDictionary<NSString *, NSString *> *viewer in TVViewers()) {
        NSString *prefix = [NSString stringWithFormat:@"/%@/", viewer[@"path"]];
        if ([path hasPrefix:prefix] || [path isEqualToString:[prefix substringToIndex:prefix.length - 1]]) return viewer;
    }
    return nil;
}

#pragma mark - Viewer card

// A viewer choice in the sidebar, drawn like Genome Canvas's "Open server files" card: icon tile, title,
// detail. The selected viewer gets the accent fill and border.
@interface TVViewerCard : NSButton
@property(nonatomic, copy) NSString *kind;
@property(nonatomic, strong) GCFillView *tile;
@property(nonatomic, strong) NSImageView *glyph;
@property(nonatomic, strong) NSTextField *titleLabel;
@property(nonatomic, strong) NSTextField *detailLabel;
@property(nonatomic) BOOL hovered;
@property(nonatomic) BOOL chosen;
@property(nonatomic, strong) NSTrackingArea *hoverArea;
- (instancetype)initWithViewer:(NSDictionary<NSString *, NSString *> *)viewer target:(id)target action:(SEL)action;
@end

@implementation TVViewerCard
- (instancetype)initWithViewer:(NSDictionary<NSString *, NSString *> *)viewer target:(id)target action:(SEL)action {
    if (!(self = [super initWithFrame:NSZeroRect])) return nil;
    self.translatesAutoresizingMaskIntoConstraints = NO;
    self.bordered = NO;
    self.title = @"";
    self.kind = viewer[@"kind"];
    self.target = target;
    self.action = action;
    self.accessibilityLabel = viewer[@"title"];
    self.tile = [[GCFillView alloc] initWithFrame:NSZeroRect];
    self.tile.fillColor = GCBrandGlyphColor();
    self.tile.cornerRadius = 8;
    self.glyph = [NSImageView imageViewWithImage:GCSymbol(viewer[@"symbol"], 14, viewer[@"title"]) ?: [[NSImage alloc] init]];
    self.glyph.translatesAutoresizingMaskIntoConstraints = NO;
    self.glyph.contentTintColor = GCHex(0xeceff4, 1);
    [self.tile addSubview:self.glyph];
    self.titleLabel = [NSTextField labelWithString:viewer[@"title"]];
    self.titleLabel.font = GCUIFont(13, 650);
    self.titleLabel.textColor = GCTextColor();
    self.detailLabel = [NSTextField labelWithString:viewer[@"detail"]];
    self.detailLabel.font = GCUIFont(11.5, 450);
    self.detailLabel.textColor = GCText3Color();
    self.detailLabel.lineBreakMode = NSLineBreakByTruncatingTail;
    NSStackView *copy = [NSStackView stackViewWithViews:@[self.titleLabel, self.detailLabel]];
    copy.translatesAutoresizingMaskIntoConstraints = NO;
    copy.orientation = NSUserInterfaceLayoutOrientationVertical;
    copy.alignment = NSLayoutAttributeLeading;
    copy.spacing = 1;
    [self addSubview:self.tile];
    [self addSubview:copy];
    [NSLayoutConstraint activateConstraints:@[
        [self.tile.leadingAnchor constraintEqualToAnchor:self.leadingAnchor constant:10],
        [self.tile.centerYAnchor constraintEqualToAnchor:self.centerYAnchor],
        [self.tile.widthAnchor constraintEqualToConstant:30], [self.tile.heightAnchor constraintEqualToConstant:30],
        [self.glyph.centerXAnchor constraintEqualToAnchor:self.tile.centerXAnchor],
        [self.glyph.centerYAnchor constraintEqualToAnchor:self.tile.centerYAnchor],
        [copy.leadingAnchor constraintEqualToAnchor:self.tile.trailingAnchor constant:10],
        [copy.trailingAnchor constraintLessThanOrEqualToAnchor:self.trailingAnchor constant:-10],
        [copy.centerYAnchor constraintEqualToAnchor:self.centerYAnchor],
        [self.heightAnchor constraintEqualToConstant:50],
    ]];
    return self;
}
- (NSView *)hitTest:(NSPoint)point { return [super hitTest:point] ? self : nil; }
- (void)setHovered:(BOOL)hovered { _hovered = hovered; self.needsDisplay = YES; }
- (void)setChosen:(BOOL)chosen {
    _chosen = chosen;
    self.tile.fillColor = chosen ? GCAccentColor() : GCBrandGlyphColor();
    self.titleLabel.textColor = chosen ? GCAccentTextColor() : GCTextColor();
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
- (void)viewDidChangeEffectiveAppearance { [super viewDidChangeEffectiveAppearance]; self.chosen = self.chosen; }
- (void)drawRect:(NSRect)dirtyRect {
    NSRect frame = NSInsetRect(self.bounds, 1, 1);
    NSBezierPath *path = [NSBezierPath bezierPathWithRoundedRect:NSInsetRect(frame, 0.5, 0.5) xRadius:11 yRadius:11];
    [(self.chosen ? GCAccentSoftColor() : (self.hovered ? GCHoverColor() : GCSurfaceColor())) setFill];
    [path fill];
    [(self.chosen || self.hovered ? GCAccentColor() : GCBorderColor()) setStroke];
    path.lineWidth = 1;
    [path stroke];
}
@end

#pragma mark - Dataset rows

// Rounded hover and selection fills, like Genome Canvas's track rows.
@interface TVRowView : NSTableRowView
@property(nonatomic) BOOL hovered;
@property(nonatomic, strong) NSTrackingArea *hoverArea;
@end

@implementation TVRowView
- (void)updateTrackingAreas {
    [super updateTrackingAreas];
    if (self.hoverArea) [self removeTrackingArea:self.hoverArea];
    self.hoverArea = [[NSTrackingArea alloc] initWithRect:NSZeroRect
        options:NSTrackingMouseEnteredAndExited | NSTrackingActiveInActiveApp | NSTrackingInVisibleRect owner:self userInfo:nil];
    [self addTrackingArea:self.hoverArea];
}
- (void)mouseEntered:(NSEvent *)event { self.hovered = YES; }
- (void)mouseExited:(NSEvent *)event { self.hovered = NO; }
- (void)setHovered:(BOOL)hovered { _hovered = hovered; self.needsDisplay = YES; }
- (NSBackgroundStyle)interiorBackgroundStyle { return NSBackgroundStyleNormal; }
- (NSBezierPath *)rowPath { return [NSBezierPath bezierPathWithRoundedRect:NSInsetRect(self.bounds, 2, 1) xRadius:9 yRadius:9]; }
- (void)drawBackgroundInRect:(NSRect)dirtyRect {
    if (self.hovered && !self.selected) { [GCHoverColor() setFill]; [[self rowPath] fill]; }
}
- (void)drawSelectionInRect:(NSRect)dirtyRect { [GCAccentSoftColor() setFill]; [[self rowPath] fill]; }
@end

@interface TVSplitView : NSSplitView
@end

@implementation TVSplitView
- (NSColor *)dividerColor { return GCDividerColor(); }
@end

#pragma mark - Application

@interface TVAppDelegate : NSObject <NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate, WKScriptMessageHandler, NSTableViewDataSource, NSTableViewDelegate, NSMenuDelegate>
@property(nonatomic, strong) NSWindow *window;
@property(nonatomic, strong) WKWebView *webView;
@property(nonatomic, copy) NSArray<TVViewerCard *> *cards;
@property(nonatomic, strong) NSPopUpButton *workspacePopup;
@property(nonatomic, strong) NSTableView *datasetTable;
@property(nonatomic, strong) NSTextField *datasetCountLabel;
@property(nonatomic, strong) NSTextField *emptyLabel;
@property(nonatomic, strong) GCFillView *statusDot;
@property(nonatomic, strong) NSTextField *statusLabel;
@property(nonatomic, strong) NSTextField *serverLabel;
@property(nonatomic, strong) NSTextField *pageTitleLabel;
@property(nonatomic, strong) GCButton *backButton;
@property(nonatomic, strong) GCButton *exportButton;
@property(nonatomic, strong) GCButton *themeButton;
@property(nonatomic, strong) GCButton *reloadButton;
@property(nonatomic, strong) GCFillView *progressBar;
@property(nonatomic, strong) NSLayoutConstraint *progressWidthConstraint;
@property(nonatomic, copy) NSArray<NSMenuItem *> *appearanceMenuItems;
@property(nonatomic, copy) NSArray<NSMenuItem *> *viewerMenuItems;
@property(nonatomic, strong) NSMapTable<WKDownload *, NSURL *> *downloadDestinations;
@property(nonatomic, copy) NSArray<NSDictionary *> *datasets;
@property(nonatomic, copy) NSArray<NSDictionary *> *workspaces;
@property(nonatomic, copy) NSString *serverAddress;
@property(nonatomic, copy) NSString *kind;
@property(nonatomic, copy) NSString *currentPath;
@property(nonatomic) BOOL updatingSelection;
/** sidebar rows: {dataset} for each dataset, followed by {dataset, sample} rows when its samples are shown */
@property(nonatomic, copy) NSArray<NSDictionary *> *datasetRows;
@property(nonatomic, strong) NSMutableSet<NSString *> *expandedDatasets;
@property(nonatomic, strong) GCButton *combineButton;
@property(nonatomic, strong) GCButton *sharedButton;
@property(nonatomic, strong) NSMenuItem *sharedMenuItem;
@property(nonatomic) BOOL snapshotScheduled;
@property(nonatomic) BOOL showingErrorPage;
@end

@implementation TVAppDelegate

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    GCRegisterBundledFonts();
    self.downloadDestinations = [NSMapTable weakToStrongObjectsMapTable];
    self.datasets = @[];
    self.workspaces = @[];
    NSUserDefaults *defaults = [NSUserDefaults standardUserDefaults];
    self.serverAddress = [defaults stringForKey:TVServerDefaultsKey] ?: TVDefaultServerAddress;
    self.kind = TVViewerForKind([defaults stringForKey:TVViewerDefaultsKey] ?: @"singlecell")[@"kind"];
    [self buildApplicationMenu];

    NSRect frame = NSMakeRect(0, 0, 1480, 920);
    NSWindowStyleMask style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable;
    self.window = [[NSWindow alloc] initWithContentRect:frame styleMask:style backing:NSBackingStoreBuffered defer:NO];
    self.window.title = @"Omics Canvas";
    self.window.subtitle = @"Spatial · single-cell · CRISPR";
    self.window.minSize = NSMakeSize(1100, 680);
    self.window.titlebarAppearsTransparent = YES;
    self.window.backgroundColor = GCBackgroundColor();
    self.window.frameAutosaveName = @"OmicsCanvasMainWindow";
    [self.window center];

    TVSplitView *splitView = [[TVSplitView alloc] initWithFrame:frame];
    splitView.translatesAutoresizingMaskIntoConstraints = NO;
    splitView.vertical = YES;
    splitView.dividerStyle = NSSplitViewDividerStyleThin;
    self.window.contentView = splitView;
    NSView *sidebar = [self buildSidebar];
    NSView *content = [self buildContent];
    [splitView addArrangedSubview:sidebar];
    [splitView addArrangedSubview:content];
    [sidebar.widthAnchor constraintEqualToConstant:280].active = YES;

    [self.webView addObserver:self forKeyPath:@"estimatedProgress" options:NSKeyValueObservingOptionInitial | NSKeyValueObservingOptionNew context:NULL];
    [self.webView addObserver:self forKeyPath:@"loading" options:NSKeyValueObservingOptionNew context:NULL];
    [self.webView addObserver:self forKeyPath:@"canGoBack" options:NSKeyValueObservingOptionInitial | NSKeyValueObservingOptionNew context:NULL];
    [NSApp addObserver:self forKeyPath:@"effectiveAppearance" options:NSKeyValueObservingOptionNew context:NULL];
    [self applyAppearancePreference];

    [self.window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
    [self updateServerLabel];
    [self showViewer:self.kind];
}

#pragma mark Sidebar

- (NSView *)buildSidebar {
    GCFillView *sidebar = [[GCFillView alloc] initWithFrame:NSZeroRect];
    sidebar.fillColor = GCRailColor();

    NSImageView *mark = [NSImageView imageViewWithImage:NSApp.applicationIconImage ?: [NSImage imageNamed:NSImageNameApplicationIcon]];
    mark.translatesAutoresizingMaskIntoConstraints = NO;
    mark.imageScaling = NSImageScaleProportionallyUpOrDown;
    NSTextField *title = [NSTextField labelWithString:@"Omics Canvas"];
    title.font = GCHeadingFont(16);
    title.textColor = GCTextColor();
    NSStackView *brand = [NSStackView stackViewWithViews:@[mark, title]];
    brand.orientation = NSUserInterfaceLayoutOrientationHorizontal;
    brand.alignment = NSLayoutAttributeCenterY;
    brand.spacing = 9;

    NSTextField *viewerLabel = [self sectionLabel:@"Viewer"];
    NSMutableArray<TVViewerCard *> *cards = [NSMutableArray array];
    for (NSDictionary<NSString *, NSString *> *viewer in TVViewers()) {
        [cards addObject:[[TVViewerCard alloc] initWithViewer:viewer target:self action:@selector(chooseViewer:)]];
    }
    self.cards = cards;
    NSStackView *cardStack = [NSStackView stackViewWithViews:cards];
    cardStack.orientation = NSUserInterfaceLayoutOrientationVertical;
    cardStack.spacing = 6;

    NSTextField *workspaceLabel = [self sectionLabel:@"Workspace"];
    self.workspacePopup = [[NSPopUpButton alloc] initWithFrame:NSZeroRect pullsDown:NO];
    self.workspacePopup.translatesAutoresizingMaskIntoConstraints = NO;
    self.workspacePopup.bordered = NO;
    self.workspacePopup.font = GCUIFont(13, 550);
    self.workspacePopup.target = self;
    self.workspacePopup.action = @selector(changeWorkspace:);
    self.workspacePopup.toolTip = @"Your datasets are kept per workspace — the same workspaces as Genome Canvas";
    [self.workspacePopup addItemWithTitle:@"Choose Workspace…"];
    GCFillView *workspaceField = [self fieldContainerWithContent:self.workspacePopup inset:6];

    NSView *divider = [self dividerLine];

    NSTextField *datasetsLabel = [self sectionLabel:@"Your datasets"];
    self.datasetCountLabel = [NSTextField labelWithString:@""];
    self.datasetCountLabel.font = GCUIFont(11.5, 550);
    self.datasetCountLabel.textColor = GCText3Color();
    GCButton *manageButton = [self iconButton:@"plus.rectangle.on.folder" tip:@"Add and manage datasets" action:@selector(manageDatasets:)];
    GCButton *refreshButton = [self iconButton:@"arrow.clockwise" tip:@"Refresh the list" action:@selector(refreshDatasets:)];
    self.combineButton = [self iconButton:@"square.stack.3d.up" tip:@"Combine datasets (or CRISPR screens) into one" action:@selector(combineDatasets:)];
    manageButton.fixedHeight = 26;
    refreshButton.fixedHeight = 26;
    self.combineButton.fixedHeight = 26;
    NSView *headingSpacer = [[NSView alloc] initWithFrame:NSZeroRect];
    [headingSpacer setContentHuggingPriority:1 forOrientation:NSLayoutConstraintOrientationHorizontal];
    NSStackView *datasetsHeading = [NSStackView stackViewWithViews:@[datasetsLabel, self.datasetCountLabel, headingSpacer, self.combineButton, manageButton, refreshButton]];
    datasetsHeading.orientation = NSUserInterfaceLayoutOrientationHorizontal;
    datasetsHeading.alignment = NSLayoutAttributeCenterY;
    datasetsHeading.spacing = 6;

    self.datasetTable = [[NSTableView alloc] initWithFrame:NSZeroRect];
    NSTableColumn *column = [[NSTableColumn alloc] initWithIdentifier:@"dataset"];
    column.resizingMask = NSTableColumnAutoresizingMask;
    [self.datasetTable addTableColumn:column];
    self.datasetTable.headerView = nil;
    self.datasetTable.style = NSTableViewStylePlain;
    self.datasetTable.rowHeight = 46;
    self.datasetTable.intercellSpacing = NSMakeSize(0, 2);
    self.datasetTable.backgroundColor = [NSColor clearColor];
    self.datasetTable.selectionHighlightStyle = NSTableViewSelectionHighlightStyleRegular;
    self.datasetTable.delegate = self;
    self.datasetTable.dataSource = self;
    self.datasetTable.menu = [[NSMenu alloc] initWithTitle:@"Dataset"];
    self.datasetTable.menu.delegate = self;
    self.expandedDatasets = [NSMutableSet set];
    self.datasetRows = @[];
    NSScrollView *scroll = [[NSScrollView alloc] initWithFrame:NSZeroRect];
    scroll.translatesAutoresizingMaskIntoConstraints = NO;
    scroll.documentView = self.datasetTable;
    scroll.hasVerticalScroller = YES;
    scroll.autohidesScrollers = YES;
    scroll.drawsBackground = NO;
    scroll.borderType = NSNoBorder;
    [scroll setContentHuggingPriority:1 forOrientation:NSLayoutConstraintOrientationVertical];
    [scroll setContentCompressionResistancePriority:NSLayoutPriorityDefaultLow forOrientation:NSLayoutConstraintOrientationVertical];

    self.emptyLabel = [NSTextField wrappingLabelWithString:@"No datasets in this workspace yet. Click + to add one from /nfs."];
    self.emptyLabel.font = GCUIFont(12, 450);
    self.emptyLabel.textColor = GCText3Color();
    self.emptyLabel.hidden = YES;

    // the pages' "Shared datasets" dialog: what anyone has loaded, converted or combined, to add in one click
    GCButton *sharedButton = [GCButton buttonWithLabel:@"Shared datasets" symbol:@"person.2" style:GCButtonStyleSecondary target:self action:@selector(showShared:)];
    sharedButton.font = GCUIFont(12.5, 550);
    sharedButton.identifier = @"SharedButton";
    self.sharedButton = sharedButton;

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
    self.serverLabel = [NSTextField labelWithString:@""];
    self.serverLabel.font = GCMonoFont(10.5, 500);
    self.serverLabel.textColor = GCText4Color();
    self.serverLabel.lineBreakMode = NSLineBreakByTruncatingMiddle;
    GCButton *serverButton = [GCButton buttonWithLabel:@"Server…" symbol:@"server.rack" style:GCButtonStyleGhost target:self action:@selector(editServerAddress:)];
    serverButton.font = GCUIFont(12, 550);
    serverButton.label = serverButton.label;
    serverButton.fixedHeight = 26;
    serverButton.toolTip = @"Connect to another Omics Canvas server address";
    NSView *footerSpacer = [[NSView alloc] initWithFrame:NSZeroRect];
    [footerSpacer setContentHuggingPriority:1 forOrientation:NSLayoutConstraintOrientationHorizontal];
    NSStackView *footer = [NSStackView stackViewWithViews:@[status, footerSpacer, serverButton]];
    footer.orientation = NSUserInterfaceLayoutOrientationHorizontal;
    footer.alignment = NSLayoutAttributeCenterY;

    NSArray<NSView *> *rows = @[brand, viewerLabel, cardStack, workspaceLabel, workspaceField, divider, datasetsHeading, self.emptyLabel, scroll, sharedButton, footer, self.serverLabel];
    NSStackView *columnStack = [NSStackView stackViewWithViews:rows];
    columnStack.translatesAutoresizingMaskIntoConstraints = NO;
    columnStack.orientation = NSUserInterfaceLayoutOrientationVertical;
    columnStack.alignment = NSLayoutAttributeLeading;
    columnStack.distribution = NSStackViewDistributionFill;
    columnStack.spacing = 6;
    [columnStack setCustomSpacing:18 afterView:brand];
    [columnStack setCustomSpacing:16 afterView:cardStack];
    [columnStack setCustomSpacing:16 afterView:workspaceField];
    [columnStack setCustomSpacing:14 afterView:divider];
    [columnStack setCustomSpacing:8 afterView:datasetsHeading];
    [columnStack setCustomSpacing:10 afterView:scroll];
    [columnStack setCustomSpacing:12 afterView:sharedButton];
    [columnStack setCustomSpacing:2 afterView:footer];
    [sidebar addSubview:columnStack];

    NSMutableArray<NSLayoutConstraint *> *constraints = [NSMutableArray arrayWithArray:@[
        [mark.widthAnchor constraintEqualToConstant:28], [mark.heightAnchor constraintEqualToConstant:28],
        [columnStack.topAnchor constraintEqualToAnchor:sidebar.safeAreaLayoutGuide.topAnchor constant:12],
        [columnStack.leadingAnchor constraintEqualToAnchor:sidebar.leadingAnchor constant:12],
        [columnStack.trailingAnchor constraintEqualToAnchor:sidebar.trailingAnchor constant:-12],
        [columnStack.bottomAnchor constraintEqualToAnchor:sidebar.bottomAnchor constant:-12],
        [scroll.heightAnchor constraintGreaterThanOrEqualToConstant:120],
        [self.statusDot.widthAnchor constraintEqualToConstant:7], [self.statusDot.heightAnchor constraintEqualToConstant:7],
    ]];
    for (NSView *row in @[cardStack, workspaceField, divider, datasetsHeading, self.emptyLabel, scroll, sharedButton, footer, self.serverLabel]) {
        [constraints addObject:[row.widthAnchor constraintEqualToAnchor:columnStack.widthAnchor]];
    }
    for (TVViewerCard *card in cards) [constraints addObject:[card.widthAnchor constraintEqualToAnchor:cardStack.widthAnchor]];
    [NSLayoutConstraint activateConstraints:constraints];
    return sidebar;
}

#pragma mark Toolbar and page

- (NSView *)buildContent {
    NSView *content = [[NSView alloc] initWithFrame:NSZeroRect];
    content.translatesAutoresizingMaskIntoConstraints = NO;
    GCFillView *toolbar = [[GCFillView alloc] initWithFrame:NSZeroRect];
    toolbar.fillColor = GCSurfaceColor();
    [content addSubview:toolbar];
    GCFillView *toolbarRule = [[GCFillView alloc] initWithFrame:NSZeroRect];
    toolbarRule.fillColor = GCBorderColor();
    [content addSubview:toolbarRule];

    self.backButton = [self toolButton:@"chevron.left" tip:@"Back" action:@selector(goBack:)];
    GCButton *datasetsButton = [GCButton buttonWithLabel:@"Datasets" symbol:@"rectangle.stack" style:GCButtonStyleSecondary target:self action:@selector(manageDatasets:)];
    datasetsButton.toolTip = @"Your datasets of this viewer: add, convert, rename, remove";
    datasetsButton.fixedHeight = 32;
    self.pageTitleLabel = [NSTextField labelWithString:@""];
    self.pageTitleLabel.font = GCHeadingFont(15);
    self.pageTitleLabel.textColor = GCTextColor();
    self.pageTitleLabel.lineBreakMode = NSLineBreakByTruncatingTail;
    [self.pageTitleLabel setContentCompressionResistancePriority:NSLayoutPriorityDefaultLow forOrientation:NSLayoutConstraintOrientationHorizontal];
    NSView *spacer = [[NSView alloc] initWithFrame:NSZeroRect];
    [spacer setContentHuggingPriority:1 forOrientation:NSLayoutConstraintOrientationHorizontal];
    self.exportButton = [self toolButton:@"photo" tip:@"Export PNG (transparent background)" action:@selector(exportPNG:)];
    GCButton *browserButton = [self toolButton:@"safari" tip:@"Open this page in the web browser" action:@selector(openInBrowser:)];
    self.themeButton = [self iconButton:@"moon" tip:@"Switch to dark theme" action:@selector(toggleAppearance:)];
    self.reloadButton = [self iconButton:@"arrow.clockwise" tip:@"Reload" action:@selector(reloadOrStop:)];
    for (GCButton *button in @[self.themeButton, self.reloadButton]) button.fixedHeight = 34;

    NSStackView *controls = [NSStackView stackViewWithViews:@[
        self.backButton, datasetsButton, [self verticalRule], self.pageTitleLabel, spacer,
        self.exportButton, browserButton, [self verticalRule], self.themeButton, self.reloadButton
    ]];
    controls.translatesAutoresizingMaskIntoConstraints = NO;
    controls.orientation = NSUserInterfaceLayoutOrientationHorizontal;
    controls.alignment = NSLayoutAttributeCenterY;
    controls.spacing = 6;
    [controls setCustomSpacing:10 afterView:datasetsButton];
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
    [configuration.userContentController addScriptMessageHandler:self name:@"vizhub"];
    self.webView = [[WKWebView alloc] initWithFrame:NSZeroRect configuration:configuration];
    self.webView.translatesAutoresizingMaskIntoConstraints = NO;
    self.webView.navigationDelegate = self;
    self.webView.UIDelegate = self;
    self.webView.allowsMagnification = YES;
    self.webView.allowsBackForwardNavigationGestures = YES;
    self.webView.underPageBackgroundColor = GCBackgroundColor();

    GCFillView *pageCard = [[GCFillView alloc] initWithFrame:NSZeroRect];
    pageCard.fillColor = GCBackgroundColor();
    pageCard.strokeColor = GCBorderColor();
    pageCard.cornerRadius = 13;
    pageCard.clipsContent = YES;
    [pageCard addSubview:self.webView];
    [content addSubview:pageCard];

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
        [progressTrack.topAnchor constraintEqualToAnchor:toolbarRule.bottomAnchor],
        [progressTrack.leadingAnchor constraintEqualToAnchor:content.leadingAnchor],
        [progressTrack.trailingAnchor constraintEqualToAnchor:content.trailingAnchor],
        [progressTrack.heightAnchor constraintEqualToConstant:2],
        [self.progressBar.leadingAnchor constraintEqualToAnchor:progressTrack.leadingAnchor],
        [self.progressBar.topAnchor constraintEqualToAnchor:progressTrack.topAnchor],
        [self.progressBar.bottomAnchor constraintEqualToAnchor:progressTrack.bottomAnchor],
        self.progressWidthConstraint,
        [pageCard.topAnchor constraintEqualToAnchor:progressTrack.bottomAnchor constant:8],
        [pageCard.leadingAnchor constraintEqualToAnchor:content.leadingAnchor constant:10],
        [pageCard.trailingAnchor constraintEqualToAnchor:content.trailingAnchor constant:-10],
        [pageCard.bottomAnchor constraintEqualToAnchor:content.bottomAnchor constant:-10],
        [self.webView.topAnchor constraintEqualToAnchor:pageCard.topAnchor constant:1],
        [self.webView.leadingAnchor constraintEqualToAnchor:pageCard.leadingAnchor constant:1],
        [self.webView.trailingAnchor constraintEqualToAnchor:pageCard.trailingAnchor constant:-1],
        [self.webView.bottomAnchor constraintEqualToAnchor:pageCard.bottomAnchor constant:-1],
    ]];
    return content;
}

#pragma mark Control factories (as in Genome Canvas Desktop)

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

#pragma mark Server address and pages

- (NSURL *)serverRoot {
    NSString *candidate = [self.serverAddress stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if (!candidate.length) candidate = TVDefaultServerAddress;
    if ([candidate rangeOfString:@"://"].location == NSNotFound) candidate = [@"http://" stringByAppendingString:candidate];
    NSURLComponents *components = [NSURLComponents componentsWithString:candidate];
    NSString *scheme = components.scheme.lowercaseString;
    if (!components.host.length || !([scheme isEqualToString:@"http"] || [scheme isEqualToString:@"https"])) return nil;
    components.path = @"/";
    components.query = nil;
    components.fragment = nil;
    return components.URL;
}

/** A page of the hub, e.g. "/spatial/" or "/single-cell/<dataset>/?s=…", opened in desktop mode. */
- (NSURL *)pageURL:(NSString *)pathAndQuery {
    NSURL *root = [self serverRoot];
    if (!root) return nil;
    NSURLComponents *page = [NSURLComponents componentsWithString:pathAndQuery];
    NSURLComponents *components = [NSURLComponents componentsWithURL:root resolvingAgainstBaseURL:NO];
    components.path = page.path.length ? page.path : @"/";
    NSMutableArray<NSURLQueryItem *> *items = [NSMutableArray array];
    for (NSURLQueryItem *item in page.queryItems ?: @[]) if (![item.name isEqualToString:@"desktop"]) [items addObject:item];
    [items addObject:[NSURLQueryItem queryItemWithName:@"desktop" value:@"1"]];
    components.queryItems = items;
    components.fragment = page.fragment;
    return components.URL;
}

- (void)loadPage:(NSString *)pathAndQuery {
    NSURL *url = [self pageURL:pathAndQuery];
    if (!url) { [self setStatus:@"Invalid server address" state:@"error"]; NSBeep(); return; }
    [self setStatus:@"Connecting" state:@"busy"];
    [self.webView loadRequest:[NSURLRequest requestWithURL:url cachePolicy:NSURLRequestReloadRevalidatingCacheData timeoutInterval:30]];
}

- (void)updateServerLabel {
    NSURL *root = [self serverRoot];
    self.serverLabel.stringValue = root ? [root.absoluteString stringByReplacingOccurrencesOfString:@"http://" withString:@""] : @"no server";
    self.serverLabel.toolTip = root.absoluteString;
}

- (void)selectCardForKind:(NSString *)kind {
    for (TVViewerCard *card in self.cards) card.chosen = [card.kind isEqualToString:kind];
    NSString *noun = [kind isEqualToString:@"crispr"] ? @"screens" : @"datasets";
    self.sharedButton.label = [@"Shared " stringByAppendingString:noun];
    self.sharedButton.toolTip = [NSString stringWithFormat:@"%@ others in the lab have loaded, converted or combined: add them without looking for their files",
        [kind isEqualToString:@"crispr"] ? @"Screens" : @"Datasets"];
    self.sharedMenuItem.title = [NSString stringWithFormat:@"Shared %@…", [kind isEqualToString:@"crispr"] ? @"Screens" : @"Datasets"];
    for (NSMenuItem *item in self.viewerMenuItems) item.state = [item.representedObject isEqualToString:kind] ? NSControlStateValueOn : NSControlStateValueOff;
}

/** Switch to a viewer: its dataset page (manage, add, convert) and its datasets in the sidebar. */
- (void)showViewer:(NSString *)kind {
    NSDictionary<NSString *, NSString *> *viewer = TVViewerForKind(kind);
    BOOL changed = ![self.kind isEqualToString:viewer[@"kind"]];
    self.kind = viewer[@"kind"];
    [[NSUserDefaults standardUserDefaults] setObject:self.kind forKey:TVViewerDefaultsKey];
    [self selectCardForKind:self.kind];
    if (changed) { self.datasets = @[]; [self rebuildDatasetRows]; }
    [self loadPage:[NSString stringWithFormat:@"/%@/", viewer[@"path"]]];
}

- (void)chooseViewer:(TVViewerCard *)sender { [self showViewer:sender.kind]; }
- (void)chooseViewerFromMenu:(NSMenuItem *)sender { [self showViewer:sender.representedObject]; }
- (void)manageDatasets:(id)sender { [self showViewer:self.kind]; }

/** The "Shared datasets" dialog of the page showing (window.VizHubDesktop.showShared), else the dataset page at #shared. */
- (void)showShared:(id)sender {
    NSString *kind = self.kind;
    [self callDesktopMethod:@"showShared" arguments:@[] completion:^(id result) {
        if (([result respondsToSelector:@selector(boolValue)] && [result boolValue]) || ![kind isEqualToString:self.kind]) return;
        [self loadPage:[[self viewerPagePath] stringByAppendingString:@"#shared"]];
    }];
}
- (void)goBack:(id)sender { if (self.webView.canGoBack) [self.webView goBack]; }

- (void)openInBrowser:(id)sender {
    NSURLComponents *components = [NSURLComponents componentsWithURL:self.webView.URL ?: [self serverRoot] resolvingAgainstBaseURL:NO];
    NSMutableArray<NSURLQueryItem *> *items = [NSMutableArray array];
    for (NSURLQueryItem *item in components.queryItems ?: @[]) if (![item.name isEqualToString:@"desktop"]) [items addObject:item];
    components.queryItems = items.count ? items : nil;
    if (components.URL) [[NSWorkspace sharedWorkspace] openURL:components.URL];
}

- (void)editServerAddress:(id)sender {
    NSAlert *alert = [[NSAlert alloc] init];
    alert.messageText = @"Connect to an Omics Canvas Server";
    alert.informativeText = @"Enter the address of the hub's gateway (the viewers are at /spatial/, /single-cell/ and /crispr/). From outside Stanford, connect the VPN first.";
    [alert addButtonWithTitle:@"Connect"];
    [alert addButtonWithTitle:@"Cancel"];
    NSTextField *input = [NSTextField textFieldWithString:self.serverAddress ?: TVDefaultServerAddress];
    input.frame = NSMakeRect(0, 0, 390, 26);
    alert.accessoryView = input;
    [alert beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse result) {
        if (result != NSAlertFirstButtonReturn) return;
        NSString *previous = self.serverAddress;
        self.serverAddress = input.stringValue;
        if (![self serverRoot]) { self.serverAddress = previous; [self setStatus:@"Invalid server address" state:@"error"]; NSBeep(); return; }
        [[NSUserDefaults standardUserDefaults] setObject:self.serverAddress forKey:TVServerDefaultsKey];
        [self updateServerLabel];
        [self showViewer:self.kind];
    }];
}

- (void)setStatus:(NSString *)text state:(NSString *)state {
    self.statusLabel.stringValue = text.length ? text : @"Ready";
    if ([state isEqualToString:@"error"]) self.statusDot.fillColor = GCDangerColor();
    else if ([state isEqualToString:@"busy"]) self.statusDot.fillColor = GCWarningColor();
    else self.statusDot.fillColor = GCSuccessColor();
}

#pragma mark Bridge to the page (window.VizHubDesktop in hub/web/src/common.ts)

- (NSString *)JSONFragment:(id)value {
    NSData *data = [NSJSONSerialization dataWithJSONObject:value ?: [NSNull null] options:NSJSONWritingFragmentsAllowed error:nil];
    return data ? [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding] : @"null";
}

- (void)callDesktopMethod:(NSString *)method arguments:(NSArray *)arguments completion:(void (^)(id result))completion {
    NSMutableArray<NSString *> *encoded = [NSMutableArray arrayWithCapacity:arguments.count];
    for (id argument in arguments) [encoded addObject:[self JSONFragment:argument]];
    // callAsyncJavaScript awaits the promise the bridge method returns
    NSString *body = [NSString stringWithFormat:@"const b = window.VizHubDesktop; if (!b || !b[%@]) return null; return await b[%@](%@);",
        [self JSONFragment:method], [self JSONFragment:method], [encoded componentsJoinedByString:@","]];
    [self.webView callAsyncJavaScript:body arguments:@{} inFrame:nil inContentWorld:[WKContentWorld pageWorld] completionHandler:^(id result, NSError *error) {
        if (completion) completion(error || [result isKindOfClass:[NSNull class]] ? nil : result);
    }];
}

- (void)refreshWorkspaces {
    [self callDesktopMethod:@"workspaces" arguments:@[] completion:^(id result) {
        if (![result isKindOfClass:[NSDictionary class]]) return;
        NSDictionary *payload = result;
        NSArray *records = [payload[@"workspaces"] isKindOfClass:[NSArray class]] ? payload[@"workspaces"] : @[];
        NSDictionary *selected = [payload[@"selected"] isKindOfClass:[NSDictionary class]] ? payload[@"selected"] : nil;
        self.workspaces = records;
        [self.workspacePopup removeAllItems];
        if (!selected) [self.workspacePopup addItemWithTitle:@"Choose Workspace…"];
        for (NSDictionary *workspace in records) {
            [self.workspacePopup addItemWithTitle:workspace[@"name"] ?: @"Workspace"];
            self.workspacePopup.lastItem.representedObject = workspace[@"id"];
        }
        for (NSMenuItem *item in self.workspacePopup.itemArray) {
            if (selected && [item.representedObject isEqual:selected[@"id"]]) { [self.workspacePopup selectItem:item]; break; }
        }
    }];
}

- (void)changeWorkspace:(id)sender {
    NSString *workspaceId = self.workspacePopup.selectedItem.representedObject;
    if (!workspaceId.length) return;
    [self callDesktopMethod:@"selectWorkspace" arguments:@[workspaceId] completion:^(id result) {
        [self refreshWorkspaces];
        [self refreshDatasets:nil];
        // dataset pages list the workspace's datasets: show them again for the new workspace
        if (![self.currentPath hasSuffix:[NSString stringWithFormat:@"/%@/", TVViewerForKind(self.kind)[@"path"]]]) [self showViewer:self.kind];
    }];
}

- (void)refreshDatasets:(id)sender {
    NSString *kind = self.kind;
    [self callDesktopMethod:@"datasets" arguments:@[kind] completion:^(id result) {
        if (![kind isEqualToString:self.kind]) return;
        self.datasets = [result isKindOfClass:[NSArray class]] ? result : @[];
        [self rebuildDatasetRows];
        self.datasetCountLabel.stringValue = self.datasets.count ? [NSString stringWithFormat:@"%lu", (unsigned long)self.datasets.count] : @"";
        self.emptyLabel.hidden = self.datasets.count > 0;
        [self selectRowForCurrentPath];
    }];
}

- (void)exportPNG:(id)sender {
    [self callDesktopMethod:@"exportPNG" arguments:@[] completion:^(id result) {
        if ([result respondsToSelector:@selector(boolValue)] && [result boolValue]) return;
        NSAlert *alert = [[NSAlert alloc] init];
        alert.messageText = @"Nothing to Export";
        alert.informativeText = @"Open a spatial or single-cell dataset first; its plot is saved as a PNG with a transparent background.";
        [alert addButtonWithTitle:@"OK"];
        [alert beginSheetModalForWindow:self.window completionHandler:nil];
    }];
}

- (void)userContentController:(WKUserContentController *)userContentController didReceiveScriptMessage:(WKScriptMessage *)message {
    if (![message.name isEqualToString:@"vizhub"] || ![message.body isKindOfClass:[NSDictionary class]]) return;
    NSDictionary *payload = message.body;
    NSString *type = payload[@"type"];
    if ([type isEqualToString:@"page"]) {
        self.currentPath = [payload[@"path"] isKindOfClass:[NSString class]] ? payload[@"path"] : @"";
        NSDictionary<NSString *, NSString *> *viewer = TVViewerForPath(self.currentPath);
        if (viewer && ![viewer[@"kind"] isEqualToString:self.kind]) {
            self.kind = viewer[@"kind"];
            [[NSUserDefaults standardUserDefaults] setObject:self.kind forKey:TVViewerDefaultsKey];
            [self selectCardForKind:self.kind];
                    self.datasets = @[];
            [self rebuildDatasetRows];
        }
        [self refreshWorkspaces];
        [self refreshDatasets:nil];
    } else if ([type isEqualToString:@"workspace"]) {
        [self refreshWorkspaces];
        [self refreshDatasets:nil];
    }
}

#pragma mark Dataset list

- (NSString *)factsForDataset:(NSDictionary *)dataset {
    NSMutableArray<NSString *> *parts = [NSMutableArray array];
    NSNumberFormatter *formatter = [[NSNumberFormatter alloc] init];
    formatter.numberStyle = NSNumberFormatterDecimalStyle;
    NSArray *samples = [dataset[@"slides"] isKindOfClass:[NSArray class]] ? dataset[@"slides"] : @[];
    if ([dataset[@"kind"] isEqual:@"crispr"] && [dataset[@"combined"] boolValue]) {
        [parts addObject:[NSString stringWithFormat:@"%lu screen%@", (unsigned long)samples.count, samples.count == 1 ? @"" : @"s"]];
    } else if ([dataset[@"kind"] isEqual:@"crispr"]) {
        [parts addObject:[dataset[@"screenKind"] isEqual:@"enhancer"] ? @"Enhancer screen" : @"Perturb-seq"];
        NSDictionary *stats = [dataset[@"stats"] isKindOfClass:[NSDictionary class]] ? dataset[@"stats"] : @{};
        if ([stats[@"targets"] isKindOfClass:[NSNumber class]]) [parts addObject:[NSString stringWithFormat:@"%@ targets", [formatter stringFromNumber:stats[@"targets"]]]];
        if ([stats[@"tests"] isKindOfClass:[NSNumber class]]) [parts addObject:[NSString stringWithFormat:@"%@ tests", [formatter stringFromNumber:stats[@"tests"]]]];
    } else {
        // combined datasets and projects: their samples (time points, sections, ...)
        if (samples.count > 1) [parts addObject:[NSString stringWithFormat:@"%lu samples", (unsigned long)samples.count]];
        if ([dataset[@"nCells"] isKindOfClass:[NSNumber class]]) [parts addObject:[NSString stringWithFormat:@"%@ cells", [formatter stringFromNumber:dataset[@"nCells"]]]];
    }
    for (NSString *key in @[@"technology", @"species"]) if ([dataset[key] isKindOfClass:[NSString class]] && [dataset[key] length]) [parts addObject:dataset[key]];
    return [parts componentsJoinedByString:@" · "];
}

static NSArray *TVSamples(NSDictionary *dataset) {
    return [dataset[@"slides"] isKindOfClass:[NSArray class]] ? dataset[@"slides"] : @[];
}

/** The dataset open in the window (by URL prefix) and its ?s= sample, if any. */
- (NSDictionary *)currentDataset:(NSString **)sampleId {
    for (NSDictionary *dataset in self.datasets) {
        NSString *url = dataset[@"url"];
        if (![url isKindOfClass:[NSString class]] || !url.length || ![self.currentPath hasPrefix:url]) continue;
        if (sampleId) {
            NSURLComponents *components = [NSURLComponents componentsWithString:[@"http://x" stringByAppendingString:self.currentPath]];
            for (NSURLQueryItem *item in components.queryItems ?: @[]) if ([item.name isEqualToString:@"s"]) *sampleId = item.value;
        }
        return dataset;
    }
    return nil;
}

/** Datasets with several samples (combined datasets, projects) are groups: their samples are listed under them. */
- (void)rebuildDatasetRows {
    NSDictionary *open = [self currentDataset:NULL];
    if (open && TVSamples(open).count > 1 && [open[@"id"] isKindOfClass:[NSString class]]) [self.expandedDatasets addObject:open[@"id"]];
    NSMutableArray<NSDictionary *> *rows = [NSMutableArray array];
    for (NSDictionary *dataset in self.datasets) {
        [rows addObject:@{@"dataset": dataset}];
        NSArray *samples = TVSamples(dataset);
        if (samples.count > 1 && [self.expandedDatasets containsObject:dataset[@"id"] ?: @""]) {
            for (NSDictionary *sample in samples) if ([sample isKindOfClass:[NSDictionary class]]) [rows addObject:@{@"dataset": dataset, @"sample": sample}];
        }
    }
    self.datasetRows = rows;
    [self.datasetTable reloadData];
    [self selectRowForCurrentPath];
}

- (void)selectRowForCurrentPath {
    self.updatingSelection = YES;
    NSString *sampleId = nil;
    NSDictionary *open = [self currentDataset:&sampleId];
    NSInteger match = -1;
    for (NSUInteger i = 0; open && i < self.datasetRows.count; i++) {
        NSDictionary *row = self.datasetRows[i];
        if (row[@"dataset"] != open) continue;
        if (match < 0) match = (NSInteger)i;  // the group, unless its open sample is listed
        NSDictionary *sample = row[@"sample"];
        if (sample && ([sample[@"id"] isEqual:sampleId] || (!sampleId && sample == TVSamples(open).firstObject))) { match = (NSInteger)i; break; }
    }
    if (match >= 0) [self.datasetTable selectRowIndexes:[NSIndexSet indexSetWithIndex:(NSUInteger)match] byExtendingSelection:NO];
    else [self.datasetTable deselectAll:nil];
    self.updatingSelection = NO;
}

- (NSInteger)numberOfRowsInTableView:(NSTableView *)tableView { return (NSInteger)self.datasetRows.count; }

- (CGFloat)tableView:(NSTableView *)tableView heightOfRow:(NSInteger)row {
    return row < (NSInteger)self.datasetRows.count && self.datasetRows[(NSUInteger)row][@"sample"] ? 28 : 46;
}

- (NSTableRowView *)tableView:(NSTableView *)tableView rowViewForRow:(NSInteger)row {
    TVRowView *rowView = [tableView makeViewWithIdentifier:@"DatasetRow" owner:self];
    if (!rowView) {
        rowView = [[TVRowView alloc] initWithFrame:NSZeroRect];
        rowView.identifier = @"DatasetRow";
    }
    return rowView;
}

- (NSTableCellView *)sampleCell {
    NSTableCellView *cell = [self.datasetTable makeViewWithIdentifier:@"SampleCell" owner:self];
    if (cell) return cell;
    cell = [[NSTableCellView alloc] initWithFrame:NSZeroRect];
    cell.identifier = @"SampleCell";
    GCFillView *rule = [[GCFillView alloc] initWithFrame:NSZeroRect];  // follows the light / dark theme
    rule.translatesAutoresizingMaskIntoConstraints = NO;
    rule.fillColor = GCBorderStrongColor();
    rule.cornerRadius = 1;
    NSTextField *name = [NSTextField labelWithString:@""];
    name.translatesAutoresizingMaskIntoConstraints = NO;
    name.font = GCUIFont(12.5, 500);
    name.textColor = GCText2Color();
    name.lineBreakMode = NSLineBreakByTruncatingTail;
    name.identifier = @"SampleName";
    [name setContentCompressionResistancePriority:NSLayoutPriorityDefaultLow forOrientation:NSLayoutConstraintOrientationHorizontal];
    [cell addSubview:rule];
    [cell addSubview:name];
    [NSLayoutConstraint activateConstraints:@[
        [rule.leadingAnchor constraintEqualToAnchor:cell.leadingAnchor constant:16],
        [rule.widthAnchor constraintEqualToConstant:2],
        [rule.topAnchor constraintEqualToAnchor:cell.topAnchor constant:2],
        [rule.bottomAnchor constraintEqualToAnchor:cell.bottomAnchor constant:-2],
        [name.leadingAnchor constraintEqualToAnchor:rule.trailingAnchor constant:10],
        [name.trailingAnchor constraintLessThanOrEqualToAnchor:cell.trailingAnchor constant:-8],
        [name.centerYAnchor constraintEqualToAnchor:cell.centerYAnchor],
    ]];
    return cell;
}

- (NSView *)tableView:(NSTableView *)tableView viewForTableColumn:(NSTableColumn *)tableColumn row:(NSInteger)row {
    NSDictionary *entry = self.datasetRows[(NSUInteger)row];
    NSDictionary *dataset = entry[@"dataset"];
    NSDictionary *sample = entry[@"sample"];
    if (sample) {
        NSTableCellView *cell = [self sampleCell];
        NSTextField *nameField = (NSTextField *)GCFindView(cell, @"SampleName");
        nameField.stringValue = [sample[@"title"] isKindOfClass:[NSString class]] ? sample[@"title"] : @"Sample";
        nameField.toolTip = [sample[@"description"] isKindOfClass:[NSString class]] ? sample[@"description"] : nameField.stringValue;
        return cell;
    }
    NSTableCellView *cell = [tableView makeViewWithIdentifier:@"DatasetCell" owner:self];
    if (!cell) {
        cell = [[NSTableCellView alloc] initWithFrame:NSZeroRect];
        cell.identifier = @"DatasetCell";
        NSButton *disclosure = [NSButton buttonWithImage:[NSImage imageWithSystemSymbolName:@"chevron.right" accessibilityDescription:@"Show samples"] target:self action:@selector(toggleSamples:)];
        disclosure.translatesAutoresizingMaskIntoConstraints = NO;
        disclosure.bordered = NO;
        disclosure.imageScaling = NSImageScaleProportionallyDown;
        disclosure.contentTintColor = GCText3Color();
        disclosure.identifier = @"DatasetDisclosure";
        NSTextField *name = [NSTextField labelWithString:@""];
        name.translatesAutoresizingMaskIntoConstraints = NO;
        name.font = GCUIFont(13, 600);
        name.textColor = GCTextColor();
        name.lineBreakMode = NSLineBreakByTruncatingTail;
        name.identifier = @"DatasetName";
        [name setContentCompressionResistancePriority:NSLayoutPriorityDefaultLow forOrientation:NSLayoutConstraintOrientationHorizontal];
        NSTextField *facts = [NSTextField labelWithString:@""];
        facts.translatesAutoresizingMaskIntoConstraints = NO;
        facts.font = GCUIFont(11.5, 450);
        facts.textColor = GCText3Color();
        facts.lineBreakMode = NSLineBreakByTruncatingTail;
        facts.identifier = @"DatasetFacts";
        [facts setContentCompressionResistancePriority:NSLayoutPriorityDefaultLow forOrientation:NSLayoutConstraintOrientationHorizontal];
        [cell addSubview:name];
        [cell addSubview:facts];
        [cell addSubview:disclosure];
        [NSLayoutConstraint activateConstraints:@[
            [name.leadingAnchor constraintEqualToAnchor:cell.leadingAnchor constant:10],
            [name.trailingAnchor constraintLessThanOrEqualToAnchor:disclosure.leadingAnchor constant:-4],
            [name.topAnchor constraintEqualToAnchor:cell.topAnchor constant:6],
            [facts.leadingAnchor constraintEqualToAnchor:name.leadingAnchor],
            [facts.trailingAnchor constraintLessThanOrEqualToAnchor:disclosure.leadingAnchor constant:-4],
            [facts.topAnchor constraintEqualToAnchor:name.bottomAnchor constant:1],
            [disclosure.trailingAnchor constraintEqualToAnchor:cell.trailingAnchor constant:-6],
            [disclosure.centerYAnchor constraintEqualToAnchor:cell.centerYAnchor],
            [disclosure.widthAnchor constraintEqualToConstant:22],
            [disclosure.heightAnchor constraintEqualToConstant:22],
        ]];
    }
    NSTextField *nameField = (NSTextField *)GCFindView(cell, @"DatasetName");
    NSTextField *factsField = (NSTextField *)GCFindView(cell, @"DatasetFacts");
    NSButton *disclosure = (NSButton *)GCFindView(cell, @"DatasetDisclosure");
    nameField.stringValue = [dataset[@"title"] isKindOfClass:[NSString class]] ? dataset[@"title"] : @"Dataset";
    nameField.toolTip = nameField.stringValue;
    factsField.stringValue = [self factsForDataset:dataset];
    BOOL group = TVSamples(dataset).count > 1;
    BOOL open = group && [self.expandedDatasets containsObject:dataset[@"id"] ?: @""];
    disclosure.hidden = !group;
    disclosure.image = [NSImage imageWithSystemSymbolName:open ? @"chevron.down" : @"chevron.right" accessibilityDescription:open ? @"Hide samples" : @"Show samples"];
    disclosure.toolTip = open ? @"Hide the samples" : @"Show the samples";
    return cell;
}

- (void)toggleSamples:(NSButton *)sender {
    NSInteger row = [self.datasetTable rowForView:sender];
    if (row < 0 || row >= (NSInteger)self.datasetRows.count) return;
    NSString *datasetId = self.datasetRows[(NSUInteger)row][@"dataset"][@"id"];
    if (![datasetId isKindOfClass:[NSString class]]) return;
    if ([self.expandedDatasets containsObject:datasetId]) [self.expandedDatasets removeObject:datasetId];
    else [self.expandedDatasets addObject:datasetId];
    [self rebuildDatasetRows];
}

- (void)tableViewSelectionDidChange:(NSNotification *)notification {
    if (self.updatingSelection) return;
    NSInteger row = self.datasetTable.selectedRow;
    if (row < 0 || row >= (NSInteger)self.datasetRows.count) return;
    NSString *url = [self urlForRow:self.datasetRows[(NSUInteger)row]];
    if (url.length && ![self.currentPath isEqualToString:url] && !(!self.datasetRows[(NSUInteger)row][@"sample"] && [self.currentPath hasPrefix:url])) [self loadPage:url];
}

/** A dataset row opens the dataset; a sample row opens that sample (?s=). */
- (NSString *)urlForRow:(NSDictionary *)entry {
    NSString *url = entry[@"dataset"][@"url"];
    if (![url isKindOfClass:[NSString class]]) return nil;
    NSString *sampleId = entry[@"sample"][@"id"];
    if (![sampleId isKindOfClass:[NSString class]]) return url;
    NSURLComponents *components = [NSURLComponents componentsWithString:url];
    components.queryItems = @[[NSURLQueryItem queryItemWithName:@"s" value:sampleId]];
    return components.string;
}

#pragma mark Dataset management (right-click a dataset; the Datasets page offers the same)

- (void)menuNeedsUpdate:(NSMenu *)menu {
    [menu removeAllItems];
    NSInteger row = self.datasetTable.clickedRow;
    if (row >= 0 && row < (NSInteger)self.datasetRows.count) {
        NSDictionary *entry = self.datasetRows[(NSUInteger)row];
        NSDictionary *dataset = entry[@"dataset"];
        NSMenuItem *open = [menu addItemWithTitle:entry[@"sample"] ? @"Open Sample" : @"Open" action:@selector(openDatasetRow:) keyEquivalent:@""];
        open.representedObject = entry;
        open.target = self;
        if (entry[@"sample"]) return;
        [menu addItem:[NSMenuItem separatorItem]];
        if ([dataset[@"combined"] boolValue] || [dataset[@"layout"] isEqual:@"project"]) {
            [menu addItemWithTitle:@"Edit Samples…" action:@selector(editSamples:) keyEquivalent:@""].representedObject = dataset;
        }
        [menu addItemWithTitle:@"Rename…" action:@selector(renameDataset:) keyEquivalent:@""].representedObject = dataset;
        [menu addItemWithTitle:@"Remove…" action:@selector(removeDataset:) keyEquivalent:@""].representedObject = dataset;
        [menu addItem:[NSMenuItem separatorItem]];
    }
    [menu addItemWithTitle:[self.kind isEqualToString:@"crispr"] ? @"Combine Screens…" : @"Combine Datasets…" action:@selector(combineDatasets:) keyEquivalent:@""];
    [menu addItemWithTitle:@"Add and Manage Datasets" action:@selector(manageDatasets:) keyEquivalent:@""];
    for (NSMenuItem *item in menu.itemArray) if (!item.isSeparatorItem) item.target = self;
}

- (void)openDatasetRow:(NSMenuItem *)sender { NSString *url = [self urlForRow:sender.representedObject]; if (url.length) [self loadPage:url]; }

- (NSString *)viewerPagePath { return [NSString stringWithFormat:@"/%@/", TVViewerForKind(self.kind)[@"path"]]; }

/** The Datasets page opens its samples dialog: ?combine=1 or ?edit=<dataset>. */
- (void)combineDatasets:(id)sender { [self loadPage:[[self viewerPagePath] stringByAppendingString:@"?combine=1"]]; }
- (void)editSamples:(NSMenuItem *)sender {
    NSString *datasetId = sender.representedObject[@"id"];
    if (![datasetId isKindOfClass:[NSString class]]) return;
    NSURLComponents *components = [NSURLComponents componentsWithString:[self viewerPagePath]];
    components.queryItems = @[[NSURLQueryItem queryItemWithName:@"edit" value:datasetId]];
    [self loadPage:components.string];
}

/** After a change: the list, and the page if it shows the changed dataset or the Datasets page. */
- (void)datasetChanged:(NSDictionary *)dataset removed:(BOOL)removed {
    [self refreshDatasets:nil];
    NSString *url = dataset[@"url"];
    BOOL showing = [url isKindOfClass:[NSString class]] && [self.currentPath hasPrefix:url];
    BOOL datasetsPage = [[self.currentPath componentsSeparatedByString:@"?"].firstObject isEqualToString:[self viewerPagePath]];
    if (removed && showing) [self showViewer:self.kind];
    else if (showing || datasetsPage) [self.webView reload];
}

- (void)renameDataset:(NSMenuItem *)sender {
    NSDictionary *dataset = sender.representedObject;
    NSAlert *alert = [[NSAlert alloc] init];
    alert.messageText = @"Rename Dataset";
    alert.informativeText = @"The name shown in the lists and the viewer; the link stays the same.";
    NSTextField *field = [[NSTextField alloc] initWithFrame:NSMakeRect(0, 0, 300, 24)];
    field.stringValue = [dataset[@"title"] isKindOfClass:[NSString class]] ? dataset[@"title"] : @"";
    alert.accessoryView = field;
    [alert addButtonWithTitle:@"Rename"];
    [alert addButtonWithTitle:@"Cancel"];
    alert.window.initialFirstResponder = field;
    [alert beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse response) {
        NSString *title = [field.stringValue stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
        if (response != NSAlertFirstButtonReturn || !title.length || [title isEqualToString:dataset[@"title"]]) return;
        [self callDesktopMethod:@"renameDataset" arguments:@[dataset[@"id"], title] completion:^(id result) { [self datasetChanged:dataset removed:NO]; }];
    }];
}

- (void)removeDataset:(NSMenuItem *)sender {
    NSDictionary *dataset = sender.representedObject;
    NSAlert *alert = [[NSAlert alloc] init];
    alert.alertStyle = NSAlertStyleWarning;
    alert.messageText = [NSString stringWithFormat:@"Remove “%@” from your workspace?", dataset[@"title"]];
    alert.informativeText = [dataset[@"combined"] boolValue]
        ? @"Its link stops working. The datasets it combines are kept and listed on their own again."
        : @"Its link stops working. Source data and converted files are kept (delete converted files on the Datasets page).";
    [alert addButtonWithTitle:@"Remove"];
    [alert addButtonWithTitle:@"Cancel"];
    [alert beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse response) {
        if (response != NSAlertFirstButtonReturn) return;
        [self callDesktopMethod:@"removeDataset" arguments:@[dataset[@"id"]] completion:^(id result) { [self datasetChanged:dataset removed:YES]; }];
    }];
}

#pragma mark Appearance (System / Light / Dark), as in Genome Canvas Desktop

- (NSString *)appearancePreference {
    NSString *value = [[NSUserDefaults standardUserDefaults] stringForKey:TVAppearanceDefaultsKey];
    return [@[@"light", @"dark"] containsObject:value] ? value : @"system";
}

- (void)setAppearancePreference:(NSString *)preference {
    NSUserDefaults *defaults = [NSUserDefaults standardUserDefaults];
    if ([preference isEqualToString:@"light"] || [preference isEqualToString:@"dark"]) [defaults setObject:preference forKey:TVAppearanceDefaultsKey];
    else [defaults removeObjectForKey:TVAppearanceDefaultsKey];
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
    for (TVViewerCard *card in self.cards) card.chosen = card.chosen;
    // the page follows the window (its own theme button is hidden in desktop mode)
    [self callDesktopMethod:@"setTheme" arguments:@[dark ? @"dark" : @"light"] completion:nil];
}

- (void)toggleAppearance:(id)sender {
    NSString *next = GCAppearanceIsDark(NSApp.effectiveAppearance) ? @"light" : @"dark";
    BOOL matchesSystem = [next isEqualToString:@"dark"] == [self systemPrefersDark];
    [self setAppearancePreference:matchesSystem ? @"system" : next];
}

- (void)chooseAppearance:(NSMenuItem *)sender { [self setAppearancePreference:sender.representedObject]; }

#pragma mark Preview snapshots (OMICS_CANVAS_SNAPSHOT_DIR, for CI preview builds)

- (NSString *)snapshotDirectory { return [NSProcessInfo processInfo].environment[@"OMICS_CANVAS_SNAPSHOT_DIR"]; }

- (void)scheduleSnapshotCaptureIfRequested {
    NSString *directory = [self snapshotDirectory];
    if (!directory.length || self.snapshotScheduled) return;
    self.snapshotScheduled = YES;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(8 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
        // CI cannot reach the lab server: OMICS_CANVAS_SNAPSHOT_DATASETS (a JSON list as /hub/api/datasets returns,
        // and "currentPath") fills the sidebar so the dataset list can be reviewed
        NSString *fixture = [NSProcessInfo processInfo].environment[@"OMICS_CANVAS_SNAPSHOT_DATASETS"];
        NSData *data = fixture.length ? [NSData dataWithContentsOfFile:fixture] : nil;
        NSDictionary *sample = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
        if ([sample isKindOfClass:[NSDictionary class]] && [sample[@"datasets"] isKindOfClass:[NSArray class]]) {
            self.datasets = sample[@"datasets"];
            self.currentPath = [sample[@"currentPath"] isKindOfClass:[NSString class]] ? sample[@"currentPath"] : @"";
            self.emptyLabel.hidden = YES;
            self.datasetCountLabel.stringValue = [NSString stringWithFormat:@"%lu", (unsigned long)self.datasets.count];
            [self rebuildDatasetRows];
        }
        // documentation screenshots against a test server: OMICS_CANVAS_SNAPSHOT_WORKSPACE (workspace id) and
        // OMICS_CANVAS_SNAPSHOT_PAGES ("name=/path;name=/path"): each page is captured as <name>.png
        NSDictionary *env = [NSProcessInfo processInfo].environment;
        NSString *pages = env[@"OMICS_CANVAS_SNAPSHOT_PAGES"];
        if (pages.length) {
            // a desktop-sized window (the runner's screen is small; the viewers need the width for their side panel)
            NSLog(@"snapshot screen %@", NSStringFromRect(NSScreen.mainScreen.frame));
            [self.window setFrame:NSMakeRect(0, 0, 1480, 920) display:YES];
            NSString *workspace = env[@"OMICS_CANVAS_SNAPSHOT_WORKSPACE"];
            void (^run)(void) = ^{ [self capturePages:[pages componentsSeparatedByString:@";"] directory:directory]; };
            if (workspace.length) [self callDesktopMethod:@"selectWorkspace" arguments:@[workspace] completion:^(id result) { [self refreshWorkspaces]; run(); }];
            else run();
            return;
        }
        [self captureThemes:@[@"light", @"dark"] directory:directory];
    });
}

- (void)capturePages:(NSArray<NSString *> *)pages directory:(NSString *)directory {
    if (!pages.count) { [NSApp terminate:nil]; return; }
    NSArray<NSString *> *parts = [pages.firstObject componentsSeparatedByString:@"="];
    NSArray<NSString *> *rest = [pages subarrayWithRange:NSMakeRange(1, pages.count - 1)];
    if (parts.count < 2) { [self capturePages:rest directory:directory]; return; }
    NSString *name = parts.firstObject, *path = [[parts subarrayWithRange:NSMakeRange(1, parts.count - 1)] componentsJoinedByString:@"="];
    NSString *theme = [name hasSuffix:@"-dark"] ? @"dark" : @"light";
    [self setAppearancePreference:theme];
    NSDictionary<NSString *, NSString *> *viewer = TVViewerForPath([path componentsSeparatedByString:@"?"].firstObject);
    if (viewer) { self.kind = viewer[@"kind"]; [self selectCardForKind:self.kind]; }
    [self loadPage:path];
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(14 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
        [self refreshDatasets:nil];
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(2 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
            [self captureWindowToPath:[directory stringByAppendingPathComponent:[name stringByAppendingString:@".png"]] completion:^{
                [self capturePages:rest directory:directory];
            }];
        });
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

#pragma mark Lifecycle and menu

- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender { return YES; }

- (void)applicationWillTerminate:(NSNotification *)notification {
    [self.webView.configuration.userContentController removeScriptMessageHandlerForName:@"vizhub"];
}

- (void)dealloc {
    @try { [self.webView removeObserver:self forKeyPath:@"estimatedProgress"]; } @catch (__unused NSException *exception) {}
    @try { [self.webView removeObserver:self forKeyPath:@"loading"]; } @catch (__unused NSException *exception) {}
    @try { [self.webView removeObserver:self forKeyPath:@"canGoBack"]; } @catch (__unused NSException *exception) {}
    @try { [NSApp removeObserver:self forKeyPath:@"effectiveAppearance"]; } @catch (__unused NSException *exception) {}
}

- (NSMenuItem *)menuItem:(NSString *)title action:(SEL)action key:(NSString *)key modifiers:(NSEventModifierFlags)modifiers {
    NSMenuItem *item = [[NSMenuItem alloc] initWithTitle:title action:action keyEquivalent:key];
    item.target = self;
    if (modifiers) item.keyEquivalentModifierMask = modifiers;
    return item;
}

- (void)buildApplicationMenu {
    NSMenu *mainMenu = [[NSMenu alloc] initWithTitle:@""];

    NSMenuItem *applicationItem = [[NSMenuItem alloc] initWithTitle:@"" action:nil keyEquivalent:@""];
    NSMenu *applicationMenu = [[NSMenu alloc] initWithTitle:@"Omics Canvas"];
    [applicationMenu addItemWithTitle:@"About Omics Canvas" action:@selector(orderFrontStandardAboutPanel:) keyEquivalent:@""];
    [applicationMenu addItem:[NSMenuItem separatorItem]];
    [applicationMenu addItem:[self menuItem:@"Connect to Server…" action:@selector(editServerAddress:) key:@"," modifiers:0]];
    [applicationMenu addItem:[NSMenuItem separatorItem]];
    [applicationMenu addItemWithTitle:@"Hide Omics Canvas" action:@selector(hide:) keyEquivalent:@"h"];
    [applicationMenu addItemWithTitle:@"Quit Omics Canvas" action:@selector(terminate:) keyEquivalent:@"q"];
    applicationItem.submenu = applicationMenu;
    [mainMenu addItem:applicationItem];

    NSMenuItem *fileItem = [[NSMenuItem alloc] initWithTitle:@"" action:nil keyEquivalent:@""];
    NSMenu *fileMenu = [[NSMenu alloc] initWithTitle:@"File"];
    [fileMenu addItem:[self menuItem:@"Add or Manage Datasets…" action:@selector(manageDatasets:) key:@"o" modifiers:0]];
    self.sharedMenuItem = [self menuItem:@"Shared Datasets…" action:@selector(showShared:) key:@"l" modifiers:NSEventModifierFlagCommand | NSEventModifierFlagShift];
    [fileMenu addItem:self.sharedMenuItem];
    [fileMenu addItem:[NSMenuItem separatorItem]];
    [fileMenu addItem:[self menuItem:@"Export PNG…" action:@selector(exportPNG:) key:@"e" modifiers:NSEventModifierFlagCommand | NSEventModifierFlagShift]];
    [fileMenu addItem:[self menuItem:@"Open in Browser" action:@selector(openInBrowser:) key:@"b" modifiers:NSEventModifierFlagCommand | NSEventModifierFlagShift]];
    [fileMenu addItem:[NSMenuItem separatorItem]];
    [fileMenu addItemWithTitle:@"Close Window" action:@selector(performClose:) keyEquivalent:@"w"];
    fileItem.submenu = fileMenu;
    [mainMenu addItem:fileItem];

    NSMenuItem *editItem = [[NSMenuItem alloc] initWithTitle:@"" action:nil keyEquivalent:@""];
    NSMenu *editMenu = [[NSMenu alloc] initWithTitle:@"Edit"];
    [editMenu addItemWithTitle:@"Undo" action:@selector(undo:) keyEquivalent:@"z"];
    [editMenu addItemWithTitle:@"Redo" action:@selector(redo:) keyEquivalent:@"Z"];
    [editMenu addItem:[NSMenuItem separatorItem]];
    [editMenu addItemWithTitle:@"Cut" action:@selector(cut:) keyEquivalent:@"x"];
    [editMenu addItemWithTitle:@"Copy" action:@selector(copy:) keyEquivalent:@"c"];
    [editMenu addItemWithTitle:@"Paste" action:@selector(paste:) keyEquivalent:@"v"];
    [editMenu addItemWithTitle:@"Select All" action:@selector(selectAll:) keyEquivalent:@"a"];
    editItem.submenu = editMenu;
    [mainMenu addItem:editItem];

    NSMenuItem *viewItem = [[NSMenuItem alloc] initWithTitle:@"" action:nil keyEquivalent:@""];
    NSMenu *viewMenu = [[NSMenu alloc] initWithTitle:@"View"];
    NSMutableArray<NSMenuItem *> *viewerItems = [NSMutableArray array];
    NSInteger number = 1;
    for (NSDictionary<NSString *, NSString *> *viewer in TVViewers()) {
        NSMenuItem *item = [self menuItem:viewer[@"title"] action:@selector(chooseViewerFromMenu:) key:[NSString stringWithFormat:@"%ld", (long)number++] modifiers:0];
        item.representedObject = viewer[@"kind"];
        [viewMenu addItem:item];
        [viewerItems addObject:item];
    }
    self.viewerMenuItems = viewerItems;
    [viewMenu addItem:[NSMenuItem separatorItem]];
    [viewMenu addItem:[self menuItem:@"Back" action:@selector(goBack:) key:@"[" modifiers:0]];
    [viewMenu addItem:[self menuItem:@"Reload" action:@selector(reloadOrStop:) key:@"r" modifiers:0]];
    [viewMenu addItem:[NSMenuItem separatorItem]];
    NSMenuItem *appearanceItem = [[NSMenuItem alloc] initWithTitle:@"Appearance" action:nil keyEquivalent:@""];
    NSMenu *appearanceMenu = [[NSMenu alloc] initWithTitle:@"Appearance"];
    NSMutableArray<NSMenuItem *> *appearanceItems = [NSMutableArray array];
    for (NSArray<NSString *> *entry in @[@[@"Use System Setting", @"system"], @[@"Light", @"light"], @[@"Dark", @"dark"]]) {
        NSMenuItem *item = [self menuItem:entry[0] action:@selector(chooseAppearance:) key:@"" modifiers:0];
        item.representedObject = entry[1];
        [appearanceMenu addItem:item];
        [appearanceItems addObject:item];
    }
    [appearanceMenu addItem:[NSMenuItem separatorItem]];
    [appearanceMenu addItem:[self menuItem:@"Toggle Light/Dark" action:@selector(toggleAppearance:) key:@"t" modifiers:NSEventModifierFlagCommand | NSEventModifierFlagShift]];
    self.appearanceMenuItems = appearanceItems;
    appearanceItem.submenu = appearanceMenu;
    [viewMenu addItem:appearanceItem];
    viewItem.submenu = viewMenu;
    [mainMenu addItem:viewItem];

    NSMenuItem *windowItem = [[NSMenuItem alloc] initWithTitle:@"" action:nil keyEquivalent:@""];
    NSMenu *windowMenu = [[NSMenu alloc] initWithTitle:@"Window"];
    [windowMenu addItemWithTitle:@"Minimize" action:@selector(performMiniaturize:) keyEquivalent:@"m"];
    [windowMenu addItemWithTitle:@"Zoom" action:@selector(performZoom:) keyEquivalent:@""];
    windowItem.submenu = windowMenu;
    [mainMenu addItem:windowItem];
    NSApp.windowsMenu = windowMenu;

    [NSApp setMainMenu:mainMenu];
}

#pragma mark Loading state

- (void)reloadOrStop:(id)sender {
    if (self.webView.loading) { [self.webView stopLoading]; [self setStatus:@"Stopped" state:@"ready"]; }
    else if (self.webView.URL) { [self setStatus:@"Loading" state:@"busy"]; [self.webView reloadFromOrigin]; }
    else [self showViewer:self.kind];
}

- (void)observeValueForKeyPath:(NSString *)keyPath ofObject:(id)object change:(NSDictionary<NSKeyValueChangeKey, id> *)change context:(void *)context {
    if ([keyPath isEqualToString:@"estimatedProgress"] || [keyPath isEqualToString:@"loading"]) {
        CGFloat width = self.progressBar.superview.bounds.size.width;
        self.progressWidthConstraint.constant = width * self.webView.estimatedProgress;
        self.progressBar.hidden = !self.webView.loading;
        self.reloadButton.image = GCSymbol(self.webView.loading ? @"xmark" : @"arrow.clockwise", 13, @"Reload or stop");
        self.reloadButton.toolTip = self.webView.loading ? @"Stop loading" : @"Reload";
        [self.reloadButton refreshColors];
        return;
    }
    if ([keyPath isEqualToString:@"canGoBack"]) { self.backButton.enabled = self.webView.canGoBack; return; }
    if ([keyPath isEqualToString:@"effectiveAppearance"]) {
        dispatch_async(dispatch_get_main_queue(), ^{ [self appearanceDidChange]; });
        return;
    }
    [super observeValueForKeyPath:keyPath ofObject:object change:change context:context];
}

#pragma mark Navigation, downloads and dialogs (as in Genome Canvas Desktop)

- (void)webView:(WKWebView *)webView didStartProvisionalNavigation:(WKNavigation *)navigation {
    if (self.showingErrorPage) { self.showingErrorPage = NO; return; }  // the local "not reachable" page keeps the status
    [self setStatus:@"Loading" state:@"busy"];
    self.progressBar.hidden = NO;
}

- (void)webView:(WKWebView *)webView didFinishNavigation:(WKNavigation *)navigation {
    self.progressBar.hidden = YES;
    if (![webView.URL.scheme isEqualToString:@"http"] && ![webView.URL.scheme isEqualToString:@"https"]) return;  // the local "not reachable" page
    NSString *title = webView.title ?: @"";
    NSRange suffix = [title rangeOfString:@" · Omics Canvas"];
    if (suffix.location != NSNotFound) title = [title substringToIndex:suffix.location];
    self.pageTitleLabel.stringValue = title;
    self.window.title = title.length ? title : @"Omics Canvas";
    [self setStatus:@"Connected" state:@"ready"];
    [self appearanceDidChange];
    [self scheduleSnapshotCaptureIfRequested];
}

- (void)webView:(WKWebView *)webView didFailNavigation:(WKNavigation *)navigation withError:(NSError *)error { [self showNavigationError:error]; }
- (void)webView:(WKWebView *)webView didFailProvisionalNavigation:(WKNavigation *)navigation withError:(NSError *)error { [self showNavigationError:error]; }
- (void)showNavigationError:(NSError *)error {
    if (error.code == NSURLErrorCancelled) return;
    [self setStatus:@"Server unavailable" state:@"error"];
    self.statusLabel.toolTip = [error.localizedDescription stringByAppendingString:@" — from outside Stanford, connect the VPN."];
    self.progressBar.hidden = YES;
    // a themed explanation instead of an empty white page; its colors follow the window (prefers-color-scheme)
    NSString *retry = [self pageURL:[NSString stringWithFormat:@"/%@/", TVViewerForKind(self.kind)[@"path"]]].absoluteString ?: @"";
    NSString *server = [self serverRoot].absoluteString ?: @"";
    NSString *html = [NSString stringWithFormat:
        @"<!doctype html><meta charset=utf-8><style>:root{color-scheme:light dark;--bg:#e5e9f0;--text:#2e3440;--muted:#69728a;--accent:#4c7a86}"
        "@media (prefers-color-scheme:dark){:root{--bg:#242933;--text:#eceff4;--muted:#9aa5b8;--accent:#88c0d0}}"
        "html,body{margin:0;height:100%%;background:var(--bg);color:var(--text);font:14px -apple-system,sans-serif}"
        "main{height:100%%;display:grid;place-content:center;text-align:center;gap:6px;padding:24px}h1{font:600 19px Georgia,serif;margin:0 0 4px}"
        "p{margin:0;color:var(--muted);max-width:440px}code{font:12px ui-monospace,monospace}a{color:var(--accent);font-weight:600}</style>"
        "<main><h1>The Omics Canvas server is not reachable</h1><p>Could not connect to <code>%@</code>.</p>"
        "<p>From outside Stanford, connect the VPN first. Use <b>Server…</b> in the sidebar for another address.</p>"
        "<p style='margin-top:10px'><a href='%@'>Try again</a></p></main>",
        server, retry];
    self.showingErrorPage = YES;
    [self.webView loadHTMLString:html baseURL:nil];
    [self scheduleSnapshotCaptureIfRequested];  // preview builds (CI) cannot reach the lab server: capture the window anyway
}
- (void)webViewWebContentProcessDidTerminate:(WKWebView *)webView { [self setStatus:@"Page process stopped" state:@"error"]; }

- (BOOL)isViewerURL:(NSURL *)url {
    NSURL *root = [self serverRoot];
    if (!root || ![url.host isEqualToString:root.host] || !((url.port == nil && root.port == nil) || [url.port isEqual:root.port])) return NO;
    NSString *path = url.path ?: @"";
    return TVViewerForPath(path) != nil || [path hasPrefix:@"/hub/api/"] || [path hasPrefix:@"/hub/static/"] || [path hasPrefix:@"/genome-canvas/api/"];
}

// Links leaving the viewers. Genome Canvas pages on this server go to Genome Canvas.app when it is installed
// (genomecanvas://open?locus=…&highlight=…, opened on the server that app uses); otherwise, and for the hub
// home page, JupyterLab and other sites, the default browser.
- (void)openOutside:(NSURL *)url {
    if (!url) return;
    NSURL *root = [self serverRoot];
    BOOL sameServer = root && [url.host isEqualToString:root.host] && ((url.port == nil && root.port == nil) || [url.port isEqual:root.port]);
    // NSURL.path drops the trailing slash ("/genome-canvas/?locus=…" -> "/genome-canvas"); the components keep it
    NSString *path = [NSURLComponents componentsWithURL:url resolvingAgainstBaseURL:YES].path ?: @"";
    BOOL canvasPage = [path isEqualToString:@"/genome-canvas"] || ([path hasPrefix:@"/genome-canvas/"] && ![path hasPrefix:@"/genome-canvas/api/"]);
    if (sameServer && canvasPage) {
        NSURLComponents *link = [NSURLComponents componentsWithString:@"genomecanvas://open"];
        NSMutableArray<NSURLQueryItem *> *items = [NSMutableArray array];
        for (NSURLQueryItem *item in [NSURLComponents componentsWithURL:url resolvingAgainstBaseURL:NO].queryItems ?: @[]) {
            if (![@[@"locus", @"genome", @"highlight", @"color", @"track"] containsObject:item.name]) continue;
            // track=<name>|/server/path: make the path absolute, Genome Canvas.app may be on another server or its local backend
            NSRange bar = [item.value rangeOfString:@"|" options:NSBackwardsSearch];
            if ([item.name isEqualToString:@"track"] && bar.location != NSNotFound && bar.location + 1 < item.value.length && [item.value characterAtIndex:bar.location + 1] == '/') {
                NSURL *absolute = [NSURL URLWithString:[item.value substringFromIndex:bar.location + 1] relativeToURL:url];
                [items addObject:[NSURLQueryItem queryItemWithName:@"track" value:[NSString stringWithFormat:@"%@|%@", [item.value substringToIndex:bar.location], absolute.absoluteString]]];
                continue;
            }
            [items addObject:item];
        }
        link.queryItems = items.count ? items : nil;
        NSURL *app = [self genomeCanvasApp];
        if (link.URL && app) {
            // sent to that app directly: Launch Services may not have registered the scheme of a freshly copied app yet
            NSWorkspaceOpenConfiguration *configuration = [NSWorkspaceOpenConfiguration configuration];
            configuration.activates = YES;
            [[NSWorkspace sharedWorkspace] openURLs:@[link.URL] withApplicationAtURL:app configuration:configuration
                                  completionHandler:^(NSRunningApplication *running, NSError *error) {
                if (!error) return;
                dispatch_async(dispatch_get_main_queue(), ^{ [[NSWorkspace sharedWorkspace] openURL:url]; });
            }];
            return;
        }
        if ([[NSWorkspace sharedWorkspace] URLForApplicationWithBundleIdentifier:TVGenomeCanvasBundleID]) {
            [self setStatus:@"Update Genome Canvas.app to 1.7 to open links in it" state:@"ready"];
        }
    }
    [[NSWorkspace sharedWorkspace] openURL:url];
}

// Genome Canvas.app (1.7 or later: it declares genomecanvas:// links), or nil
- (NSURL *)genomeCanvasApp {
    NSWorkspace *workspace = [NSWorkspace sharedWorkspace];
    NSMutableArray<NSURL *> *candidates = [NSMutableArray array];
    NSURL *handler = [workspace URLForApplicationToOpenURL:[NSURL URLWithString:@"genomecanvas://open"]];
    if (handler) [candidates addObject:handler];
    NSURL *byId = [workspace URLForApplicationWithBundleIdentifier:TVGenomeCanvasBundleID];
    if (byId) [candidates addObject:byId];
    for (NSString *dir in @[@"/Applications", [NSHomeDirectory() stringByAppendingPathComponent:@"Applications"]]) {
        [candidates addObject:[NSURL fileURLWithPath:[dir stringByAppendingPathComponent:@"Genome Canvas.app"]]];
    }
    for (NSURL *candidate in candidates) {
        NSDictionary *info = [NSBundle bundleWithURL:candidate].infoDictionary;
        for (NSDictionary *type in info[@"CFBundleURLTypes"] ?: @[]) {
            if ([type[@"CFBundleURLSchemes"] containsObject:@"genomecanvas"]) return candidate;
        }
    }
    return nil;
}

- (void)webView:(WKWebView *)webView decidePolicyForNavigationAction:(WKNavigationAction *)navigationAction decisionHandler:(void (^)(WKNavigationActionPolicy))decisionHandler {
    if (navigationAction.shouldPerformDownload) { decisionHandler(WKNavigationActionPolicyDownload); return; }
    NSURL *url = navigationAction.request.URL;
    NSString *scheme = url.scheme.lowercaseString;
    if ([@[@"blob", @"about", @"data"] containsObject:scheme ?: @""]) { decisionHandler(WKNavigationActionPolicyAllow); return; }
    // this app shows the three viewers; everything else leaves through openOutside:
    if (navigationAction.targetFrame.isMainFrame && ![self isViewerURL:url]) {
        [self openOutside:url];
        decisionHandler(WKNavigationActionPolicyCancel);
        return;
    }
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
    panel.title = @"Save File";
    [panel beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse result) {
        if (result != NSModalResponseOK || !panel.URL) { completionHandler(nil); return; }
        NSURL *destination = panel.URL;
        if ([[NSFileManager defaultManager] fileExistsAtPath:destination.path]) [[NSFileManager defaultManager] removeItemAtURL:destination error:nil];
        [self.downloadDestinations setObject:destination forKey:download];
        [self setStatus:@"Saving" state:@"busy"];
        completionHandler(destination);
    }];
}
- (void)downloadDidFinish:(WKDownload *)download {
    NSURL *destination = [self.downloadDestinations objectForKey:download];
    [self.downloadDestinations removeObjectForKey:download];
    [self setStatus:(destination.lastPathComponent.length ? [@"Saved " stringByAppendingString:destination.lastPathComponent] : @"Saved") state:@"ready"];
}
- (void)download:(WKDownload *)download didFailWithError:(NSError *)error resumeData:(NSData *)resumeData {
    [self.downloadDestinations removeObjectForKey:download];
    [self setStatus:@"Saving failed" state:@"error"];
    self.statusLabel.toolTip = error.localizedDescription;
}

- (void)webView:(WKWebView *)webView runOpenPanelWithParameters:(WKOpenPanelParameters *)parameters initiatedByFrame:(WKFrameInfo *)frame completionHandler:(void (^)(NSArray<NSURL *> * _Nullable URLs))completionHandler {
    NSOpenPanel *panel = [NSOpenPanel openPanel];
    panel.allowsMultipleSelection = parameters.allowsMultipleSelection;
    panel.canChooseDirectories = parameters.allowsDirectories;
    panel.canChooseFiles = YES;
    [panel beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse result) { completionHandler(result == NSModalResponseOK ? panel.URLs : nil); }];
}
- (WKWebView *)webView:(WKWebView *)webView createWebViewWithConfiguration:(WKWebViewConfiguration *)configuration forNavigationAction:(WKNavigationAction *)navigationAction windowFeatures:(WKWindowFeatures *)windowFeatures {
    // target=_blank links: viewer pages stay in this window, everything else leaves through openOutside:
    NSURL *url = navigationAction.request.URL;
    if ([self isViewerURL:url]) [webView loadRequest:navigationAction.request];
    else [self openOutside:url];
    return nil;
}
- (void)webView:(WKWebView *)webView runJavaScriptAlertPanelWithMessage:(NSString *)message initiatedByFrame:(WKFrameInfo *)frame completionHandler:(void (^)(void))completionHandler {
    NSAlert *alert = [[NSAlert alloc] init];
    alert.messageText = @"Omics Canvas";
    alert.informativeText = message;
    [alert addButtonWithTitle:@"OK"];
    [alert beginSheetModalForWindow:self.window completionHandler:^(__unused NSModalResponse result) { completionHandler(); }];
}
- (void)webView:(WKWebView *)webView runJavaScriptConfirmPanelWithMessage:(NSString *)message initiatedByFrame:(WKFrameInfo *)frame completionHandler:(void (^)(BOOL result))completionHandler {
    NSAlert *alert = [[NSAlert alloc] init];
    alert.messageText = @"Omics Canvas";
    alert.informativeText = message;
    [alert addButtonWithTitle:@"OK"];
    [alert addButtonWithTitle:@"Cancel"];
    [alert beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse result) { completionHandler(result == NSAlertFirstButtonReturn); }];
}
- (void)webView:(WKWebView *)webView runJavaScriptTextInputPanelWithPrompt:(NSString *)prompt defaultText:(NSString *)defaultText initiatedByFrame:(WKFrameInfo *)frame completionHandler:(void (^)(NSString * _Nullable result))completionHandler {
    NSAlert *alert = [[NSAlert alloc] init];
    alert.messageText = @"Omics Canvas";
    alert.informativeText = prompt;
    [alert addButtonWithTitle:@"OK"];
    [alert addButtonWithTitle:@"Cancel"];
    NSTextField *input = [NSTextField textFieldWithString:defaultText ?: @""];
    input.frame = NSMakeRect(0, 0, 320, 24);
    alert.accessoryView = input;
    [alert beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse result) { completionHandler(result == NSAlertFirstButtonReturn ? input.stringValue : nil); }];
}

@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSApplication *application = [NSApplication sharedApplication];
        application.activationPolicy = NSApplicationActivationPolicyRegular;
        TVAppDelegate *delegate = [[TVAppDelegate alloc] init];
        application.delegate = delegate;
        [application run];
    }
    return 0;
}
